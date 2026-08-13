import logging
from typing import Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Query
from fastapi.responses import JSONResponse

from app import __version__
from app.client import MercosClient
from app.config import get_settings
from app.errors import MercosError
from app.resources import READ_RESOURCES
from app.security import require_api_key

settings = get_settings()
logging.basicConfig(level=settings.log_level)
app = FastAPI(title="Mercos_Adaptor", version=__version__)


@app.exception_handler(MercosError)
async def mercos_error_handler(_, exc: MercosError):
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc), "details": exc.details})


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
    return {"resources": sorted(READ_RESOURCES)}


@app.get("/v1/{resource}", dependencies=[Depends(require_api_key)])
async def list_resource(resource: str, changed_after: str | None = Query(default=None, alias="alterado_apos")):
    if resource not in READ_RESOURCES:
        return JSONResponse(status_code=404, content={"error": "Recurso não suportado"})
    page = await MercosClient().list_page(READ_RESOURCES[resource], changed_after=changed_after)
    return {
        "resource": resource,
        "count": page["count"],
        "pageCursor": page["pageCursor"],
        "nextCursor": page["nextCursor"],
        "data": page["data"],
    }


@app.get("/v1/{resource}/{mercos_id}", dependencies=[Depends(require_api_key)])
async def get_resource(resource: str, mercos_id: str):
    if resource not in {"customers", "products", "orders"}:
        return JSONResponse(status_code=404, content={"error": "Consulta individual não suportada"})
    return await MercosClient().get_detail(READ_RESOURCES[resource], mercos_id)


@app.post("/v1/customers", dependencies=[Depends(require_api_key)])
async def create_customer(payload: dict[str, Any]):
    return await MercosClient().request("POST", "clientes", json=payload)


@app.put("/v1/customers/{mercos_id}", dependencies=[Depends(require_api_key)])
async def update_customer(mercos_id: str, payload: dict[str, Any]):
    return await MercosClient().request("PUT", f"clientes/{mercos_id}", json=payload)


@app.post("/v1/orders", dependencies=[Depends(require_api_key)])
async def create_order(payload: dict[str, Any]):
    return await MercosClient().request("POST", "pedidos", json=payload, version="v2")


@app.put("/v1/orders/{mercos_id}", dependencies=[Depends(require_api_key)])
async def update_order(mercos_id: str, payload: dict[str, Any]):
    return await MercosClient().request("PUT", f"pedidos/{mercos_id}", json=payload, version="v2")


@app.post("/v1/titles", dependencies=[Depends(require_api_key)])
async def create_title(payload: dict[str, Any]):
    return await MercosClient().request("POST", "titulos", json=payload)


@app.put("/v1/titles/{mercos_id}", dependencies=[Depends(require_api_key)])
async def update_title(mercos_id: str, payload: dict[str, Any]):
    return await MercosClient().request("PUT", f"titulos/{mercos_id}", json=payload)

