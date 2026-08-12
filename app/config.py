from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def configured(self) -> bool:
        return bool(self.mercos_application_token and self.mercos_company_token)

    @property
    def environment(self) -> str:
        return "sandbox" if "sandbox" in self.mercos_base_url.lower() else "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()

