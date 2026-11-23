from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AISRE_", env_file=".env", extra="ignore")

    llm_provider: str = ""
    llm_api_key: str = ""
    llm_external_consent: bool = False
    database_url: str = ""
    prometheus_url: str = ""
    loki_url: str = ""
    slack_approvers: list[str] = Field(default_factory=list)
