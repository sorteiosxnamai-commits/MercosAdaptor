import logging
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.capabilities import (
    BY_KEY,
    NOT_IMPLEMENTED,
    READ_BY_ALIAS,
    describe,
    disabled_reason,
    is_enabled,
)
from app.client import MercosClient
from app.config import get_settings
from app.errors import MercosError, MercosRateLimitError
from app.resources import READ_RESOURCES
from app.schemas import (
    ID_PATTERN,
    BillingCreate,
    BillingUpdate,
    PaymentMethod,
    ProductCreate,
    ProductUpdate,
    StockAdjust,
)
from app.security import Principal, get_principal, require_api_key, require_scope

settings = get_settings()
logging.basicConfig(level=settings.log_level)
app = FastAPI(title="Mercos_Adaptor", version=__version__)

# IDs entram em caminhos upstream: só [0-9A-Za-z_-], nunca `/`, `..` ou `%`.
MercosId = Annotated[str, Path(pattern=ID_PATTERN.pattern)]


@app.exception_handler(MercosError)
async def mercos_error_handler(_, exc: MercosError):
    headers = {}
    if isinstance(exc, MercosRateLimitError):
        headers["Retry-After"] = str(int(exc.retry_after))
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": str(exc), "details": exc.details},
        headers=headers,
    )


def ensure_enabled(key: str) -> None:
    cap = BY_KEY[key]
    if not is_enabled(cap, get_settings()):
        raise HTTPException(
            status_code=404,
            detail={"error": "Capacidade não habilitada", "capability": key, "reason": disabled_reason(cap)},
        )


@app.get("/health")
async def health():
    parsed = urlparse(settings.mercos_base_url)
    return {
        "status": "ok",
        "service": "Mercos_Adaptor",
        "version": __version__,
        "mercosConfigured": settings.configured,
        "environment": settings.environment,
        "mercosHost": parsed.netloc,
        "mercosPath": parsed.path,
    }


@app.get("/v1/resources", dependencies=[Depends(require_api_key)])
async def resources():
    # Contrato legado preservado: continua listando só os 12 recursos originais.
    return {"resources": sorted(READ_RESOURCES)}


@app.get("/v1/capabilities", dependencies=[Depends(require_api_key)])
async def capabilities(principal: Principal = Depends(get_principal)):
    """O que este build sabe fazer (aditivo). Não prova acesso da conta Mercos."""
    cfg = get_settings()
    return {
        "version": __version__,
        "principal": principal.name,
        "scopes": sorted(principal.scopes),
        "capabilities": describe(cfg),
        "notImplemented": list(NOT_IMPLEMENTED),
        "quotaCoordination": "redis" if cfg.mercos_redis_url else "local",
    }


@app.get("/v1/{resource}", dependencies=[Depends(require_api_key)])
async def list_resource(request: Request, resource: str, changed_after: str | None = Query(default=None, alias="alterado_apos")):
    cap = READ_BY_ALIAS.get(resource)
    if cap is None:
        return JSONResponse(status_code=404, content={"error": "Recurso não suportado"})
    ensure_enabled(cap.key)
    extra: dict[str, str] = {}
    for name, value in request.query_params.items():
        if name == "alterado_apos":
            continue
        pattern = cap.filters.get(name)
        if pattern is None:
            if cap.extension:  # recursos novos: nada de filtro fora da allowlist
                raise HTTPException(status_code=422, detail=f"Filtro não suportado em '{resource}': {name}")
            continue  # recursos legados ignoram, como sempre fizeram
        if not pattern.fullmatch(value):
            raise HTTPException(status_code=422, detail=f"Valor inválido para o filtro '{name}'")
        extra[name] = value
    options: dict[str, Any] = {}
    if extra:
        options["extra_params"] = extra
    if resource == "commissions":
        options["id_field"] = "comissao_id"
    # chamada idêntica à antiga quando não há filtro extra (contrato dos consumidores)
    page = await MercosClient().list_page(cap.upstream, changed_after=changed_after, **options)
    body = {
        "resource": resource,
        "count": page["count"],
        "pageCursor": page["pageCursor"],
        "nextCursor": page["nextCursor"],
        "data": page["data"],
    }
    if "lastId" in page:
        body["lastId"] = page["lastId"]
    return body


@app.get("/v1/{resource}/{mercos_id}", dependencies=[Depends(require_api_key)])
async def get_resource(resource: str, mercos_id: MercosId):
    if resource not in {"customers", "products", "orders"}:
        return JSONResponse(status_code=404, content={"error": "Consulta individual não suportada"})
    return await MercosClient().get_detail(READ_RESOURCES[resource], mercos_id)


# --- escritas legadas: mesmo contrato; a chave legada tem esses escopos --------


@app.post("/v1/customers", dependencies=[Depends(require_scope("write:customers"))])
async def create_customer(payload: dict[str, Any]):
    return await MercosClient().request("POST", "clientes", json=payload)


@app.put("/v1/customers/{mercos_id}", dependencies=[Depends(require_scope("write:customers"))])
async def update_customer(mercos_id: MercosId, payload: dict[str, Any]):
    return await MercosClient().request("PUT", f"clientes/{mercos_id}", json=payload)


@app.post("/v1/orders", dependencies=[Depends(require_scope("write:orders"))])
async def create_order(payload: dict[str, Any]):
    return await MercosClient().request("POST", "pedidos", json=payload, version="v2")


@app.put("/v1/orders/{mercos_id}", dependencies=[Depends(require_scope("write:orders"))])
async def update_order(mercos_id: MercosId, payload: dict[str, Any]):
    return await MercosClient().request("PUT", f"pedidos/{mercos_id}", json=payload, version="v2")


@app.post("/v1/titles", dependencies=[Depends(require_scope("write:titles"))])
async def create_title(payload: dict[str, Any]):
    return await MercosClient().request("POST", "titulos", json=payload)


@app.put("/v1/titles/{mercos_id}", dependencies=[Depends(require_scope("write:titles"))])
async def update_title(mercos_id: MercosId, payload: dict[str, Any]):
    return await MercosClient().request("PUT", f"titulos/{mercos_id}", json=payload)


# --- escritas novas: rotas explícitas, DTO próprio, escopo próprio -------------
# Nunca encaminham verbo/caminho arbitrário recebido do consumidor.
# POST/PUT não são repetidos: resultado desconhecido é reconciliado pelo consumidor.


def _dump(model) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=True)


@app.post("/v1/products", dependencies=[Depends(require_scope("write:products"))])
async def create_product(payload: ProductCreate):
    ensure_enabled("write.products")
    return await MercosClient().request("POST", "produtos", json=_dump(payload))


@app.put("/v1/products/{mercos_id}", dependencies=[Depends(require_scope("write:products"))])
async def update_product(mercos_id: MercosId, payload: ProductUpdate):
    ensure_enabled("write.products")
    return await MercosClient().request("PUT", f"produtos/{mercos_id}", json=_dump(payload))


@app.put("/v1/products/{mercos_id}/stock", dependencies=[Depends(require_scope("write:stock"))])
async def adjust_stock(mercos_id: MercosId, payload: StockAdjust):
    """Saldo ABSOLUTO (`novo_saldo`), um produto por vez; nunca incremento."""
    ensure_enabled("write.stock")
    if not mercos_id.isdigit():
        raise HTTPException(status_code=422, detail="produto_id deve ser numérico")
    body = {"produto_id": int(mercos_id), "novo_saldo": payload.novo_saldo}
    return await MercosClient().request("PUT", "ajustar_estoque", json=body)


@app.post("/v1/orders/{mercos_id}/cancel", dependencies=[Depends(require_scope("write:order-cancel"))])
async def cancel_order(mercos_id: MercosId):
    ensure_enabled("write.order-cancel")
    if not mercos_id.isdigit():
        raise HTTPException(status_code=422, detail="Id do pedido deve ser numérico")
    return await MercosClient().request("POST", f"pedidos/cancelar/{mercos_id}")


@app.post("/v1/billings", dependencies=[Depends(require_scope("write:billing"))])
async def create_billing(payload: BillingCreate):
    ensure_enabled("write.billing")
    return await MercosClient().request("POST", "faturamento", json=_dump(payload))


@app.put("/v1/billings", dependencies=[Depends(require_scope("write:billing"))])
async def update_billing(payload: BillingUpdate):
    """Alterar/cancelar faturamento (`excluido=true`). Identificado por `pedido_id` no corpo."""
    ensure_enabled("write.billing")
    return await MercosClient().request("PUT", "faturamento", json=_dump(payload))


@app.post("/v1/payment-methods", dependencies=[Depends(require_scope("write:payment-methods"))])
async def create_payment_method(payload: PaymentMethod):
    ensure_enabled("write.payment-methods")
    return await MercosClient().request("POST", "formas_pagamento", json=_dump(payload))


@app.put("/v1/payment-methods/{mercos_id}", dependencies=[Depends(require_scope("write:payment-methods"))])
async def update_payment_method(mercos_id: MercosId, payload: PaymentMethod):
    ensure_enabled("write.payment-methods")
    return await MercosClient().request("PUT", f"formas_pagamento/{mercos_id}", json=_dump(payload))
