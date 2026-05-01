from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from pyclaw.models import CompactionConfig, RetryConfig, TimeoutConfig, ToolsConfig

CONFIG_SEARCH_PATHS = [
    "pyclaw.json",
    "configs/pyclaw.json",
    "~/.openclaw/pyclaw.json",
]


class RedisSettings(BaseSettings):
    host: str = "localhost"
    port: int = 6379
    password: str | None = None
    url: str = ""
    key_prefix: str = Field("pyclaw:", alias="keyPrefix")
    transcript_retention_days: int = Field(7, alias="transcriptRetentionDays")

    model_config = SettingsConfigDict(
        env_prefix="PYCLAW_REDIS_",
        populate_by_name=True,
    )

    @property
    def ttl_seconds(self) -> int:
        return self.transcript_retention_days * 86_400

    def build_url(self) -> str:
        if self.url:
            return self.url
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}"
        return f"redis://{self.host}:{self.port}"


class DatabaseSettings(BaseSettings):
    url: str = ""

    model_config = SettingsConfigDict(env_prefix="PYCLAW_DATABASE_")


class StorageSettings(BaseSettings):
    session_backend: str = "memory"
    memory_backend: str = "sqlite"
    lock_backend: str = "file"

    model_config = SettingsConfigDict(env_prefix="PYCLAW_STORAGE_")


class ProviderSettings(BaseSettings):
    api_key: str | None = Field(default=None, alias="apiKey")
    base_url: str | None = Field(default=None, alias="baseURL")

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class AgentSettings(BaseSettings):
    default_model: str = "gpt-4o"
    max_context_tokens: int = 128000
    compaction_threshold: float = 0.8
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)
    max_iterations: int = 50
    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)

    model_config = SettingsConfigDict(env_prefix="PYCLAW_AGENT_", extra="ignore")


class ServerSettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = SettingsConfigDict(env_prefix="PYCLAW_SERVER_")


class FeishuSettings(BaseSettings):
    enabled: bool = False
    app_id: str = Field("", alias="appId")
    app_secret: str = Field("", alias="appSecret")
    session_scope: str = Field("chat", alias="sessionScope")
    group_context: str = Field("recent", alias="groupContext")
    group_context_size: int = Field(20, alias="groupContextSize")
    idle_minutes: int = Field(0, alias="idleMinutes")

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class WebSettings(BaseSettings):
    enabled: bool = False
    auth_token: str = Field("", alias="authToken")

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class ChannelsSettings(BaseSettings):
    feishu: FeishuSettings = Field(default_factory=FeishuSettings)
    web: WebSettings = Field(default_factory=WebSettings)

    model_config = SettingsConfigDict(extra="ignore")


class WorkspaceSettings(BaseSettings):
    default: str = "~/.pyclaw/workspaces"
    backend: str = Field("file", alias="backend")
    bootstrap_files: list[str] = Field(
        default_factory=lambda: ["AGENTS.md"], alias="bootstrapFiles"
    )

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class Settings(BaseSettings):
    server: ServerSettings = Field(default_factory=ServerSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    channels: ChannelsSettings = Field(default_factory=ChannelsSettings)
    workspaces: WorkspaceSettings = Field(default_factory=WorkspaceSettings)

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
