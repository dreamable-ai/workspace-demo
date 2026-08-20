"""Public API and provider-neutral data models."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class ProviderName(StrEnum):
    PAI = "pai"
    E2B = "e2b"
    VOLCENGINE = "volcengine"


class SandboxState(StrEnum):
    CREATING = "creating"
    RUNNING = "running"
    PAUSED = "paused"
    TERMINATED = "terminated"
    UNKNOWN = "unknown"
    ERROR = "error"


class CreateSandboxRequest(BaseModel):
    provider: ProviderName
    template_id: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=30, le=86400)
    env: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)


class SandboxView(BaseModel):
    id: str
    provider: ProviderName
    provider_sandbox_id: str
    state: SandboxState
    template_id: str
    timeout_seconds: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    env_keys: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=100_000)
    cwd: str = "."
    timeout_seconds: float = Field(default=60, gt=0, le=3600)
    env: dict[str, str] = Field(default_factory=dict)


class CommandResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str


class StartProcessRequest(BaseModel):
    command: str = Field(min_length=1, max_length=100_000)
    cwd: str = "."
    env: dict[str, str] = Field(default_factory=dict)


class ProcessResult(BaseModel):
    pid: int | None = None


class FileWriteRequest(BaseModel):
    path: str
    text: str | None = None
    content_base64: str | None = None

    @model_validator(mode="after")
    def exactly_one_content(self) -> FileWriteRequest:
        if (self.text is None) == (self.content_base64 is None):
            raise ValueError("Exactly one of text or content_base64 is required")
        if self.content_base64 is not None:
            try:
                base64.b64decode(self.content_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("content_base64 must be valid Base64") from exc
        return self


class FileWriteResult(BaseModel):
    path: str
    bytes_written: int


class FileReadResult(BaseModel):
    path: str
    content_base64: str
    size: int


class PreviewResult(BaseModel):
    port: int
    upstream_url: str
    gateway_proxy_path: str
    note: str = "Provider credentials are retained by the Gateway and are never returned."


class PreviewAccessResult(BaseModel):
    port: int
    url: str
    upstream_url: str
    expires_at: datetime
    note: str = "Open url through the Gateway; provider credentials are not exposed."


class ProviderCapabilities(BaseModel):
    provider: ProviderName
    configured: bool
    endpoint: str | None = None
    default_template_id: str | None = None
    default_timeout_seconds: int | None = None
    create: bool = True
    connect: bool = True
    commands: bool = True
    files: bool = True
    processes: bool = True
    preview: bool = True
    pause: bool = True
    kill: bool = True
    notes: list[str] = Field(default_factory=list)


class ProviderConfigurationRequest(BaseModel):
    api_key: SecretStr | None = None
    domain: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=30, le=86400)
    mode: str | None = None
    base_url: str | None = None
    e2b_domain: str | None = None

    @field_validator("domain", "mode", "base_url", "e2b_domain")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("domain", "e2b_domain")
    @classmethod
    def valid_e2b_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        lowered = value.lower()
        if lowered.startswith(("http://", "https://")):
            raise ValueError(
                "E2B Domain must be a hostname without http:// or https://"
            )
        if lowered.startswith("api."):
            raise ValueError(
                "E2B Domain must not start with api.; the E2B SDK adds it automatically"
            )
        if any(character in value for character in "/?#"):
            raise ValueError(
                "E2B Domain must not include a path, query string, or fragment"
            )
        if any(character.isspace() for character in value):
            raise ValueError("E2B Domain must not contain whitespace")
        return value

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in {"e2b", "rest"}:
            raise ValueError("mode must be e2b or rest")
        return value


class SandboxTemplateCreateRequest(BaseModel):
    provider: ProviderName
    template_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    default_timeout_seconds: int = Field(default=900, ge=30, le=86400)
    is_default: bool = False

    @field_validator("template_id", "name", "description")
    @classmethod
    def strip_template_text(cls, value: str) -> str:
        return value.strip()


class SandboxTemplateView(BaseModel):
    id: str
    provider: ProviderName
    template_id: str
    name: str
    description: str = ""
    default_timeout_seconds: int
    is_default: bool = False
    created_at: datetime
    updated_at: datetime


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)

    @field_validator("name", "description")
    @classmethod
    def strip_workspace_text(cls, value: str) -> str:
        return value.strip()


class WorkspaceView(BaseModel):
    id: str
    name: str
    description: str = ""
    default_branch: str = "main"
    current_version: str | None = None
    dirty: bool = False
    file_count: int = 0
    created_at: datetime
    updated_at: datetime


class WorkspaceFileWriteRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    text: str | None = None
    content_base64: str | None = None

    @model_validator(mode="after")
    def exactly_one_workspace_content(self) -> WorkspaceFileWriteRequest:
        if (self.text is None) == (self.content_base64 is None):
            raise ValueError("Exactly one of text or content_base64 is required")
        if self.content_base64 is not None:
            try:
                base64.b64decode(self.content_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("content_base64 must be valid Base64") from exc
        return self


class WorkspaceFileView(BaseModel):
    path: str
    size: int
    modified_at: datetime


class WorkspaceFileReadResult(BaseModel):
    path: str
    content_base64: str
    size: int
    version: str | None = None


class WorkspaceCommitRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)


class WorkspaceVersionView(BaseModel):
    version: str
    short_version: str
    message: str
    author: str
    created_at: datetime


class WorkspaceCommitResult(BaseModel):
    version: WorkspaceVersionView
    created: bool


class WorkspaceSyncRequest(BaseModel):
    sandbox_id: str
    version: str | None = None


class WorkspaceSyncResult(BaseModel):
    workspace_id: str
    sandbox_id: str
    version: str
    file_count: int


class WorkspaceRunRequest(BaseModel):
    command: str = Field(min_length=1, max_length=100_000)
    sandbox_id: str | None = None
    version: str | None = None
    auto_commit: bool = True
    commit_message: str = Field(default="Run snapshot", min_length=1, max_length=500)
    timeout_seconds: float = Field(default=300, gt=0, le=3600)
    env: dict[str, str] = Field(default_factory=dict)


class WorkspaceRunResult(BaseModel):
    run_id: str
    workspace_id: str
    sandbox: SandboxView
    version: str
    command: str
    result: CommandResult
    created_sandbox: bool


def utc_now() -> datetime:
    return datetime.now(UTC)
