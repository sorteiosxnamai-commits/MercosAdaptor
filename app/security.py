import logging
import secrets
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException

from app.config import get_settings

log = logging.getLogger(__name__)

# Chave legada (BI e agente): leitura + as escritas que já existiam. Nunca ganha as novas.
LEGACY_SCOPES = frozenset({"read", "write:customers", "write:orders", "write:titles"})
ALL_SCOPES = frozenset(
    {
        "read", "write:customers", "write:orders", "write:titles", "write:products",
        "write:stock", "write:billing", "write:order-cancel", "write:payment-methods",
    }
)


@dataclass(frozen=True)
class Principal:
    name: str
    scopes: frozenset[str]


def _match(given: str | None, expected: str) -> bool:
    return bool(expected and given and secrets.compare_digest(given, expected))


def authenticate(x_api_key: str | None) -> Principal:
    cfg = get_settings()
    if _match(x_api_key, cfg.mercos_adaptor_api_key):
        return Principal("legacy", LEGACY_SCOPES)
    erp_key = cfg.mercos_erp_api_key
    if erp_key and erp_key != cfg.mercos_adaptor_api_key and _match(x_api_key, erp_key):
        unknown = cfg.erp_scopes - ALL_SCOPES
        if unknown:
            log.warning("Escopos ERP desconhecidos ignorados: %s", sorted(unknown))
        return Principal("erp", cfg.erp_scopes & ALL_SCOPES)
    raise HTTPException(status_code=401, detail="Chave interna inválida")


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Leitura: qualquer chave válida com o escopo `read`."""
    principal = authenticate(x_api_key)
    if "read" not in principal.scopes:
        raise HTTPException(status_code=403, detail="Chave sem o escopo 'read'")


async def get_principal(x_api_key: str | None = Header(default=None)) -> Principal:
    return authenticate(x_api_key)


def require_scope(scope: str):
    async def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if scope not in principal.scopes:
            raise HTTPException(status_code=403, detail=f"Chave sem o escopo '{scope}'")
        return principal

    return dependency
