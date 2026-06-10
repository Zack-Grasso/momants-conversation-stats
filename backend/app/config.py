from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://conversation_stats:conversation_stats@localhost:5432/conversation_stats"
    backend_cors_origins: str = "http://localhost:5173"
    sentiment_model: str = "distilbert-base-uncased-finetuned-sst-2-english"
    hf_home: str = "/app/.cache/huggingface"
    preload_models: bool = True

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
