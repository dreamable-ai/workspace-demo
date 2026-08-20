"""Environment-backed configuration without a provider-specific dependency."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


@dataclass(frozen=True)
class E2BProviderSettings:
    name: str
    api_key: str
    template_id: str
    timeout_seconds: int
    domain: str | None = None

    @property
    def configured(self) -> bool:
        if not self.api_key:
            return False
        if self.name == "e2b":
            return True
        return bool(self.domain)


@dataclass(frozen=True)
class VolcengineSettings:
    mode: str
    api_key: str
    template_id: str
    timeout_seconds: int
    e2b_domain: str | None = None
    base_url: str | None = None

    @property
    def configured(self) -> bool:
        if not self.api_key:
            return False
        if self.mode == "e2b":
            return bool(self.e2b_domain)
        if self.mode == "rest":
            return bool(self.base_url)
        return False


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    api_key: str
    allow_insecure_local: bool
    database_path: Path
    sandbox_code_dir: str
    database_url: str | None = None
    workspace_storage_path: Path = Path("./data/workspaces")
    log_dir: Path = Path("./logs")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            host=os.getenv("GATEWAY_HOST", "127.0.0.1"),
            port=_int("GATEWAY_PORT", 8080),
            api_key=os.getenv("GATEWAY_API_KEY", ""),
            allow_insecure_local=_bool("GATEWAY_ALLOW_INSECURE_LOCAL", True),
            database_path=Path(
                os.getenv("GATEWAY_DATABASE_PATH", "./data/workspace-gateway.db")
            ).expanduser(),
            sandbox_code_dir=os.getenv("GATEWAY_SANDBOX_CODE_DIR", "/workspace"),
            database_url=os.getenv("GATEWAY_DATABASE_URL", "").strip() or None,
            workspace_storage_path=Path(
                os.getenv("GATEWAY_WORKSPACE_STORAGE_PATH", "./data/workspaces")
            ).expanduser(),
            log_dir=Path(os.getenv("GATEWAY_LOG_DIR", "./logs")).expanduser(),
        )

    def validate_gateway_auth(self) -> None:
        local_hosts = {"127.0.0.1", "localhost", "::1"}
        if self.api_key:
            return
        if self.allow_insecure_local and self.host in local_hosts:
            return
        raise ValueError(
            "Set GATEWAY_API_KEY unless the Gateway is explicitly limited to localhost"
        )
