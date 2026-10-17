"""Environment-driven settings. Every value has a name in .env.example."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # FalkorDB
    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    falkordb_username: str | None = None
    falkordb_password: str | None = None
    falkordb_tls: bool = False

    # Summon API
    summon_api_url: str = "http://localhost:8787"
    summon_api_token: str = "change-me"
    summon_voice: Literal["mock", "sandbox", "shravan"] = "mock"
    summon_api_port: int = 8787
    summon_max_calls_per_hour: int = 6

    # Developer
    summon_developer_id: str = "dev"
    summon_developer_name: str = "Developer"
    summon_developer_phone: str = ""

    # Curator LLM (OpenAI-compatible)
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_model: str = "openai/gpt-oss-120b"

    # Shravan.ai (voice provider)
    shravan_base_url: str = "https://api.shravan-ai.tech/v1"
    shravan_api_key: str = ""
    shravan_agent_id: str = ""
    shravan_webhook_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
