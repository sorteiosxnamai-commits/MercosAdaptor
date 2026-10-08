"""Coordenação de cota da conta Mercos entre TODOS os consumidores (BI, ERP, agente).

A cota do Mercos é da conta, não da rota nem do consumidor. Por isso as chamadas
passam por um gate único que:

- serializa as chamadas upstream (FIFO: nenhum consumidor fura a fila);
- guarda um *deadline* compartilhado ("não antes de"): depois de um 429 e depois
  de cada chamada (pacing), a próxima chamada de qualquer consumidor espera;
- falha rápido (429 com Retry-After) quando a espera restante é longa, em vez de
  segurar a requisição HTTP por minutos.

`LocalGate` vale para uma instância. Com várias réplicas use `RedisGate`
(MERCOS_REDIS_URL): lock com lease e deadline no Redis, atômicos, e **sem
fallback permissivo**: se o coordenador cair, a chamada falha (503) em vez de
seguir sem coordenação.
"""

import asyncio
import hashlib
import logging
import time
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from app.config import Settings, get_settings
from app.errors import MercosError, MercosRateLimitError

log = logging.getLogger(__name__)
MAX_BLOCK_SECONDS = 60.0

_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class Slot:
    """Vaga obtida no gate. `cooldown` empurra o deadline compartilhado."""

    def __init__(self) -> None:
        self.cooldown_seconds = 0.0

    def cooldown(self, seconds: float) -> None:
        self.cooldown_seconds = max(self.cooldown_seconds, float(seconds), 0.0)


class LocalGate:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._not_before = 0.0

    @asynccontextmanager
    async def slot(self, max_block: float = MAX_BLOCK_SECONDS):
        await self._lock.acquire()
        slot = Slot()
        try:
            remaining = self._not_before - time.monotonic()
            if remaining > max_block:
                raise MercosRateLimitError(retry_after=remaining)
            if remaining > 0.05:
                await asyncio.sleep(remaining)
            yield slot
        finally:
            if slot.cooldown_seconds:
                self._not_before = max(self._not_before, time.monotonic() + slot.cooldown_seconds)
            self._lock.release()

    def reset(self) -> None:
        self._not_before = 0.0


class RedisGate:
    def __init__(
        self,
        client: Any,
        *,
        namespace: str,
        lease_seconds: float = 150.0,
        wait_seconds: float = 120.0,
    ) -> None:
        self._r = client
        self._lock_key = f"mercos:gate:{namespace}:lock"
        self._deadline_key = f"mercos:gate:{namespace}:not_before"
        self._lease_ms = int(lease_seconds * 1000)
        self._wait = wait_seconds

    async def _server_ms(self) -> int:
        seconds, micros = await self._r.time()
        return int(seconds) * 1000 + int(micros) // 1000

    @asynccontextmanager
    async def slot(self, max_block: float = MAX_BLOCK_SECONDS):
        token = uuid4().hex
        waited = time.monotonic()
        try:
            while not await self._r.set(self._lock_key, token, nx=True, px=self._lease_ms):
                if time.monotonic() - waited > self._wait:
                    raise MercosError(
                        "Fila de cota da conta Mercos ocupada", status_code=503,
                        details="gate_busy",
                    )
                await asyncio.sleep(0.05)
        except MercosError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed
            log.error("Coordenador de cota indisponível: %s", type(exc).__name__)
            raise MercosError(
                "Coordenador de cota indisponível", status_code=503, details="gate_unavailable"
            ) from exc
        slot = Slot()
        try:
            try:
                now = await self._server_ms()
                raw = await self._r.get(self._deadline_key)
                deadline = int(raw) if raw else 0
            except Exception as exc:  # noqa: BLE001
                raise MercosError(
                    "Coordenador de cota indisponível", status_code=503, details="gate_unavailable"
                ) from exc
            remaining = (deadline - now) / 1000
            if remaining > max_block:
                raise MercosRateLimitError(retry_after=remaining)
            if remaining > 0.05:
                await asyncio.sleep(remaining)
            yield slot
        finally:
            try:
                if slot.cooldown_seconds:
                    now = await self._server_ms()
                    raw = await self._r.get(self._deadline_key)
                    new = max(int(raw) if raw else 0, now + int(slot.cooldown_seconds * 1000))
                    await self._r.set(
                        self._deadline_key, new, px=int(slot.cooldown_seconds * 1000) + 60_000
                    )
            except Exception as exc:  # noqa: BLE001
                log.error("Falha ao gravar deadline de cota: %s", type(exc).__name__)
            try:
                await self._r.eval(_RELEASE, 1, self._lock_key, token)
            except Exception as exc:  # noqa: BLE001 - o lease expira sozinho
                log.error("Falha ao liberar o gate de cota: %s", type(exc).__name__)


_gates: dict[str, Any] = {}


def _namespace(settings: Settings) -> str:
    # Namespace por conta, sem expor o token (hash truncado, só usado como chave interna).
    return hashlib.sha256(settings.mercos_company_token.encode()).hexdigest()[:16] or "default"


def get_gate(settings: Settings | None = None):
    settings = settings or get_settings()
    key = settings.mercos_redis_url or "local"
    if key not in _gates:
        if settings.mercos_redis_url:
            import redis.asyncio as aioredis

            _gates[key] = RedisGate(
                aioredis.from_url(settings.mercos_redis_url),
                namespace=_namespace(settings),
                wait_seconds=settings.mercos_gate_wait_seconds,
            )
        else:
            _gates[key] = LocalGate()
    return _gates[key]


def set_gate(gate: Any, key: str = "local") -> None:
    _gates[key] = gate


def reset_gates() -> None:
    """Zera estado entre testes; em produção só reinicia o processo."""
    _gates.clear()
