from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_SEARCH_PATHS = [
    "pyclaw.json",
    "configs/pyclaw.json",
    "~/.openclaw/pyclaw.json",
]


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


def find_config_file() -> Path | None:
    for candidate in CONFIG_SEARCH_PATHS:
        path = Path(candidate).expanduser()
        if path.is_file():
            return path
    return None


def load_settings() -> Settings:
    config_file = find_config_file()
    if config_file is None:
        return Settings()
    data = json.loads(config_file.read_text(encoding="utf-8"))
    return Settings.model_validate(data)
