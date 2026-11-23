from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AISRE_", env_file=".env", extra="ignore")

    llm_provider: str = ""
    llm_api_key: SecretStr | None = None
    llm_model: str = "claude-sonnet-5-5"
    llm_base_url: str = ""
    llm_external_consent: bool = False
    database_url: str = ""
    prometheus_url: str = ""
    loki_url: str = ""
    slack_approvers: list[str] = Field(default_factory=list)
    tool_timeout_seconds: int = 10
    github_token: SecretStr = SecretStr("")
    github_repo: str = ""
    github_api_url: str = "https://api.github.com"
    kubernetes_enabled: bool = False
