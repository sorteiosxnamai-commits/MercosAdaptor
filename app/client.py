import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any
import httpx

from app.config import Settings, get_settings
from app.errors import MercosConfigurationError, MercosError, MercosRateLimitError

logger = logging.getLogger(__name__)
SENSITIVE = {"applicationtoken", "companytoken", "authorization", "token", "password", "secret", "apikey", "api_key"}


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items() if k.lower().replace("-", "").replace("_", "") not in SENSITIVE}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


class MercosClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings or get_settings()
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        if not self.settings.configured:
            raise MercosConfigurationError()
        return {
            "ApplicationToken": self.settings.mercos_application_token,
            "CompanyToken": self.settings.mercos_company_token,
            "Accept": "application/json",
            "User-Agent": "Mercos_Adaptor/1.0",
        }

    def _url(self, path: str, *, version: str = "v1") -> str:
        base = self.settings.mercos_base_url.rstrip("/")
        if version == "v2" and base.endswith("/v1"):
            base = base[:-2] + "v2"
        return f"{base}/{path.lstrip('/')}"

    @staticmethod
    def _version_for_resource(resource: str) -> str:
        return "v2" if resource == "pedidos" else "v1"

    @staticmethod
    def _retry_after(response: httpx.Response, default: float) -> float:
        try:
            payload = response.json()
            if isinstance(payload, dict) and payload.get("tempo_ate_permitir_novamente") is not None:
                return float(payload["tempo_ate_permitir_novamente"]) + 0.5
        except (ValueError, TypeError):
            pass
        header = response.headers.get("Retry-After")
        try:
            return float(header) if header else default
        except ValueError:
            return default

    async def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None, version: str = "v1") -> Any:
        async with httpx.AsyncClient(
            headers=self._headers(), timeout=self.settings.mercos_timeout_seconds,
            verify=self.settings.mercos_verify_ssl, transport=self._transport,
        ) as client:
            last: httpx.Response | None = None
            for attempt in range(self.settings.mercos_max_retries):
                try:
                    response = await client.request(method, self._url(path, version=version), params=params, json=json)
                except httpx.RequestError as exc:
                    if attempt + 1 == self.settings.mercos_max_retries:
                        raise MercosError("Falha de comunicação com a Mercos", details=type(exc).__name__) from exc
                    await asyncio.sleep(min(2 ** attempt, 8))
                    continue
                last = response
                if response.status_code != 429:
                    break
                if attempt + 1 < self.settings.mercos_max_retries:
                    await asyncio.sleep(self._retry_after(response, self.settings.mercos_default_retry_seconds))
            if last is None:
                raise MercosError("Mercos não respondeu")
            if last.status_code == 429:
                raise MercosRateLimitError()
            if last.is_error:
                try:
                    details = sanitize(last.json())
                except ValueError:
                    details = last.text[:500]
                mapped = last.status_code if 400 <= last.status_code < 500 and last.status_code not in (401, 403) else 502
                raise MercosError("A Mercos rejeitou a requisição", status_code=mapped, details=details)
            if last.status_code == 204 or not last.content:
                return None
            return last.json()

    async def iter_changed(self, resource: str, *, changed_after: str | None = None) -> AsyncIterator[dict]:
        cursor = changed_after
        seen: set[str] = set()
        version = self._version_for_resource(resource)
        for page in range(self.settings.mercos_max_pages):
            params = {"alterado_apos": cursor} if cursor else {}
            async with httpx.AsyncClient(headers=self._headers(), timeout=self.settings.mercos_timeout_seconds, verify=self.settings.mercos_verify_ssl, transport=self._transport) as client:
                response = await self._paged_request(
                    client,
                    resource,
                    params,
                    version=version,
                )
            data = response.json()
            if isinstance(data, dict):
                raise MercosError("Resposta paginada inesperada", details=sanitize(data))
            if not isinstance(data, list) or not data:
                return
            for row in data:
                key = str(row.get("id", "")) + ":" + str(row.get("ultima_alteracao", ""))
                if key not in seen:
                    seen.add(key)
                    yield row
            limited = response.headers.get("MEUSPEDIDOS_LIMITOU_REGISTROS") == "1"
            if not limited:
                return
            next_cursor = next((str(x.get("ultima_alteracao")) for x in reversed(data) if x.get("ultima_alteracao")), None)
            if not next_cursor or next_cursor == cursor:
                raise MercosError("Paginação interrompida: cursor não avançou")
            cursor = next_cursor
            if page + 1 < self.settings.mercos_max_pages:
                await asyncio.sleep(self.settings.mercos_page_pause_seconds)
        raise MercosError("Limite máximo de páginas atingido")

    async def _paged_request(
        self, client: httpx.AsyncClient, resource: str, params: dict, *, version: str = "v1"
    ) -> httpx.Response:
        last = None
        for attempt in range(self.settings.mercos_max_retries):
            last = await client.get(self._url(resource, version=version), params=params)
            if last.status_code != 429:
                break
            if attempt + 1 < self.settings.mercos_max_retries:
                await asyncio.sleep(self._retry_after(last, self.settings.mercos_default_retry_seconds))
        if last is None or last.status_code == 429:
            raise MercosRateLimitError()
        if last.is_error:
            try:
                details = sanitize(last.json())
            except ValueError:
                text = last.text[:500]
                if "Loja não encontrada" in text or "loja publicada" in text.lower():
                    raise MercosError(
                        "URL Mercos inválida (resposta de vitrine). "
                        "Confira MERCOS_BASE_URL: use https://app.mercos.com/api/v1 "
                        "(produção) ou https://sandbox.mercos.com/api/v1 (sandbox).",
                        status_code=502,
                        details={"hint": "loja_nao_encontrada", "status": last.status_code},
                    )
                details = text
            if last.status_code in (401, 403):
                raise MercosError(
                    f"Sem permissão Mercos para '{resource}'. "
                    "No painel Mercos (Integração), libere o recurso para o ApplicationToken "
                    "ou conclua a homologação de produção.",
                    status_code=403,
                    details=details,
                )
            mapped = (
                last.status_code
                if 400 <= last.status_code < 500
                else 502
            )
            logger.warning("Mercos page error %s %s: %s", last.status_code, resource, details)
            raise MercosError("Falha ao paginar recurso Mercos", status_code=mapped, details=details)
        return last

    async def list_page(self, resource: str, *, changed_after: str | None = None) -> dict[str, Any]:
        """Fetch a single Mercos page. nextCursor is set only when more pages exist."""
        params = {"alterado_apos": changed_after} if changed_after else {}
        # Pedidos só na API v2 — não tenta v1
        version = self._version_for_resource(resource)
        async with httpx.AsyncClient(
            headers=self._headers(),
            timeout=self.settings.mercos_timeout_seconds,
            verify=self.settings.mercos_verify_ssl,
            transport=self._transport,
        ) as client:
            response = await self._paged_request(client, resource, params, version=version)
        data = response.json()
        if isinstance(data, dict):
            raise MercosError("Resposta paginada inesperada", details=sanitize(data))
        if not isinstance(data, list):
            raise MercosError("Resposta paginada inesperada", details=type(data).__name__)
        limited = response.headers.get("MEUSPEDIDOS_LIMITOU_REGISTROS") == "1"
        page_cursor = next(
            (str(x.get("ultima_alteracao")) for x in reversed(data) if x.get("ultima_alteracao")),
            None,
        )
        next_cursor = None
        if limited and data:
            next_cursor = page_cursor
            if not next_cursor or next_cursor == changed_after:
                raise MercosError("Paginação interrompida: cursor não avançou")
        return {
            "data": data,
            "pageCursor": page_cursor,
            "nextCursor": next_cursor,
            "count": len(data),
        }

    async def get_detail(self, resource: str, mercos_id: str) -> Any:
        """Fetch a resource detail using the same API version as its list endpoint."""
        version = self._version_for_resource(resource)
        return await self.request(
            "GET",
            f"{resource}/{mercos_id}",
            version=version,
        )

    async def list_all(self, resource: str, *, changed_after: str | None = None) -> list[dict]:
        return [row async for row in self.iter_changed(resource, changed_after=changed_after)]

