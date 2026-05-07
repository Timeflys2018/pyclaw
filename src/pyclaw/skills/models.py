from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SkillRequirements(BaseModel):
    bins: list[str] = Field(default_factory=list)
    any_bins: list[str] = Field(default_factory=list)
    env: list[str] = Field(default_factory=list)
    os: list[str] = Field(default_factory=list)


class InstallSpec(BaseModel):
    kind: Literal["brew", "node", "uv", "go", "download"]
    formula: str | None = None
    package: str | None = None
    module: str | None = None
    url: str | None = None
    bins: list[str] = Field(default_factory=list)
    os_filter: list[str] = Field(default_factory=list)
    label: str | None = None


class SkillManifest(BaseModel):
    name: str
    description: str = ""
    body: str = ""
    file_path: str = ""
    requirements: SkillRequirements = Field(default_factory=SkillRequirements)
    install_specs: list[InstallSpec] = Field(default_factory=list)
    always: bool = False
    emoji: str | None = None
    disable_model_invocation: bool = False
    # Auto-generated skill metadata (from SOP graduation)
    auto_generated: bool = False
    lifecycle: str = "active"
    generated_at: str | None = None
    source_session: str | None = None


class SkillParseError(Exception):
    file_path: str
    message: str

    def __init__(self, message: str, file_path: str = "") -> None:
        self.file_path = file_path
        self.message = message
        super().__init__(f"{file_path}: {message}" if file_path else message)


class ClawHubError(Exception):
    status_code: int
    message: str

    def __init__(self, message: str, status_code: int = 0) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"[{status_code}] {message}" if status_code else message)


class SkillInstallError(Exception):
    message: str

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
