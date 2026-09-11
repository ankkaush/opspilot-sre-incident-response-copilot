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

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
