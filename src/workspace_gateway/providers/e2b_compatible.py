"""E2B SDK adapter shared by E2B Cloud and compatible provider endpoints."""

from __future__ import annotations

import asyncio
from typing import Any

from ..config import E2BProviderSettings
from ..errors import ProviderNotConfiguredError, ProviderOperationError
from ..models import (
    CommandResult,
    ProviderCapabilities,
    ProviderName,
    SandboxState,
)
from .base import CreatedSandbox


class E2BCompatibleProvider:
    def __init__(self, settings: E2BProviderSettings, provider_name: ProviderName) -> None:
        self.settings = settings
        self.provider_name = provider_name

    @property
    def capabilities(self) -> ProviderCapabilities:
        notes: list[str] = []
        if self.provider_name == ProviderName.PAI:
            notes.append("PAI uses its E2B-compatible domain; templates are managed separately.")
            notes.append("PAI preview traffic may require a provider token retained by the Gateway.")
        return ProviderCapabilities(
            provider=self.provider_name,
            configured=self.settings.configured,
            endpoint=(
                self.settings.domain
                or ("E2B Cloud SDK default" if self.provider_name == ProviderName.E2B else None)
            ),
            default_template_id=self.settings.template_id or None,
            default_timeout_seconds=self.settings.timeout_seconds,
            notes=notes,
        )

    def _sandbox_class(self) -> Any:
        if not self.settings.configured:
            raise ProviderNotConfiguredError(
                f"Provider {self.provider_name.value!r} is not configured"
            )
        try:
            from e2b import Sandbox
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                'Install the compatible SDK with: pip install "e2b>=2.13,<2.25"'
            ) from exc
        return Sandbox

    def _connection_args(self) -> dict[str, Any]:
        args: dict[str, Any] = {"api_key": self.settings.api_key}
        if self.settings.domain:
            args["domain"] = self.settings.domain
        return args

    def _connect_sync(self, sandbox_id: str) -> Any:
        sandbox_class = self._sandbox_class()
        return sandbox_class.connect(sandbox_id, **self._connection_args())

    async def create(
        self,
        *,
        template_id: str | None,
        timeout_seconds: int | None,
        env: dict[str, str],
        metadata: dict[str, str],
    ) -> CreatedSandbox:
        sandbox_class = self._sandbox_class()
        selected_template = template_id or self.settings.template_id
        if not selected_template:
            raise ProviderNotConfiguredError(
                "A template_id from the sandbox template catalog is required"
            )
        selected_timeout = timeout_seconds or self.settings.timeout_seconds
        try:
            raw = await asyncio.to_thread(
                sandbox_class.create,
                template=selected_template,
                timeout=selected_timeout,
                envs=env or None,
                metadata=metadata or None,
                **self._connection_args(),
            )
            return CreatedSandbox(
                provider_sandbox_id=str(raw.sandbox_id),
                state=SandboxState.RUNNING,
                template_id=selected_template,
                timeout_seconds=selected_timeout,
            )
        except Exception as exc:
            raise ProviderOperationError(
                f"{self.provider_name.value} create failed: {exc}"
            ) from exc

    async def status(self, sandbox_id: str) -> SandboxState:
        try:
            raw = await asyncio.to_thread(self._connect_sync, sandbox_id)
            running = await asyncio.to_thread(raw.is_running)
            return SandboxState.RUNNING if running else SandboxState.UNKNOWN
        except Exception as exc:
            # E2B-compatible control planes report an expired or already deleted
            # sandbox as not found. This is a terminal state, not an operation
            # failure that callers should keep retrying forever.
            if type(exc).__name__ == "SandboxNotFoundException":
                return SandboxState.TERMINATED
            raise ProviderOperationError(
                f"{self.provider_name.value} status failed: {exc}"
            ) from exc

    async def run_command(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str,
        timeout_seconds: float,
        env: dict[str, str],
    ) -> CommandResult:
        try:
            raw = await asyncio.to_thread(self._connect_sync, sandbox_id)
            result = await asyncio.to_thread(
                raw.commands.run,
                command,
                cwd=cwd,
                timeout=timeout_seconds,
                envs=env or None,
            )
            return CommandResult(
                exit_code=int(result.exit_code),
                stdout=str(result.stdout or ""),
                stderr=str(result.stderr or ""),
            )
        except Exception as exc:
            raise ProviderOperationError(
                f"{self.provider_name.value} command failed: {exc}"
            ) from exc

    async def start_process(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str,
        env: dict[str, str],
    ) -> int | None:
        try:
            raw = await asyncio.to_thread(self._connect_sync, sandbox_id)
            handle = await asyncio.to_thread(
                raw.commands.run,
                command,
                cwd=cwd,
                background=True,
                envs=env or None,
            )
            pid = getattr(handle, "pid", None)
            return int(pid) if pid is not None else None
        except Exception as exc:
            raise ProviderOperationError(
                f"{self.provider_name.value} process start failed: {exc}"
            ) from exc

    async def write_file(self, sandbox_id: str, path: str, content: bytes) -> None:
        try:
            raw = await asyncio.to_thread(self._connect_sync, sandbox_id)
            await asyncio.to_thread(raw.files.write, path, content)
        except Exception as exc:
            raise ProviderOperationError(
                f"{self.provider_name.value} file write failed: {exc}"
            ) from exc

    async def read_file(self, sandbox_id: str, path: str) -> bytes:
        try:
            raw = await asyncio.to_thread(self._connect_sync, sandbox_id)
            content = await asyncio.to_thread(raw.files.read, path)
            return content.encode() if isinstance(content, str) else bytes(content)
        except Exception as exc:
            raise ProviderOperationError(
                f"{self.provider_name.value} file read failed: {exc}"
            ) from exc

    async def preview_url(self, sandbox_id: str, port: int) -> str:
        url, _ = await self.preview_connection(sandbox_id, port)
        return url

    async def preview_connection(
        self, sandbox_id: str, port: int
    ) -> tuple[str, dict[str, str]]:
        try:
            raw = await asyncio.to_thread(self._connect_sync, sandbox_id)
            host = await asyncio.to_thread(raw.get_host, port)
            headers: dict[str, str] = {}
            access_token = getattr(raw, "traffic_access_token", None) or getattr(
                raw, "_envd_access_token", None
            )
            if access_token:
                headers["X-Access-Token"] = str(access_token)
            return f"https://{host}", headers
        except Exception as exc:
            raise ProviderOperationError(
                f"{self.provider_name.value} preview failed: {exc}"
            ) from exc

    async def pause(self, sandbox_id: str) -> None:
        try:
            raw = await asyncio.to_thread(self._connect_sync, sandbox_id)
            pause = getattr(raw, "pause", None)
            if not callable(pause):
                raise TypeError("The installed E2B SDK does not expose pause()")
            await asyncio.to_thread(pause)
        except Exception as exc:
            raise ProviderOperationError(
                f"{self.provider_name.value} pause failed: {exc}"
            ) from exc

    async def kill(self, sandbox_id: str) -> None:
        try:
            sandbox_class = self._sandbox_class()
            # Use the SDK's class-level kill operation. It calls the control
            # plane directly and returns False when the sandbox no longer
            # exists. Connecting first makes an already completed deletion
            # fail before the idempotent delete request can be sent.
            await asyncio.to_thread(
                sandbox_class.kill,
                sandbox_id,
                **self._connection_args(),
            )
        except Exception as exc:
            if type(exc).__name__ == "SandboxNotFoundException":
                return
            raise ProviderOperationError(
                f"{self.provider_name.value} kill failed: {exc}"
            ) from exc
