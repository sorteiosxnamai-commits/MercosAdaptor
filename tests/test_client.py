import httpx
import pytest
from app.client import MercosClient, sanitize
from app.config import Settings
from app.errors import MercosError, MercosRateLimitError


def settings(**overrides):
    base = dict(mercos_application_token="app", mercos_company_token="company", mercos_adaptor_api_key="internal", mercos_default_retry_seconds=0, mercos_page_pause_seconds=0)
    base.update(overrides)
    return Settings(**base)


def test_sanitize_removes_secrets_recursively():
    assert sanitize({"token": "x", "nested": {"CompanyToken": "y", "ok": 1}}) == {"nested": {"ok": 1}}


def test_sanitize_redacts_customer_personal_data_recursively():
    assert sanitize({
        "cnpj": "52998224725",
        "razao_social": "Maria da Silva",
        "nested": {"telefones": [{"numero": "11999998888"}], "ok": 1},
    }) == {
        "cnpj": "[REDACTED]",
        "razao_social": "[REDACTED]",
        "nested": {"telefones": "[REDACTED]", "ok": 1},
    }


@pytest.mark.asyncio
async def test_create_preserves_meus_pedidos_id_when_body_is_empty():
    def handler(request: httpx.Request):
        assert request.method == "POST"
        assert request.url.path == "/api/v1/clientes"
        return httpx.Response(201, headers={"MeusPedidosID": "9290554"})

    client = MercosClient(settings(), transport=httpx.MockTransport(handler))

    assert await client.request("POST", "clientes", json={"tipo": "F"}) == {
        "id": 9290554,
    }


@pytest.mark.asyncio
async def test_create_adds_header_id_without_discarding_response_body():
    def handler(_request: httpx.Request):
        return httpx.Response(
            201,
            json={"status": "created"},
            headers={"MeusPedidosID": "9290554"},
        )

    client = MercosClient(settings(), transport=httpx.MockTransport(handler))

    assert await client.request("POST", "clientes", json={"tipo": "F"}) == {
        "status": "created",
        "id": 9290554,
    }


@pytest.mark.asyncio
async def test_post_transport_failure_is_never_retried():
    calls = 0

    def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("response did not arrive")

    client = MercosClient(
        settings(mercos_max_retries=5),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(MercosError) as exc_info:
        await client.request("POST", "clientes", json={"tipo": "F"})

    assert calls == 1
    assert exc_info.value.details == {
        "mutation_state": "unknown",
        "error_type": "ReadTimeout",
    }


@pytest.mark.asyncio
async def test_post_rate_limit_is_returned_without_automatic_retry():
    calls = 0

    def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"tempo_ate_permitir_novamente": 2})

    client = MercosClient(
        settings(mercos_max_retries=5),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(MercosRateLimitError):
        await client.request("POST", "clientes", json={"tipo": "F"})

    assert calls == 1


@pytest.mark.asyncio
async def test_pagination_uses_cursor_and_deduplicates():
    calls = 0
    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        assert request.url.path == "/api/v2/pedidos"
        assert request.url.params["registros_por_pagina"] == "20"
        if calls == 1:
            return httpx.Response(200, json=[{"id": 1, "ultima_alteracao": "2026-01-01T00:00:00"}], headers={"MEUSPEDIDOS_LIMITOU_REGISTROS": "1"})
        assert request.url.params["alterado_apos"] == "2026-01-01T00:00:00"
        return httpx.Response(200, json=[{"id": 2, "ultima_alteracao": "2026-01-02T00:00:00"}])
    client = MercosClient(settings(), transport=httpx.MockTransport(handler))
    rows = await client.list_all("pedidos")
    assert [r["id"] for r in rows] == [1, 2]


@pytest.mark.asyncio
async def test_retries_429():
    calls = 0
    def handler(_):
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"tempo_ate_permitir_novamente": -0.5}) if calls == 1 else httpx.Response(200, json={"ok": True})
    client = MercosClient(settings(), transport=httpx.MockTransport(handler))
    assert await client.request("GET", "clientes") == {"ok": True}
    assert calls == 2


@pytest.mark.asyncio
async def test_long_429_returns_retry_after_without_busy_waiting():
    calls = 0
    def handler(_):
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"tempo_ate_permitir_novamente": 90})

    client = MercosClient(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(MercosRateLimitError) as exc_info:
        await client.request("GET", "clientes")
    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after >= 90
    assert calls == 1


@pytest.mark.asyncio
async def test_short_429_waits_internally(monkeypatch):
    calls = 0

    def handler(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"tempo_ate_permitir_novamente": 20})
        return httpx.Response(200, json={"ok": True})

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("app.client.asyncio.sleep", fake_sleep)
    client = MercosClient(settings(), transport=httpx.MockTransport(handler))
    assert await client.request("GET", "clientes") == {"ok": True}
    assert calls == 2
    assert slept[0] == pytest.approx(20.5, abs=0.05)


@pytest.mark.asyncio
async def test_order_detail_uses_v2_and_preserves_items():
    def handler(request: httpx.Request):
        assert request.url.path == "/api/v2/pedidos/42"
        return httpx.Response(
            200,
            json={"id": 42, "itens": [{"id": 7, "produto_id": 3}]},
        )

    client = MercosClient(settings(), transport=httpx.MockTransport(handler))
    detail = await client.get_detail("pedidos", "42")

    assert detail["id"] == 42
    assert detail["itens"] == [{"id": 7, "produto_id": 3}]


@pytest.mark.asyncio
async def test_order_detail_401_is_permission_error_not_bad_gateway():
    def handler(request: httpx.Request):
        assert request.url.path == "/api/v2/pedidos/165803166"
        return httpx.Response(401, json={"mensagem": "Unauthorized"})

    client = MercosClient(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(MercosError) as exc_info:
        await client.get_detail("pedidos", "165803166")
    assert exc_info.value.status_code == 403
    assert "GET por ID" in str(exc_info.value)


@pytest.mark.asyncio
async def test_list_page_returns_last_persistable_cursor():
    def handler(request: httpx.Request):
        assert request.url.path == "/api/v2/pedidos"
        assert request.url.params["registros_por_pagina"] == "20"
        return httpx.Response(
            200,
            json=[
                {"id": 1, "ultima_alteracao": "2026-01-01T00:00:00"},
                {"id": 2, "ultima_alteracao": "2026-01-02T00:00:00"},
            ],
        )

    client = MercosClient(settings(), transport=httpx.MockTransport(handler))
    page = await client.list_page("pedidos")

    assert page["pageCursor"] == "2026-01-02T00:00:00"
    assert page["nextCursor"] is None


@pytest.mark.asyncio
async def test_nested_order_type_resource_remains_on_v1():
    def handler(request: httpx.Request):
        assert request.url.path == "/api/v1/pedidos/tipo"
        return httpx.Response(200, json=[])

    client = MercosClient(settings(), transport=httpx.MockTransport(handler))

    page = await client.list_page("pedidos/tipo")

    assert page["data"] == []

