from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment variables / .env.

    Never hardcode secrets here — this class only reads them.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+psycopg://opspilot:opspilot@localhost:5432/opspilot"
    api_key: str = "change-me-to-a-real-secret"
    cors_origins: str = "http://localhost:3000"
    log_level: str = "info"
    env: str = "dev"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    agent_max_steps: int = 8
    agent_max_cost_usd: float = 0.20

    rate_limit_max_requests: int = 100
    rate_limit_window_seconds: int = 60
    max_request_body_bytes: int = 64 * 1024

    # v0.2 Phase 3: if nobody approves or denies a paused incident within
    # this many seconds, it's auto-escalated rather than left hanging
    # forever. Checked lazily on read (no background scheduler — consistent
    # with the "no queues/Redis" scope decision), so the exact moment of
    # escalation is "whenever the incident is next fetched," not a precise
    # timer tick.
    approval_sla_seconds: int = 3600

    # v0.3 Phase 3: Langfuse tracing. Observability only — the Langfuse
    # client itself no-ops gracefully when public_key/secret_key are unset,
    # so leaving these blank never changes agent behavior, only visibility.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"

    # v0.4 Phase 1: the write-policy confidence floor. A confirmed diagnosis
    # below this never becomes a service_memory row — see
    # opspilot.memory.write_confirmed_memory. Deliberately code-enforced,
    # not a prompt instruction the model could ignore or talk around.
    memory_write_min_confidence: float = 0.6

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
