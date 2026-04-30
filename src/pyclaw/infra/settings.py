from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RedisSettings(BaseSettings):
    url: str = "redis://localhost:6379"
    key_prefix: str = "pyclaw:"

    model_config = SettingsConfigDict(env_prefix="PYCLAW_REDIS_")


class DatabaseSettings(BaseSettings):
    url: str = ""

    model_config = SettingsConfigDict(env_prefix="PYCLAW_DATABASE_")


class StorageSettings(BaseSettings):
    session_backend: str = "file"
    memory_backend: str = "sqlite"
    lock_backend: str = "file"

    model_config = SettingsConfigDict(env_prefix="PYCLAW_STORAGE_")


class AgentSettings(BaseSettings):
    default_model: str = "gpt-4o"
    max_context_tokens: int = 128000
    compaction_threshold: float = 0.8

    model_config = SettingsConfigDict(env_prefix="PYCLAW_AGENT_")


class ServerSettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = SettingsConfigDict(env_prefix="PYCLAW_SERVER_")


class Settings(BaseSettings):
    server: ServerSettings = Field(default_factory=ServerSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)

    model_config = SettingsConfigDict(env_prefix="PYCLAW_")
