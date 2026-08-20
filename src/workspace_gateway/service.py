"""Gateway orchestration independent from HTTP and provider SDKs."""

from __future__ import annotations

import base64
import uuid
from pathlib import PurePosixPath

from .errors import SandboxNotFoundError, WorkspacePathError
from .models import (
    CommandRequest,
    CommandResult,
    CreateSandboxRequest,
    FileReadResult,
    FileWriteRequest,
    FileWriteResult,
    PreviewResult,
    ProcessResult,
    SandboxState,
    SandboxView,
    StartProcessRequest,
    utc_now,
)
from .registry import ProviderRegistry
from .storage import SandboxRecord, SandboxStore


class SandboxGatewayService:
    def __init__(
        self,
        registry: ProviderRegistry,
        store: SandboxStore,
        sandbox_code_dir: str = "/workspace",
    ) -> None:
        self.registry = registry
        self.store = store
        self.sandbox_code_dir = PurePosixPath(sandbox_code_dir)
        if not self.sandbox_code_dir.is_absolute() or ".." in self.sandbox_code_dir.parts:
            raise ValueError("sandbox_code_dir must be an absolute Sandbox path")

    def _record(self, gateway_id: str) -> SandboxRecord:
        record = self.store.get(gateway_id)
        if record is None:
            raise SandboxNotFoundError(f"Sandbox {gateway_id!r} was not found")
        return record

    def _workspace_path(self, raw_path: str) -> str:
        path = PurePosixPath(raw_path)
        if not path.is_absolute():
            path = self.sandbox_code_dir / path
        if ".." in path.parts:
            raise WorkspacePathError("Remote paths cannot contain '..'")
        if path != self.sandbox_code_dir and self.sandbox_code_dir not in path.parents:
            raise WorkspacePathError(
                f"Remote paths must stay under {self.sandbox_code_dir}"
            )
        return str(path)

    async def create(self, request: CreateSandboxRequest) -> SandboxView:
        provider = self.registry.get(request.provider)
        created = await provider.create(
            template_id=request.template_id,
            timeout_seconds=request.timeout_seconds,
            env=request.env,
            metadata=request.metadata,
        )
        now = utc_now()
        record = SandboxRecord(
            id=f"sbxgw_{uuid.uuid4().hex}",
            provider=request.provider,
            provider_sandbox_id=created.provider_sandbox_id,
            state=created.state,
            template_id=created.template_id,
            created_at=now,
            updated_at=now,
            timeout_seconds=created.timeout_seconds,
            metadata=dict(request.metadata),
            env_keys=sorted(request.env),
        )
        self.store.insert(record)
        return record.as_view()

    def list(self, limit: int = 100) -> list[SandboxView]:
        return [record.as_view() for record in self.store.list(limit)]

    async def get(self, gateway_id: str, refresh: bool = False) -> SandboxView:
        record = self._record(gateway_id)
        if refresh and record.state != SandboxState.TERMINATED:
            state = await self.registry.get(record.provider).status(record.provider_sandbox_id)
            record = self.store.update_state(gateway_id, state) or record
        return record.as_view()

    async def run_command(self, gateway_id: str, request: CommandRequest) -> CommandResult:
        record = self._record(gateway_id)
        return await self.registry.get(record.provider).run_command(
            record.provider_sandbox_id,
            request.command,
            cwd=self._workspace_path(request.cwd),
            timeout_seconds=request.timeout_seconds,
            env=request.env,
        )

    async def start_process(
        self, gateway_id: str, request: StartProcessRequest
    ) -> ProcessResult:
        record = self._record(gateway_id)
        pid = await self.registry.get(record.provider).start_process(
            record.provider_sandbox_id,
            request.command,
            cwd=self._workspace_path(request.cwd),
            env=request.env,
        )
        return ProcessResult(pid=pid)

    async def write_file(
        self, gateway_id: str, request: FileWriteRequest
    ) -> FileWriteResult:
        record = self._record(gateway_id)
        path = self._workspace_path(request.path)
        if request.text is not None:
            content = request.text.encode()
        else:
            assert request.content_base64 is not None
            content = base64.b64decode(request.content_base64, validate=True)
        await self.registry.get(record.provider).write_file(
            record.provider_sandbox_id, path, content
        )
        return FileWriteResult(path=path, bytes_written=len(content))

    async def read_file(self, gateway_id: str, path: str) -> FileReadResult:
        record = self._record(gateway_id)
        resolved = self._workspace_path(path)
        content = await self.registry.get(record.provider).read_file(
            record.provider_sandbox_id, resolved
        )
        return FileReadResult(
            path=resolved,
            content_base64=base64.b64encode(content).decode(),
            size=len(content),
        )

    async def preview(self, gateway_id: str, port: int) -> PreviewResult:
        url, _ = await self.preview_connection(gateway_id, port)
        return PreviewResult(
            port=port,
            upstream_url=url,
            gateway_proxy_path=f"/v1/sandboxes/{gateway_id}/proxy/{port}/",
        )

    async def preview_connection(
        self, gateway_id: str, port: int
    ) -> tuple[str, dict[str, str]]:
        record = self._record(gateway_id)
        return await self.registry.get(record.provider).preview_connection(
            record.provider_sandbox_id,
            port,
        )

    async def pause(self, gateway_id: str) -> SandboxView:
        record = self._record(gateway_id)
        await self.registry.get(record.provider).pause(record.provider_sandbox_id)
        updated = self.store.update_state(gateway_id, SandboxState.PAUSED)
        return (updated or record).as_view()

    async def kill(self, gateway_id: str) -> SandboxView:
        record = self._record(gateway_id)
        if record.state != SandboxState.TERMINATED:
            await self.registry.get(record.provider).kill(record.provider_sandbox_id)
        updated = self.store.update_state(gateway_id, SandboxState.TERMINATED)
        return (updated or record).as_view()
