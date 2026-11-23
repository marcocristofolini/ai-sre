from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PredictWorkload(BaseModel):
    """Workload monitorado pelo preditivo: `name` = "namespace/workload"; `query` = PromQL que
    devolve UMA série (use agregação), usada no backfill (query_range) e na leitura atual."""

    name: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")
    query: str = Field(min_length=1)


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
    memory_top_k: int = Field(default=3, ge=0)  # incidentes anteriores dados ao agente; 0 desativa
    github_token: SecretStr = SecretStr("")
    github_repo: str = ""
    github_api_url: str = "https://api.github.com"
    kubernetes_enabled: bool = False
    dedupe_window_seconds: int = 300
    investigation_timeout_seconds: int = 300
    max_concurrent_investigations: int = 3
    max_webhook_body_bytes: int = 1_000_000
    max_alerts_per_request: int = 100  # alertas *firing* investigados por requisição; o excesso é descartado (202 com `truncated`)
    max_pending_investigations: int = Field(default=50, ge=1)  # fila cheia => 503 (o Alertmanager reenvia)
    # Webhook do Alertmanager: exige `Authorization: Bearer <token>`. Sem token, falha fechado (503),
    # a menos que webhook_allow_unauthenticated=true seja definido explicitamente (só desenvolvimento).
    webhook_token: SecretStr = SecretStr("")
    webhook_allow_unauthenticated: bool = False
    risk_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    min_baseline_points: int = Field(default=500, ge=1)
    scoreboard_path: str = "eval/scoreboard.json"
    web_dist_dir: str = ""  # vazio = <repositório>/web/dist (servido só se existir)
    # Login local do dashboard. Sem padrões: hash/segredo vazios => login falha fechado.
    admin_user: str = "admin"
    admin_password_hash: SecretStr = SecretStr("")  # scrypt$n$r$p$salt$hash (python -m aisre.auth hash)
    session_secret: SecretStr = SecretStr("")
    session_cookie_secure: bool = True  # só desligue em desenvolvimento local (http)
    session_ttl_seconds: int = Field(default=8 * 3600, ge=60)
    # Preditivo: vazio => agendador não inicia.
    predict_workloads: list[PredictWorkload] = Field(default_factory=list)
    predict_interval_seconds: float = Field(default=60.0, gt=0)
    predict_backfill_seconds: int = Field(default=7 * 86400, ge=1)
    predict_backfill_step_seconds: int = Field(default=60, ge=1)
    predict_retry_initial_seconds: float = Field(default=30.0, gt=0)  # 1a espera antes de refazer o backfill
    predict_retry_max_seconds: float = Field(default=900.0, gt=0)
    # Detector ativo é reajustado (backfill da janela recente) a cada intervalo: depois de uma mudança
    # de patamar permanente ele aprende o novo normal em vez de abrir investigação a cada cooldown.
    predict_refit_interval_seconds: float = Field(default=86400.0, gt=0)

    @field_validator("predict_workloads")
    @classmethod
    def _unique_workloads(cls, v: list[PredictWorkload]) -> list[PredictWorkload]:
        names = [w.name for w in v]
        if len(set(names)) != len(names):
            raise ValueError("predict_workloads com nomes repetidos")
        return v
