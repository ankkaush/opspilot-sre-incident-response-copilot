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

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
