"""DTOs das rotas de escrita novas. Rotas legadas seguem aceitando `dict`."""

import re
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]{1,40}$")


def validate_id(value: str) -> str:
    """IDs entram em paths: nada de `/`, `..`, `?` ou `%`."""
    if not ID_PATTERN.fullmatch(value):
        raise ValueError("Identificador inválido")
    return value


class Loose(BaseModel):
    """Campos conhecidos validados; os demais (documentados pelo Mercos) passam adiante."""

    model_config = ConfigDict(extra="allow")


class ProductCreate(Loose):
    nome: str = Field(min_length=1, max_length=100)
    preco_tabela: float = Field(ge=0)


class ProductUpdate(Loose):
    @model_validator(mode="after")
    def not_empty(self):
        if not (self.model_extra or {}):
            raise ValueError("Informe ao menos um campo a alterar")
        return self


class StockAdjust(BaseModel):
    model_config = ConfigDict(extra="forbid")
    novo_saldo: float = Field(ge=0, le=9999999.99)


class BillingBase(Loose):
    pedido_id: int = Field(gt=0)
    valor_faturado: float | None = Field(default=None, ge=0, le=9999999.99)
    data_faturamento: date | None = None
    numero_nf: str | None = Field(default=None, max_length=500)
    informacoes_adicionais: str | None = Field(default=None, max_length=5000)
    itens_faturados: list[dict[str, Any]] | None = None


class BillingCreate(BillingBase):
    valor_faturado: float = Field(ge=0, le=9999999.99)
    data_faturamento: date


class BillingUpdate(BillingBase):
    excluido: bool | None = None


class PaymentMethod(Loose):
    nome: str = Field(min_length=1, max_length=100)
    excluido: bool | None = None

    @field_validator("nome")
    @classmethod
    def strip(cls, value: str) -> str:
        return value.strip()
