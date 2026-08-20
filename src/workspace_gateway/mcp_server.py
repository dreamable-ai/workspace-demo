"""MCP tools backed directly by the provider-neutral gateway service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp import MCPError
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .errors import GatewayError, ProviderOperationError
from .models import (
    CommandRequest,
    StartProcessRequest,
    WorkspaceRunRequest,
)
from .service import SandboxGatewayService

if TYPE_CHECKING:
    from .workspace_service import WorkspaceService

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
CREATE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
EXECUTE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=True,
)
WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=True,
)


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _raise_mcp_error(exc: GatewayError) -> None:
    message = (
        "The sandbox provider operation failed"
        if isinstance(exc, ProviderOperationError)
        else str(exc)
    )
    raise MCPError(-32000, message) from None


def create_mcp_server(
    service: SandboxGatewayService,
    workspace_service: WorkspaceService,
) -> MCPServer:
    """Create the MCP surface while retaining provider credentials in the Gateway."""

    mcp = MCPServer(
        name="workspace-gateway",
        title="Workspace Gateway",
        description=(
            "Persistent coding Workspace tools with provider-neutral Sandbox execution."
        ),
        instructions=(
            "Use persistent Workspace tools for every project source-code read and write. "
            "Commit a version, then use workspace_run to create or reuse a remote Sandbox, "
            "copy that exact version, and execute it. Sandbox tools are runtime-only and "
            "must not be used as project source storage. Provider credentials are never exposed."
        ),
        version="0.1.0",
    )

    @mcp.tool(
        name="sandbox_get",
        description="Get one sandbox by Gateway ID, optionally refreshing provider state.",
        annotations=READ_ONLY,
    )
    async def get_sandbox(gateway_id: str, refresh: bool = False) -> dict[str, Any]:
        try:
            result = await service.get(gateway_id, refresh)
        except GatewayError as exc:
            _raise_mcp_error(exc)
        return _dump(result)

    @mcp.tool(
        name="sandbox_run_command",
        description=(
            "Run a command synchronously inside a sandbox workspace and return exit code, "
            "stdout, and stderr. Commands do not run on the Gateway host."
        ),
        annotations=EXECUTE,
    )
    async def run_command(
        gateway_id: str,
        command: str,
        cwd: str = ".",
        timeout_seconds: Annotated[float, Field(gt=0, le=3600)] = 60,
        env: dict[str, str] | None = None,
        max_output_chars: Annotated[int, Field(ge=1_000, le=200_000)] = 50_000,
    ) -> dict[str, Any]:
        try:
            result = await service.run_command(
                gateway_id,
                CommandRequest(
                    command=command,
                    cwd=cwd,
                    timeout_seconds=timeout_seconds,
                    env=env or {},
                ),
            )
        except GatewayError as exc:
            _raise_mcp_error(exc)
        stdout = result.stdout[:max_output_chars]
        stderr = result.stderr[:max_output_chars]
        return {
            "exit_code": result.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": len(stdout) < len(result.stdout),
            "stderr_truncated": len(stderr) < len(result.stderr),
        }

    @mcp.tool(
        name="sandbox_start_process",
        description=(
            "Start a background process inside a sandbox, for example a Next.js dev server."
        ),
        annotations=EXECUTE,
    )
    async def start_process(
        gateway_id: str,
        command: str,
        cwd: str = ".",
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            result = await service.start_process(
                gateway_id,
                StartProcessRequest(command=command, cwd=cwd, env=env or {}),
            )
        except GatewayError as exc:
            _raise_mcp_error(exc)
        return _dump(result)

    @mcp.tool(
        name="sandbox_get_preview",
        description="Get the externally reachable preview URL for a sandbox TCP port.",
        annotations=READ_ONLY,
    )
    async def get_preview(
        gateway_id: str,
        port: Annotated[int, Field(ge=1, le=65535)],
    ) -> dict[str, Any]:
        try:
            result = await service.preview(gateway_id, port)
        except GatewayError as exc:
            _raise_mcp_error(exc)
        return _dump(result)

    @mcp.tool(
        name="sandbox_pause",
        description="Pause a sandbox while retaining its recoverable provider state.",
        annotations=WRITE,
    )
    async def pause_sandbox(gateway_id: str) -> dict[str, Any]:
        try:
            result = await service.pause(gateway_id)
        except GatewayError as exc:
            _raise_mcp_error(exc)
        return _dump(result)

    @mcp.tool(
        name="sandbox_kill",
        description=(
            "Permanently terminate a sandbox. Set confirm=true explicitly; unpersisted "
            "workspace data may be lost."
        ),
        annotations=DESTRUCTIVE,
    )
    async def kill_sandbox(gateway_id: str, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise MCPError(-32003, "Set confirm=true to terminate the sandbox")
        try:
            result = await service.kill(gateway_id)
        except GatewayError as exc:
            _raise_mcp_error(exc)
        return _dump(result)

    if workspace_service is not None:

        @mcp.tool(
            name="workspace_create",
            description=(
                "Create a durable Git-backed project Workspace. Source code written here "
                "survives Sandbox termination."
            ),
            annotations=CREATE,
        )
        async def create_workspace(name: str, description: str = "") -> dict[str, Any]:
            try:
                result = workspace_service.create(name.strip(), description.strip())
            except GatewayError as exc:
                _raise_mcp_error(exc)
            return _dump(result)

        @mcp.tool(
            name="workspace_get",
            description=(
                "Get Workspace metadata, current Git version, dirty state, and file count."
            ),
            annotations=READ_ONLY,
        )
        async def get_workspace(workspace_id: str) -> dict[str, Any]:
            try:
                result = workspace_service.get(workspace_id)
            except GatewayError as exc:
                _raise_mcp_error(exc)
            return _dump(result)

        @mcp.tool(
            name="workspace_list_files",
            description="List source files in a persistent Workspace working tree.",
            annotations=READ_ONLY,
        )
        async def list_workspace_files(workspace_id: str) -> list[dict[str, Any]]:
            try:
                result = workspace_service.list_files(workspace_id)
            except GatewayError as exc:
                _raise_mcp_error(exc)
            return [_dump(item) for item in result]

        @mcp.tool(
            name="workspace_read_file",
            description=(
                "Read a Workspace file from the editable working tree or from a specific "
                "committed version."
            ),
            annotations=READ_ONLY,
        )
        async def read_workspace_file(
            workspace_id: str,
            path: str,
            version: str | None = None,
            encoding: Literal["utf-8", "base64"] = "utf-8",
            max_bytes: Annotated[int, Field(ge=1, le=5_000_000)] = 1_000_000,
        ) -> dict[str, Any]:
            try:
                result = workspace_service.read_file(workspace_id, path, version)
            except GatewayError as exc:
                _raise_mcp_error(exc)
            if result.size > max_bytes:
                raise MCPError(
                    -32001,
                    f"File is {result.size} bytes, above max_bytes={max_bytes}",
                )
            if encoding == "base64":
                content = result.content_base64
            else:
                import base64

                try:
                    content = base64.b64decode(result.content_base64).decode("utf-8")
                except UnicodeDecodeError:
                    raise MCPError(
                        -32002,
                        "File is not valid UTF-8; read it again with encoding='base64'",
                    ) from None
            return {
                "path": result.path,
                "version": result.version,
                "encoding": encoding,
                "content": content,
                "size": result.size,
            }

        @mcp.tool(
            name="workspace_write_file",
            description=(
                "Create or overwrite a project file in a durable Workspace working tree. "
                "Commit it before using a fixed version."
            ),
            annotations=WRITE,
        )
        async def write_workspace_file(
            workspace_id: str,
            path: str,
            # Keep the annotation exactly ``str``. The MCP SDK otherwise treats
            # ``str | None`` as a structured type and JSON-decodes package.json text.
            text: str = None,  # noqa: RUF013
            content_base64: str = None,  # noqa: RUF013
        ) -> dict[str, Any]:
            if (text is None) == (content_base64 is None):
                raise MCPError(-32602, "Provide exactly one of text or content_base64")
            try:
                result = workspace_service.write_file(
                    workspace_id,
                    path,
                    text=text,
                    content_base64=content_base64,
                )
            except GatewayError as exc:
                _raise_mcp_error(exc)
            return _dump(result)

        @mcp.tool(
            name="workspace_commit",
            description=(
                "Create a Git commit from all pending Workspace changes and return the "
                "immutable version hash."
            ),
            annotations=WRITE,
        )
        async def commit_workspace(workspace_id: str, message: str) -> dict[str, Any]:
            try:
                result = workspace_service.commit(workspace_id, message)
            except GatewayError as exc:
                _raise_mcp_error(exc)
            return _dump(result)

        @mcp.tool(
            name="workspace_history",
            description="List recent committed versions for a Workspace.",
            annotations=READ_ONLY,
        )
        async def workspace_history(
            workspace_id: str,
            limit: Annotated[int, Field(ge=1, le=100)] = 20,
        ) -> list[dict[str, Any]]:
            try:
                result = workspace_service.history(workspace_id, limit)
            except GatewayError as exc:
                _raise_mcp_error(exc)
            return [_dump(item) for item in result]

        @mcp.tool(
            name="workspace_run",
            description=(
                "Run project code reproducibly: optionally commit pending changes, create "
                "or reuse a Sandbox, copy the selected Workspace version into /workspace, "
                "then execute the command. Returns the Sandbox ID and command result."
            ),
            annotations=EXECUTE,
        )
        async def run_workspace(
            workspace_id: str,
            command: str,
            sandbox_id: str | None = None,
            version: str | None = None,
            auto_commit: bool = True,
            commit_message: str = "Run snapshot",
            timeout_seconds: Annotated[float, Field(gt=0, le=3600)] = 300,
            env: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            try:
                result = await workspace_service.run(
                    workspace_id,
                    WorkspaceRunRequest(
                        command=command,
                        sandbox_id=sandbox_id,
                        version=version,
                        auto_commit=auto_commit,
                        commit_message=commit_message,
                        timeout_seconds=timeout_seconds,
                        env=env or {},
                    ),
                )
            except GatewayError as exc:
                _raise_mcp_error(exc)
            return _dump(result)

    return mcp
