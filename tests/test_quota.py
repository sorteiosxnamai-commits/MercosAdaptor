import asyncio

import httpx
import pytest
from fakeredis import FakeAsyncRedis, FakeServer

from app import quota
from app.client import MercosClient
from app.config import Settings
from app.errors import MercosError, MercosRateLimitError
from app.quota import LocalGate, RedisGate, set_gate


def settings(**overrides):
    base = dict(
        mercos_application_token="app", mercos_company_token="company",
        mercos_adaptor_api_key="internal", mercos_default_retry_seconds=0,
        mercos_page_pause_seconds=0,
    )
    base.update(overrides)
    return Settings(**base)


class Spy:
    """Transporte que mede quantas chamadas upstream estão em voo ao mesmo tempo."""

    def __init__(self, responder=None, hold=0.0):
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.responder = responder or (lambda n: httpx.Response(200, json=[]))
        self.hold = hold
        self.transport = httpx.MockTransport(self.handle)

    async def handle(self, request):
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.hold:
                await asyncio.sleep(self.hold)
            return self.responder(self.calls)
        finally:
            self.in_flight -= 1


@pytest.mark.asyncio
async def test_calls_are_serialized_across_consumers():
    spy = Spy(hold=0.02)
    clients = [MercosClient(settings(), transport=spy.transport) for _ in range(6)]
    await asyncio.gather(*(c.list_page("clientes") for c in clients))
    assert spy.calls == 6 and spy.max_in_flight == 1


@pytest.mark.asyncio
async def test_429_returned_to_one_consumer_blocks_the_next_one_without_calling_mercos():
    spy = Spy(lambda n: httpx.Response(429, json={"tempo_ate_permitir_novamente": 90}))
    bi = MercosClient(settings(), transport=spy.transport)
    erp = MercosClient(settings(), transport=spy.transport)
    with pytest.raises(MercosRateLimitError):
        await bi.list_page("pedidos")
    assert spy.calls == 1
    # o ERP (outro consumidor, mesma conta) é barrado ANTES do deadline, sem gastar cota
    with pytest.raises(MercosRateLimitError) as err:
        await erp.list_page("clientes")
    assert spy.calls == 1
    assert 80 <= err.value.retry_after <= 91


@pytest.mark.asyncio
async def test_short_remaining_cooldown_is_waited_inside_the_gate(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(quota.asyncio, "sleep", fake_sleep)
    gate = LocalGate()
    set_gate(gate)
    async with gate.slot() as slot:
        slot.cooldown(10)
    async with gate.slot():
        pass
    assert slept and 9 <= slept[0] <= 10


@pytest.mark.asyncio
async def test_pacing_between_calls_applies_to_every_consumer(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(quota.asyncio, "sleep", fake_sleep)
    spy = Spy()
    a = MercosClient(settings(mercos_page_pause_seconds=2.0), transport=spy.transport)
    b = MercosClient(settings(mercos_page_pause_seconds=2.0), transport=spy.transport)
    await a.list_page("clientes")
    await b.list_page("produtos")
    assert any(1.5 <= s <= 2.0 for s in slept)  # a 2ª chamada esperou o pacing da 1ª


@pytest.mark.asyncio
async def test_post_429_also_sets_the_account_deadline():
    spy = Spy(lambda n: httpx.Response(429, headers={"Retry-After": "100"}))
    client = MercosClient(settings(), transport=spy.transport)
    with pytest.raises(MercosRateLimitError):
        await client.request("POST", "clientes", json={"tipo": "F"})
    with pytest.raises(MercosRateLimitError):
        await client.request("GET", "clientes")
    assert spy.calls == 1


def redis_gate(server, **kw):
    return RedisGate(FakeAsyncRedis(server=server), namespace="acct", **kw)


@pytest.mark.asyncio
async def test_redis_gate_serializes_two_independent_processes():
    server = FakeServer()
    spy = Spy(hold=0.02)
    proc_a, proc_b = redis_gate(server), redis_gate(server)  # "dois processos", um Redis

    clients = []
    for gate in (proc_a, proc_b, proc_a, proc_b):
        client = MercosClient(settings(), transport=spy.transport)
        client._gate = lambda g=gate: g  # noqa: SLF001
        clients.append(client)
    await asyncio.gather(*(c.list_page("clientes") for c in clients))
    assert spy.calls == 4 and spy.max_in_flight == 1


@pytest.mark.asyncio
async def test_redis_deadline_is_shared_between_processes():
    server = FakeServer()
    spy = Spy(lambda n: httpx.Response(429, json={"tempo_ate_permitir_novamente": 90}))
    a, b = redis_gate(server), redis_gate(server)
    ca = MercosClient(settings(), transport=spy.transport)
    cb = MercosClient(settings(), transport=spy.transport)
    ca._gate = lambda: a  # noqa: SLF001
    cb._gate = lambda: b  # noqa: SLF001
    with pytest.raises(MercosRateLimitError):
        await ca.list_page("clientes")
    with pytest.raises(MercosRateLimitError) as err:
        await cb.list_page("pedidos")  # outro processo, mesma conta
    assert spy.calls == 1 and err.value.retry_after > 80


@pytest.mark.asyncio
async def test_redis_failure_fails_closed_without_calling_mercos():
    class Broken:
        async def set(self, *a, **k):
            raise ConnectionError("redis fora")

    spy = Spy()
    client = MercosClient(settings(), transport=spy.transport)
    client._gate = lambda: RedisGate(Broken(), namespace="x")  # noqa: SLF001
    with pytest.raises(MercosError) as err:
        await client.list_page("clientes")
    assert err.value.status_code == 503 and err.value.details == "gate_unavailable"
    assert spy.calls == 0  # sem fallback permissivo


@pytest.mark.asyncio
async def test_redis_lock_is_released_after_an_error_and_lease_expires():
    server = FakeServer()
    gate = redis_gate(server, lease_seconds=0.2, wait_seconds=2)
    with pytest.raises(RuntimeError):
        async with gate.slot():
            raise RuntimeError("falha no meio da chamada")
    async with gate.slot():  # liberou: não trava
        pass
    # processo morto segurando o lock: o lease expira e outro segue
    other = FakeAsyncRedis(server=server)
    await other.set("mercos:gate:acct:lock", "morto", nx=True, px=200)
    async with gate.slot():
        pass


@pytest.mark.asyncio
async def test_redis_busy_gate_times_out_instead_of_hanging_forever():
    server = FakeServer()
    other = FakeAsyncRedis(server=server)
    await other.set("mercos:gate:acct:lock", "ocupado", nx=True, px=60_000)
    gate = redis_gate(server, wait_seconds=0.2)
    with pytest.raises(MercosError) as err:
        async with gate.slot():
            pass
    assert err.value.status_code == 503 and err.value.details == "gate_busy"


def test_gate_selection_uses_redis_only_when_configured():
    assert isinstance(quota.get_gate(settings()), LocalGate)
