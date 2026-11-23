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
    slack_bot_token: SecretStr | None = None
    slack_signing_secret: SecretStr | None = None
    slack_channel: str = ""
    slack_api_url: str = "https://slack.com/api"
    tool_timeout_seconds: int = 10
    github_token: SecretStr = SecretStr("")
    github_repo: str = ""
    github_api_url: str = "https://api.github.com"
    kubernetes_enabled: bool = False
    dedupe_window_seconds: int = 300
    investigation_timeout_seconds: int = 300
    max_concurrent_investigations: int = 3
    max_webhook_body_bytes: int = 1_000_000
    max_alerts_per_request: int = 100
    risk_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    min_baseline_points: int = Field(default=500, ge=1)
