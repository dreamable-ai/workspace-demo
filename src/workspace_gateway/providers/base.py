"""Provider contract implemented by every sandbox backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models import CommandResult, ProviderCapabilities, SandboxState


@dataclass(frozen=True)
class CreatedSandbox:
    provider_sandbox_id: str
    state: SandboxState
    template_id: str
    timeout_seconds: int


class SandboxProvider(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def create(
        self,
        *,
        template_id: str | None,
        timeout_seconds: int | None,
        env: dict[str, str],
        metadata: dict[str, str],
    ) -> CreatedSandbox: ...

    async def status(self, sandbox_id: str) -> SandboxState: ...

    async def run_command(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str,
        timeout_seconds: float,
        env: dict[str, str],
    ) -> CommandResult: ...

    async def start_process(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str,
        env: dict[str, str],
    ) -> int | None: ...

    async def write_file(self, sandbox_id: str, path: str, content: bytes) -> None: ...

    async def read_file(self, sandbox_id: str, path: str) -> bytes: ...

    async def preview_url(self, sandbox_id: str, port: int) -> str: ...

    async def preview_connection(
        self, sandbox_id: str, port: int
    ) -> tuple[str, dict[str, str]]: ...

    async def pause(self, sandbox_id: str) -> None: ...

    async def kill(self, sandbox_id: str) -> None: ...
