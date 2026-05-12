from __future__ import annotations

import json
from pathlib import Path

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pyclaw.models import (
    CompactionConfig,
    PromptBudgetConfig,
    RetryConfig,
    TimeoutConfig,
    ToolsConfig,
)

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


class MemorySettings(BaseSettings):
    base_dir: str = "~/.pyclaw/memory"
    l1_max_entries: int = 30
    l1_max_chars: int = 3000
    l1_ttl_seconds: int = 2_592_000  # 30 days
    search_l2_quota: int = 3
    search_l3_quota: int = 2
    search_fts_min_query_chars: int = 3
    archive_max_results: int = 5
    archive_min_similarity: float = 0.5
    archive_min_results: int = 1
    archive_enabled: bool = True

    model_config = SettingsConfigDict(env_prefix="PYCLAW_MEMORY_")


class CuratorSettings(BaseSettings):
    """Curator 生命周期管理配置。"""

    enabled: bool = True
    check_interval_seconds: int = Field(3600, alias="checkIntervalSeconds")
    interval_seconds: int = Field(604800, alias="intervalSeconds")
    stale_after_days: int = Field(30, alias="staleAfterDays")
    archive_after_days: int = Field(90, alias="archiveAfterDays")

    graduation_enabled: bool = Field(True, alias="graduationEnabled")
    graduation_mode: str = Field("template", alias="graduationMode")
    graduation_model: str | None = Field(None, alias="graduationModel")
    promotion_min_use_count: int = Field(5, alias="promotionMinUseCount")
    promotion_min_days: int = Field(7, alias="promotionMinDays")

    llm_review_enabled: bool = Field(False, alias="llmReviewEnabled")
    llm_review_model: str | None = Field(None, alias="llmReviewModel")
    llm_review_interval_seconds: int = Field(1209600, alias="llmReviewIntervalSeconds")
    llm_review_actions: list[str] = Field(default_factory=lambda: ["promote"], alias="llmReviewActions")
    llm_review_max_batch: int = Field(20, alias="llmReviewMaxBatch")

    model_config = SettingsConfigDict(
        env_prefix="PYCLAW_CURATOR_",
        populate_by_name=True,
        extra="ignore",
    )


class EvolutionSettings(BaseSettings):
    """Self-evolution SOP extraction configuration."""

    enabled: bool = True
    extraction_model: str | None = None
    max_candidates: int = 100
    min_tool_calls_for_extraction: int = Field(2, alias="minToolCallsForExtraction")
    dedup_overlap_threshold: float = Field(0.6, alias="dedupOverlapThreshold")
    max_sops_per_extraction: int = Field(5, alias="maxSopsPerExtraction")
    description_max_chars: int = Field(150, alias="descriptionMaxChars")
    procedure_max_chars: int = Field(5000, alias="procedureMaxChars")
    curator: CuratorSettings = Field(default_factory=CuratorSettings)

    model_config = SettingsConfigDict(
        env_prefix="PYCLAW_EVOLUTION_",
        populate_by_name=True,
        extra="ignore",
    )


class EmbeddingSettings(BaseSettings):
    model: str = ""
    api_key: str = Field("", alias="apiKey")
    base_url: str = Field("", alias="baseURL")
    dimensions: int = 4096

    model_config = SettingsConfigDict(
        env_prefix="PYCLAW_EMBEDDING_",
        populate_by_name=True,
    )


class ModelModalities(BaseModel):
    input: set[str] = Field(default_factory=lambda: {"text"})
    output: set[str] = Field(default_factory=lambda: {"text"})

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class ModelEntry(BaseModel):
    modalities: ModelModalities = Field(default_factory=ModelModalities)

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class ProviderSettings(BaseSettings):
    api_key: str | None = Field(default=None, alias="apiKey")
    base_url: str | None = Field(default=None, alias="baseURL")
    models: dict[str, ModelEntry] = Field(default_factory=dict)
    prefixes: list[str] = Field(default_factory=list)
    litellm_provider: str | None = Field(default=None, alias="litellmProvider")

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")

    @field_validator("models", mode="before")
    @classmethod
    def _reject_legacy_list_form(cls, value):
        if isinstance(value, list):
            raise ValueError(
                'ProviderSettings.models must be a dict {model_id: ModelEntry}, got list. '
                'Migration: change "models": [...] to '
                '"models": {model_id: {"modalities": {"input": [...], "output": [...]}}}. '
                "See configs/pyclaw.example.json."
            )
        return value


class AgentSettings(BaseSettings):
    default_model: str = "gpt-4o"
    max_context_tokens: int = 128000
    compaction_threshold: float = 0.8
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)
    default_provider: str | None = None
    unknown_prefix_policy: Literal["fail", "default"] = "fail"
    max_iterations: int = 50
    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    prompt_budget: PromptBudgetConfig = Field(
        default_factory=PromptBudgetConfig, alias="promptBudget"
    )

    model_config = SettingsConfigDict(
        env_prefix="PYCLAW_AGENT_", extra="ignore", populate_by_name=True
    )


class ServerSettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = SettingsConfigDict(env_prefix="PYCLAW_SERVER_")


class FeishuStreamingConfig(BaseModel):
    """Streaming card configuration — passed directly to Feishu CardKit API."""
    print_frequency_ms: int = Field(50, alias="printFrequencyMs")
    print_step: int = Field(2, alias="printStep")
    print_strategy: str = Field("fast", alias="printStrategy")
    summary: str = Field("", alias="summary")
    throttle_ms: int = Field(100, alias="throttleMs")

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class FeishuSettings(BaseSettings):
    enabled: bool = False
    app_id: str = Field("", alias="appId")
    app_secret: str = Field("", alias="appSecret")
    session_scope: str = Field("chat", alias="sessionScope")
    group_context: str = Field("recent", alias="groupContext")
    group_context_size: int = Field(20, alias="groupContextSize")
    idle_minutes: int = Field(0, alias="idleMinutes")
    streaming: FeishuStreamingConfig = Field(default_factory=FeishuStreamingConfig)

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class WebUserConfig(BaseModel):
    id: str
    password: str


class WebSettings(BaseSettings):
    enabled: bool = False
    auth_token: str = Field("", alias="authToken")
    jwt_secret: str = Field("change-me-in-production", alias="jwtSecret")
    admin_token: str = Field("", alias="adminToken")
    heartbeat_interval: int = Field(30, alias="heartbeatInterval")
    pong_timeout: int = Field(10, alias="pongTimeout")
    max_connections_per_user: int = Field(3, alias="maxConnectionsPerUser")
    buffer_ttl_seconds: int = Field(300, alias="bufferTtlSeconds")
    buffer_max_entries: int = Field(1000, alias="bufferMaxEntries")
    tools_requiring_approval: list[str] = Field(
        default_factory=lambda: ["bash", "write"], alias="toolsRequiringApproval"
    )
    allowed_tools: list[str] = Field(
        default_factory=lambda: ["read"], alias="allowedTools"
    )
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173"], alias="corsOrigins"
    )
    users: list[WebUserConfig] = Field(default_factory=list)

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


class SkillSettings(BaseSettings):
    workspace_skills_dir: str = "skills"
    project_agents_skills_dir: str = ".agents/skills"
    managed_skills_dir: str = "~/.openclaw/skills"
    personal_agents_skills_dir: str = "~/.agents/skills"
    bundled_skills_dir: str | None = None
    clawhub_base_url: str = "https://clawhub.ai"
    max_skills_in_prompt: int = 150
    max_skills_prompt_chars: int = 18000
    max_skill_file_bytes: int = 256_000
    max_candidates_per_root: int = 300
    max_skills_loaded_per_source: int = 200
    progressive_disclosure: bool = Field(
        default=True,
        alias="progressiveDisclosure",
    )

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class Settings(BaseSettings):
    server: ServerSettings = Field(default_factory=ServerSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    channels: ChannelsSettings = Field(default_factory=ChannelsSettings)
    workspaces: WorkspaceSettings = Field(default_factory=WorkspaceSettings)
    skills: SkillSettings = Field(default_factory=SkillSettings)
    evolution: EvolutionSettings = Field(default_factory=EvolutionSettings)
    admin_user_ids: list[str] = Field(default_factory=list)
    # Graceful shutdown timeout in seconds.  Matches the default K8s
    # SIGTERM→SIGKILL window (30 s) so that TaskManager drain completes
    # before the orchestrator force-kills the process.
    shutdown_grace_seconds: int = Field(
        30, alias="shutdownGraceSeconds"
    )

    model_config = SettingsConfigDict(env_prefix="PYCLAW_")

    @model_validator(mode="before")
    @classmethod
    def _flatten_admin(cls, data):
        if isinstance(data, dict) and "admin" in data and isinstance(data["admin"], dict):
            admin_block = data.pop("admin")
            user_ids = admin_block.get("userIds") or admin_block.get("user_ids") or []
            if "admin_user_ids" not in data:
                data["admin_user_ids"] = list(user_ids)
        return data


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
