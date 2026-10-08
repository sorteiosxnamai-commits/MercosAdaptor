"""Registro explícito do que este build do Adaptor sabe falar com o Mercos.

Cada linha: alias, caminho upstream, versão, métodos, filtros em allowlist, escopo
exigido e a EVIDÊNCIA oficial. Separa "suportado por este build" de "liberado
nesta conta": o acesso real da conta só se confirma chamando (a sonda do ERP
registra 200/403); aqui nunca se afirma acesso.

Extensões cujo caminho/paginação/filtros ainda não foram confirmados no sandbox
vêm DESLIGADAS e só ligam listadas em MERCOS_ENABLED_EXTENSIONS.
"""

import re
from dataclasses import dataclass, field

from app.config import Settings

DOCS = "https://docs.mercos.com/reference/"

_BOOL = re.compile(r"^(true|false|0|1)$")
_INT = re.compile(r"^\d{1,18}$")


@dataclass(frozen=True)
class Capability:
    key: str
    kind: str  # read | write
    alias: str
    upstream: str
    version: str
    methods: tuple[str, ...]
    scope: str
    evidence: str
    default_enabled: bool = True
    extension: bool = False
    filters: dict[str, re.Pattern] = field(default_factory=dict)
    note: str = ""
    legacy: bool = False


def _read(alias, upstream, version="v1", *, evidence="", enabled=True, ext=False, filters=None, note=""):
    return Capability(
        key=f"read.{alias}", kind="read", alias=alias, upstream=upstream, version=version,
        methods=("GET",), scope="read", evidence=evidence, default_enabled=enabled, extension=ext,
        filters=filters or {}, note=note, legacy=not ext,
    )


CAPABILITIES: tuple[Capability, ...] = (
    # --- leitura legada (contrato preservado) ---
    _read("customers", "clientes", evidence=DOCS + "v1clientes"),
    _read("products", "produtos", evidence=DOCS + "v1produtos", filters={"excluido": _BOOL}),
    _read("orders", "pedidos", "v2", evidence=DOCS + "v2pedidos"),
    _read("price-tables", "tabelas_preco", evidence=DOCS + "v1tabelas_preco"),
    _read("payment-conditions", "condicoes_pagamento", evidence=DOCS + "v1condicoes_pagamento"),
    _read("carriers", "transportadoras", evidence=DOCS + "v1transportadoras"),
    _read("commercial-policies", "politicas_comerciais", evidence=DOCS + "v1politicas_comerciais"),
    _read("categories", "categorias", evidence=DOCS + "v1produtos"),
    _read("segments", "segmentos", evidence=DOCS + "v1clientes"),
    _read("order-types", "pedidos/tipo", evidence=DOCS + "v2pedidos"),
    _read("product-prices", "produtos_tabela_preco", evidence=DOCS + "v1produtos_tabela_preco"),
    _read("users", "usuarios", evidence=DOCS + "v1usuarios"),
    # --- leitura nova: desligada até validar filtros/paginação no sandbox ---
    _read("titles", "titulos", evidence=DOCS + "v1titulos (GET /v1/titulos confirmado; filtros e paginação não)",
          enabled=False, ext=True,
          note="Fatura do cliente no Mercos; não é o financeiro da empresa."),
    _read("payments", "pagamentos", evidence=DOCS + "v1pagamentos (caminho e paginação não confirmados)",
          enabled=False, ext=True, note="Mercos Pay; campo `token` tem semântica de negócio."),
    _read("payment-methods", "formas_pagamento", evidence=DOCS + "v1formas_pagamento (caminho não confirmado)",
          enabled=False, ext=True, note="Separado de condições de pagamento."),
    _read("promotions", "promocoes", evidence=DOCS + "v1promocoes (caminho não confirmado)",
          enabled=False, ext=True, note="Alterar uma promoção pode devolver novo ID."),
    _read("commissions", "comissoes", evidence=DOCS + "v1comissoes (caminho não confirmado)",
          enabled=False, ext=True,
          filters={"colaborador_id": _INT, "pedido_id": _INT, "ultimo_id": _INT},
          note="Paginação própria por `ultimo_id`; identificador `comissao_id`; devolve `lastId`."),
    # --- escrita legada (contrato preservado; chave legada continua valendo) ---
    Capability("write.customers", "write", "customers", "clientes", "v1", ("POST", "PUT"),
               "write:customers", DOCS + "v1clientes", legacy=True),
    Capability("write.orders", "write", "orders", "pedidos", "v2", ("POST", "PUT"),
               "write:orders", DOCS + "v2pedidos", legacy=True),
    Capability("write.titles", "write", "titles", "titulos", "v1", ("POST", "PUT"),
               "write:titles", DOCS + "v1titulos", legacy=True),
    # --- escrita nova ---
    Capability("write.products", "write", "products", "produtos", "v1", ("POST", "PUT"),
               "write:products", DOCS + "v1produtos (POST /v1/produtos e PUT /v1/produtos/{id} confirmados)",
               extension=True, note="Grades devolvem os IDs em `produtos_grade`."),
    Capability("write.order-cancel", "write", "orders", "pedidos/cancelar/{id}", "v1", ("POST",),
               "write:order-cancel", DOCS + "cancelar-um-pedido (POST /v1/pedidos/cancelar/{id} confirmado)",
               extension=True, note="Operação dedicada; não existe DELETE de pedido."),
    Capability("write.stock", "write", "products", "ajustar_estoque", "v1", ("PUT",), "write:stock",
               DOCS + "v1ajustar_estoque (PUT, saldo absoluto, 1 produto; caminho não confirmado)",
               default_enabled=False, extension=True, note="`novo_saldo` é absoluto, nunca incremento."),
    Capability("write.billing", "write", "billings", "faturamento", "v1", ("POST", "PUT"),
               "write:billing", DOCS + "v1faturamento (métodos confirmados; caminho não)",
               default_enabled=False, extension=True,
               note="Sem GET documentado. Cancelar = PUT com `excluido=true`."),
    Capability("write.payment-methods", "write", "payment-methods", "formas_pagamento", "v1",
               ("POST", "PUT"), "write:payment-methods",
               DOCS + "v1formas_pagamento (métodos confirmados; caminho não)",
               default_enabled=False, extension=True),
)

BY_KEY = {c.key: c for c in CAPABILITIES}
READ_BY_ALIAS = {c.alias: c for c in CAPABILITIES if c.kind == "read"}

NOT_IMPLEMENTED = (
    {
        "key": "read.product-images",
        "reason": "A leitura devolve um objeto por produto com hashes SHA-512 (formato diferente do "
        "envelope de lista); precisa de rota própria depois de validar no sandbox. Upload não exclui imagens.",
        "evidence": DOCS + "v1imagens_produto",
    },
    {
        "key": "write.price-tables|payment-conditions|carriers|categories|segments|prices",
        "reason": "Métodos de escrita desses cadastros não foram confirmados na documentação consultada.",
        "evidence": "",
    },
    {
        "key": "read.titles-by-document",
        "reason": "Não existe busca por CPF/CNPJ; consumidores mantêm índice incremental próprio.",
        "evidence": "",
    },
)


def is_enabled(cap: Capability, settings: Settings) -> bool:
    return cap.default_enabled or cap.key in settings.enabled_extensions


def disabled_reason(cap: Capability) -> str:
    return (
        f"Extensão '{cap.key}' desabilitada neste build: {cap.evidence}. "
        "Habilite em MERCOS_ENABLED_EXTENSIONS depois de validar no sandbox."
    )


def describe(settings: Settings) -> list[dict]:
    return [
        {
            "key": c.key,
            "kind": c.kind,
            "alias": c.alias,
            "upstream": f"{c.version}/{c.upstream}",
            "methods": list(c.methods),
            "scope": c.scope,
            "filters": ["alterado_apos", *c.filters] if c.kind == "read" else [],
            "supportedByBuild": True,
            "enabled": is_enabled(c, settings),
            "extension": c.extension,
            "evidence": c.evidence or None,
            "note": c.note or None,
            "accountAccess": "unknown",  # só a chamada real prova; este build não afirma
        }
        for c in CAPABILITIES
    ]
