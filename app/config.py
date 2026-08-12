from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_mercos_base_url(value: str) -> str:
    url = (value or "").strip().strip('"').strip("'").rstrip("/")
    if not url:
        return "https://sandbox.mercos.com/api/v1"

    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    scheme = parsed.scheme or "https"

    # api.mercos.com is NOT the integration API — it serves storefront HTML 404s
    if host == "api.mercos.com":
        host = "app.mercos.com"

    # Common mistake: storefront subdomain (loja.mercos.com) instead of API host
    if host.endswith(".mercos.com") and host not in {"sandbox.mercos.com", "app.mercos.com"}:
        raise ValueError(
            "MERCOS_BASE_URL parece URL de loja (vitrine). Use "
            "https://sandbox.mercos.com/api/v1 ou https://app.mercos.com/api/v1"
        )

    if host in {"sandbox.mercos.com", "app.mercos.com"} and path in {"", "/api"}:
        return f"{scheme}://{host}/api/v1"
    if "/api/v" not in path:
        raise ValueError(
            "MERCOS_BASE_URL deve incluir /api/v1 (ex.: https://app.mercos.com/api/v1)"
        )
    return f"{scheme}://{host}{path}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mercos_base_url: str = "https://sandbox.mercos.com/api/v1"
    mercos_application_token: str = ""
    mercos_company_token: str = ""
    mercos_adaptor_api_key: str = ""
    mercos_timeout_seconds: float = Field(default=60, gt=0)
    mercos_max_retries: int = Field(default=4, ge=1, le=10)
    mercos_default_retry_seconds: float = Field(default=6, ge=0)
    mercos_max_pages: int = Field(default=500, ge=1)
    mercos_page_pause_seconds: float = Field(default=0.25, ge=0)
    mercos_verify_ssl: bool = True
    log_level: str = "INFO"

    @field_validator("mercos_base_url", mode="before")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return normalize_mercos_base_url(value)

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @property
    def configured(self) -> bool:
        return bool(self.mercos_application_token and self.mercos_company_token)

    @property
    def environment(self) -> str:
        return "sandbox" if "sandbox" in self.mercos_base_url.lower() else "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
