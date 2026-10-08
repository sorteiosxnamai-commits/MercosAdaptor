import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.client import MercosClient
from app.config import get_settings
from app.main import app

LEGACY = {"X-API-Key": "legacy-key"}
ERP = {"X-API-Key": "erp-key"}


@pytest.fixture
def env(monkeypatch):
    """Configura o ambiente do Adaptor e devolve o 'Mercos' simulado."""

    def configure(**values):
        base = {
            "MERCOS_APPLICATION_TOKEN": "app", "MERCOS_COMPANY_TOKEN": "company",
            "MERCOS_ADAPTOR_API_KEY": "legacy-key", "MERCOS_ERP_API_KEY": "erp-key",
            "MERCOS_ERP_SCOPES": "read", "MERCOS_PAGE_PAUSE_SECONDS": "0",
            "MERCOS_ENABLED_EXTENSIONS": "",
        }
        base.update(values)
        for key, value in base.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    class Upstream:
        def __init__(self):
            self.requests: list[httpx.Request] = []
            self.responder = lambda request: httpx.Response(200, json=[])

        def handle(self, request):
            self.requests.append(request)
            return self.responder(request)

    upstream = Upstream()
    configure()
    monkeypatch.setattr(
        main, "MercosClient",
        lambda: MercosClient(get_settings(), transport=httpx.MockTransport(upstream.handle)),
    )
    upstream.configure = configure
    yield upstream
    get_settings.cache_clear()


def client():
    return TestClient(app)


# --- contrato legado preservado -------------------------------------------------


def test_legacy_contract_is_unchanged(env):
    c = client()
    body = c.get("/v1/resources", headers=LEGACY).json()
    assert body == {"resources": sorted([
        "customers", "products", "orders", "price-tables", "payment-conditions", "carriers",
        "commercial-policies", "categories", "segments", "order-types", "product-prices", "users",
    ])}
    env.responder = lambda r: httpx.Response(200, json=[{"id": 1, "ultima_alteracao": "2026-01-01T00:00:00"}])
    page = c.get("/v1/customers?alterado_apos=2026-01-01T00:00:00&qualquer=1", headers=LEGACY).json()
    assert set(page) == {"resource", "count", "pageCursor", "nextCursor", "data"}
    sent = dict(env.requests[-1].url.params)
    assert sent == {"alterado_apos": "2026-01-01T00:00:00"}  # parâmetro desconhecido nunca é repassado


def test_legacy_openapi_operations_are_still_there():
    ops = {(m.upper(), p) for p, v in app.openapi()["paths"].items() for m in v}
    legacy = {
        ("GET", "/health"), ("GET", "/v1/resources"), ("GET", "/v1/{resource}"),
        ("GET", "/v1/{resource}/{mercos_id}"), ("POST", "/v1/customers"), ("POST", "/v1/orders"),
        ("POST", "/v1/titles"), ("PUT", "/v1/customers/{mercos_id}"),
        ("PUT", "/v1/orders/{mercos_id}"), ("PUT", "/v1/titles/{mercos_id}"),
    }
    assert legacy <= ops


def test_legacy_key_keeps_its_writes_but_does_not_gain_new_ones(env):
    c = client()
    env.responder = lambda r: httpx.Response(201, headers={"MeusPedidosID": "9"})
    assert c.post("/v1/customers", json={"tipo": "F"}, headers=LEGACY).json() == {"id": "9"}
    assert c.post("/v1/orders", json={"cliente_id": 1}, headers=LEGACY).status_code == 200
    for method, path, body in (
        ("post", "/v1/products", {"nome": "X", "preco_tabela": 1}),
        ("post", "/v1/orders/5/cancel", None),
        ("put", "/v1/products/5/stock", {"novo_saldo": 3}),
        ("post", "/v1/billings", {"pedido_id": 1, "valor_faturado": 1, "data_faturamento": "2026-10-07"}),
    ):
        r = getattr(c, method)(path, headers=LEGACY, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, path  # a chave existente NÃO é ampliada automaticamente


# --- chave ERP e escopos --------------------------------------------------------


def test_erp_key_reads_but_cannot_write_without_explicit_scopes(env):
    c = client()
    assert c.get("/v1/customers", headers=ERP).status_code == 200
    r = c.post("/v1/customers", json={"tipo": "F"}, headers=ERP)
    assert r.status_code == 403 and "write:customers" in r.json()["detail"]
    assert c.get("/v1/customers", headers={"X-API-Key": "nope"}).status_code == 401
    assert c.get("/v1/customers").status_code == 401


def test_erp_scopes_are_exactly_what_was_configured(env):
    env.configure(MERCOS_ERP_SCOPES="read,write:customers,write:bogus")
    c = client()
    env.responder = lambda r: httpx.Response(201, headers={"MeusPedidosID": "1"})
    assert c.post("/v1/customers", json={"tipo": "F"}, headers=ERP).status_code == 200
    assert c.post("/v1/orders", json={"x": 1}, headers=ERP).status_code == 403
    caps = c.get("/v1/capabilities", headers=ERP).json()
    assert caps["principal"] == "erp" and caps["scopes"] == ["read", "write:customers"]


def test_erp_key_equal_to_legacy_key_gets_no_extra_privilege(env):
    env.configure(MERCOS_ERP_API_KEY="legacy-key", MERCOS_ERP_SCOPES="read,write:products")
    r = client().post("/v1/products", json={"nome": "X", "preco_tabela": 1}, headers=LEGACY)
    assert r.status_code == 403


def test_capabilities_never_expose_tokens_and_state_what_is_unproven(env):
    body = client().get("/v1/capabilities", headers=LEGACY).json()
    flat = json.dumps(body)
    assert "legacy-key" not in flat and "erp-key" not in flat and "company" not in flat
    by_key = {c["key"]: c for c in body["capabilities"]}
    assert by_key["read.customers"]["enabled"] is True
    assert by_key["read.titles"]["enabled"] is False and by_key["read.titles"]["extension"] is True
    assert by_key["write.products"]["enabled"] is True
    assert by_key["write.stock"]["enabled"] is False
    assert all(c["accountAccess"] == "unknown" for c in body["capabilities"])
    assert body["quotaCoordination"] == "local"
    assert any(n["key"] == "read.product-images" for n in body["notImplemented"])


# --- leituras novas: desligadas por padrão, filtros em allowlist ----------------


@pytest.mark.parametrize("alias", ["titles", "payments", "payment-methods", "promotions", "commissions"])
def test_new_reads_are_disabled_until_listed(env, alias):
    r = client().get(f"/v1/{alias}", headers=ERP)
    assert r.status_code == 404
    assert r.json()["detail"]["capability"] == f"read.{alias}"
    assert env.requests == []  # nada foi enviado ao Mercos


def test_enabled_extension_reads_the_documented_path_with_allowlisted_filters(env):
    env.configure(MERCOS_ENABLED_EXTENSIONS="read.titles,read.commissions")
    c = client()
    env.responder = lambda r: httpx.Response(200, json=[{"id": 1, "ultima_alteracao": "2026-02-01T00:00:00"}])
    page = c.get("/v1/titles?alterado_apos=2026-01-01T00:00:00", headers=ERP).json()
    assert page["resource"] == "titles" and page["pageCursor"] == "2026-02-01T00:00:00"
    assert env.requests[-1].url.path == "/api/v1/titulos"
    assert c.get("/v1/titles?evil=1", headers=ERP).status_code == 422  # sem proxy aberto

    env.responder = lambda r: httpx.Response(200, json=[{"comissao_id": 7}, {"comissao_id": 12}])
    page = c.get("/v1/commissions?ultimo_id=5&pedido_id=9", headers=ERP).json()
    assert page["lastId"] == 12
    sent = dict(env.requests[-1].url.params)
    assert sent == {"ultimo_id": "5", "pedido_id": "9"}
    assert c.get("/v1/commissions?ultimo_id=abc", headers=ERP).status_code == 422
    assert c.get("/v1/commissions?ultimo_id=1;drop", headers=ERP).status_code == 422


def test_products_accept_only_the_documented_excluido_filter(env):
    c = client()
    c.get("/v1/products?excluido=false&outro=1", headers=ERP)
    assert dict(env.requests[-1].url.params) == {"excluido": "false"}
    assert c.get("/v1/products?excluido=talvez", headers=ERP).status_code == 422


# --- escritas novas -------------------------------------------------------------


def enable_all(env):
    env.configure(
        MERCOS_ERP_SCOPES="read,write:products,write:stock,write:billing,write:order-cancel,write:payment-methods",
        MERCOS_ENABLED_EXTENSIONS="write.stock,write.billing,write.payment-methods",
    )


def test_product_create_and_update_use_confirmed_paths_and_expose_ids(env):
    enable_all(env)
    c = client()
    env.responder = lambda r: httpx.Response(
        201, json={"produtos_grade": [11, 12]}, headers={"MeusPedidosID": "55"}
    )
    r = c.post("/v1/products", json={"nome": "Camisa", "preco_tabela": 10.5, "codigo": "C1"}, headers=ERP)
    assert r.json() == {"produtos_grade": [11, 12], "id": "55"}
    req = env.requests[-1]
    assert req.method == "POST" and req.url.path == "/api/v1/produtos"
    assert json.loads(req.content) == {"nome": "Camisa", "preco_tabela": 10.5, "codigo": "C1"}
    env.responder = lambda r: httpx.Response(200, headers={"MeusPedidosID": "55"})
    c.put("/v1/products/55", json={"preco_tabela": 12}, headers=ERP)
    assert env.requests[-1].method == "PUT" and env.requests[-1].url.path == "/api/v1/produtos/55"
    assert c.put("/v1/products/55", json={}, headers=ERP).status_code == 422
    assert c.post("/v1/products", json={"nome": "", "preco_tabela": 1}, headers=ERP).status_code == 422
    assert c.post("/v1/products", json={"nome": "x"}, headers=ERP).status_code == 422


def test_stock_adjustment_is_absolute_and_validated(env):
    enable_all(env)
    c = client()
    env.responder = lambda r: httpx.Response(200)
    assert c.put("/v1/products/77/stock", json={"novo_saldo": 12.5}, headers=ERP).status_code == 200
    req = env.requests[-1]
    assert req.method == "PUT" and req.url.path == "/api/v1/ajustar_estoque"
    assert json.loads(req.content) == {"produto_id": 77, "novo_saldo": 12.5}
    for bad in ({"novo_saldo": -1}, {"novo_saldo": 99999999}, {"delta": 3}, {"novo_saldo": "abc"}):
        assert c.put("/v1/products/77/stock", json=bad, headers=ERP).status_code == 422
    assert c.put("/v1/products/ab/stock", json={"novo_saldo": 1}, headers=ERP).status_code == 422


def test_order_cancel_uses_the_dedicated_operation_not_delete(env):
    enable_all(env)
    c = client()
    env.responder = lambda r: httpx.Response(200)
    assert c.post("/v1/orders/165/cancel", headers=ERP).status_code == 200
    req = env.requests[-1]
    assert req.method == "POST" and req.url.path == "/api/v1/pedidos/cancelar/165"
    assert c.delete("/v1/orders/165", headers=ERP).status_code == 405
    env.responder = lambda r: httpx.Response(412, json={"mensagem": "pedido inexistente"})
    r = c.post("/v1/orders/999/cancel", headers=ERP)
    assert r.status_code == 412  # erro do provedor chega legível, não vira 502


def test_billing_requires_documented_fields_and_has_no_get(env):
    enable_all(env)
    c = client()
    env.responder = lambda r: httpx.Response(201, headers={"MeusPedidosID": "3"})
    ok = {"pedido_id": 10, "valor_faturado": 99.9, "data_faturamento": "2026-10-07", "numero_nf": "123"}
    assert c.post("/v1/billings", json=ok, headers=ERP).json() == {"id": "3"}
    assert env.requests[-1].url.path == "/api/v1/faturamento"
    c.put("/v1/billings", json={"pedido_id": 10, "excluido": True}, headers=ERP)
    assert env.requests[-1].method == "PUT"
    assert json.loads(env.requests[-1].content) == {"pedido_id": 10, "excluido": True}
    assert c.post("/v1/billings", json={"pedido_id": 10}, headers=ERP).status_code == 422
    assert c.post("/v1/billings", json={**ok, "data_faturamento": "07/10/2026"}, headers=ERP).status_code == 422
    assert c.get("/v1/billings", headers=ERP).status_code == 404  # não inventamos GET


def test_disabled_extension_writes_never_reach_mercos(env):
    env.configure(MERCOS_ERP_SCOPES="read,write:stock,write:billing,write:payment-methods")
    c = client()
    assert c.put("/v1/products/1/stock", json={"novo_saldo": 1}, headers=ERP).status_code == 404
    assert c.post("/v1/payment-methods", json={"nome": "Pix"}, headers=ERP).status_code == 404
    assert env.requests == []


def test_new_writes_are_never_retried_after_timeout_or_429(env):
    enable_all(env)
    c = client()

    def boom(request):
        raise httpx.ReadTimeout("t", request=request)

    env.responder = boom
    assert c.post("/v1/products", json={"nome": "X", "preco_tabela": 1}, headers=ERP).status_code == 502
    assert len(env.requests) == 1
    env.requests.clear()
    from app.quota import reset_gates

    reset_gates()
    env.responder = lambda r: httpx.Response(429, headers={"Retry-After": "5"})
    r = c.put("/v1/products/3/stock", json={"novo_saldo": 1}, headers=ERP)
    assert r.status_code == 429 and r.headers["Retry-After"]
    assert len(env.requests) == 1


# --- IDs e caminhos ---------------------------------------------------------------


@pytest.mark.parametrize("bad", ["a%2Fb", "a b", "x;y", "..%2F..", "a" * 41])
def test_ids_with_path_characters_are_rejected_before_any_upstream_call(env, bad):
    c = client()
    assert c.get(f"/v1/orders/{bad}", headers=LEGACY).status_code in (404, 422)
    assert c.put(f"/v1/customers/{bad}", json={"a": 1}, headers=LEGACY).status_code in (404, 422)
    assert env.requests == []


def test_provider_permission_error_names_the_real_operation(env):
    enable_all(env)
    env.responder = lambda r: httpx.Response(403, json={"mensagem": "sem permissão"})
    r = client().post("/v1/products", json={"nome": "X", "preco_tabela": 1}, headers=ERP)
    assert r.status_code == 403
    msg = r.json()["error"]
    assert "POST" in msg and "403" in msg and "GET por ID" not in msg  # não culpa "detalhe por ID"
