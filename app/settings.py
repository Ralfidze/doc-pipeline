from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings from environment variables. An empty value is the same as "not set"."""

    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore")

    redis_url: str = "redis://redis:6379/0"
    gemini_api_key: str = ""
    gemini_model_id: str = "gemini-3.1-flash-lite"
    openai_api_key: str = ""
    gemini_test_mode: str = ""

    @field_validator("gemini_model_id")
    @classmethod
    def _strip_model_id(cls, value: str) -> str:
        return value.strip() or "gemini-3.1-flash-lite"


@lru_cache
def get_settings() -> Settings:
    return Settings()
