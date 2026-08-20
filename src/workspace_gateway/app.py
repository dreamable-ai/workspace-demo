"""FastAPI application exposing Workspace-first coding and sandbox runtime APIs."""

from __future__ import annotations

import secrets
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .errors import (
    GatewayError,
    ProviderConfigurationError,
    ProviderNotConfiguredError,
    ProviderOperationError,
    SandboxNotFoundError,
    TemplateConflictError,
    TemplateNotFoundError,
    WorkspaceConflictError,
    WorkspaceNotFoundError,
    WorkspacePathError,
    WorkspaceVersionError,
)
from .mcp_server import create_mcp_server
from .models import (
    CommandRequest,
    CommandResult,
    CreateSandboxRequest,
    FileReadResult,
    FileWriteRequest,
    FileWriteResult,
    PreviewAccessResult,
    PreviewResult,
    ProcessResult,
    ProviderCapabilities,
    ProviderConfigurationRequest,
    ProviderName,
    SandboxTemplateCreateRequest,
    SandboxTemplateView,
    SandboxView,
    StartProcessRequest,
    WorkspaceCommitRequest,
    WorkspaceCommitResult,
    WorkspaceCreateRequest,
    WorkspaceFileReadResult,
    WorkspaceFileView,
    WorkspaceFileWriteRequest,
    WorkspaceRunRequest,
    WorkspaceRunResult,
    WorkspaceSyncRequest,
    WorkspaceSyncResult,
    WorkspaceVersionView,
    WorkspaceView,
    utc_now,
)
from .registry import ProviderRegistry
from .service import SandboxGatewayService
from .storage import SandboxStore, SandboxTemplateRecord
from .workspace_service import WorkspaceService


def _uvicorn_log_config(log_dir: Path) -> dict[str, object]:
    log_dir.mkdir(parents=True, exist_ok=True)
    config = deepcopy(uvicorn.config.LOGGING_CONFIG)
    config["handlers"]["file"] = {
        "class": "logging.handlers.RotatingFileHandler",
        "formatter": "default",
        "filename": str((log_dir / "workspace-gateway.log").resolve()),
        "maxBytes": 10 * 1024 * 1024,
        "backupCount": 5,
        "encoding": "utf-8",
    }
    config["loggers"]["uvicorn"]["handlers"].append("file")
    config["loggers"]["uvicorn.access"]["handlers"].append("file")
    config["root"] = {"handlers": ["default", "file"], "level": "INFO"}
    return config


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    resolved.validate_gateway_auth()
    store = SandboxStore(resolved.database_url or resolved.database_path)
    registry = ProviderRegistry()
    service = SandboxGatewayService(registry, store, resolved.sandbox_code_dir)
    workspace_service = WorkspaceService(store, resolved.workspace_storage_path, service)
    mcp_server = create_mcp_server(service, workspace_service)
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        host=resolved.host,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.open()
        workspace_service.open()
        for record in store.list_provider_configurations():
            registry.apply_saved_configuration(record)
        store.ensure_default_template("code-interpreter")
        app.state.store = store
        app.state.service = service
        app.state.workspace_service = workspace_service
        app.state.mcp_server = mcp_server
        try:
            async with mcp_server.session_manager.run():
                yield
        finally:
            store.close()

    app = FastAPI(
        title="Workspace Gateway",
        version="0.1.0",
        description=(
            "Workspace-first coding control plane with provider-neutral Sandbox execution."
        ),
        lifespan=lifespan,
    )
    web_root = Path(__file__).with_name("web")
    preview_sessions: dict[str, dict[str, object]] = {}
    app.mount("/console/assets", StaticFiles(directory=web_root), name="console-assets")

    @app.middleware("http")
    async def authenticate_mcp(request: Request, call_next):
        if request.url.path != "/mcp" and not request.url.path.startswith("/mcp/"):
            return await call_next(request)
        if not resolved.api_key and resolved.allow_insecure_local:
            return await call_next(request)

        supplied = request.headers.get("x-gateway-api-key", "")
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not supplied or not secrets.compare_digest(supplied, resolved.api_key):
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "detail": "Invalid Gateway API key"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

    @app.middleware("http")
    async def proxy_sandbox_preview(request: Request, call_next):
        parts = request.url.path.split("/", 6)
        if (
            len(parts) < 6
            or parts[1:3] != ["v1", "sandboxes"]
            or parts[4] != "proxy"
        ):
            return await call_next(request)
        gateway_id = parts[3]
        try:
            preview_port = int(parts[5])
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid preview port"})
        proxy_root = f"/v1/sandboxes/{gateway_id}/proxy/{preview_port}/"
        upstream_path = f"/{parts[6]}" if len(parts) == 7 and parts[6] else "/"
        cookie_name = "sandbox_preview_access"
        supplied_token = request.query_params.get("preview_token") or request.cookies.get(
            cookie_name, ""
        )
        session = preview_sessions.get(supplied_token)
        if session is None or session["expires_at"] <= utc_now():
            preview_sessions.pop(supplied_token, None)
            return JSONResponse(status_code=410, content={"detail": "Preview link expired"})
        if session["gateway_id"] != gateway_id or session["port"] != preview_port:
            return JSONResponse(status_code=401, content={"detail": "Invalid preview token"})

        if "preview_token" in request.query_params:
            clean_query = [
                (key, value)
                for key, value in request.query_params.multi_items()
                if key != "preview_token"
            ]
            location = request.url.path
            if clean_query:
                location = f"{location}?{urlencode(clean_query)}"
            response = RedirectResponse(location, status_code=302)
            response.set_cookie(
                cookie_name,
                supplied_token,
                max_age=900,
                httponly=True,
                samesite="lax",
                path=proxy_root,
            )
            return response

        upstream_url = f"{str(session['upstream_url']).rstrip('/')}{upstream_path}"
        if request.url.query:
            upstream_url = f"{upstream_url}?{request.url.query}"
        excluded_request_headers = {
            "authorization",
            "connection",
            "content-length",
            "cookie",
            "host",
            "transfer-encoding",
            "x-gateway-api-key",
        }
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in excluded_request_headers
        }
        headers.update(session["provider_headers"])
        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=60) as client:
                upstream = await client.request(
                    request.method,
                    upstream_url,
                    headers=headers,
                    content=await request.body(),
                )
        except httpx.HTTPError:
            return JSONResponse(
                status_code=502,
                content={"detail": "Sandbox preview is not reachable"},
            )

        excluded_response_headers = {
            "connection",
            "content-encoding",
            "content-length",
            "transfer-encoding",
        }
        response_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key.lower() not in excluded_response_headers
        }
        location = response_headers.get("location")
        if location and location.startswith(str(session["upstream_url"])):
            response_headers["location"] = location.removeprefix(
                str(session["upstream_url"])
            ) or "/"
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=None,
        )

    async def require_api_key(
        x_gateway_api_key: Annotated[str | None, Header()] = None,
    ) -> None:
        if not resolved.api_key and resolved.allow_insecure_local:
            return
        if not x_gateway_api_key or not secrets.compare_digest(
            x_gateway_api_key, resolved.api_key
        ):
            from fastapi import HTTPException

            raise HTTPException(status_code=401, detail="Invalid Gateway API key")

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(_: Request, exc: GatewayError) -> JSONResponse:
        status = 500
        detail = str(exc)
        if isinstance(
            exc,
            (SandboxNotFoundError, TemplateNotFoundError, WorkspaceNotFoundError),
        ):
            status = 404
        elif isinstance(exc, (TemplateConflictError, WorkspaceConflictError)):
            status = 409
        elif isinstance(
            exc,
            (WorkspacePathError, WorkspaceVersionError, ProviderConfigurationError),
        ):
            status = 400
        elif isinstance(exc, ProviderNotConfiguredError):
            status = 503
        elif isinstance(exc, ProviderOperationError):
            status = 502
            detail = "The sandbox provider operation failed"
        return JSONResponse(
            status_code=status,
            content={"error": type(exc).__name__, "detail": detail},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/console")

    @app.get("/console", include_in_schema=False)
    async def console() -> FileResponse:
        return FileResponse(web_root / "console.html")

    @app.get("/v1/providers", response_model=list[ProviderCapabilities])
    async def providers(_: None = Depends(require_api_key)) -> list[ProviderCapabilities]:
        return registry.capabilities()

    @app.put(
        "/v1/providers/{provider}/configuration",
        response_model=ProviderCapabilities,
    )
    async def configure_provider(
        provider: ProviderName,
        body: ProviderConfigurationRequest,
        _: None = Depends(require_api_key),
    ) -> ProviderCapabilities:
        prepared = registry.prepare_configuration(provider, body)
        store.upsert_provider_configuration(prepared.record)
        registry.apply_configuration(prepared)
        return prepared.adapter.capabilities

    @app.get("/v1/templates", response_model=list[SandboxTemplateView])
    async def list_templates(
        _: None = Depends(require_api_key),
    ) -> list[SandboxTemplateView]:
        return [SandboxTemplateView(**record.__dict__) for record in store.list_templates()]

    @app.post("/v1/templates", response_model=SandboxTemplateView, status_code=201)
    async def create_template(
        body: SandboxTemplateCreateRequest,
        _: None = Depends(require_api_key),
    ) -> SandboxTemplateView:
        now = utc_now()
        record = SandboxTemplateRecord(
            id=f"tpl_{uuid.uuid4().hex}",
            provider=body.provider,
            template_id=body.template_id,
            name=body.name,
            description=body.description,
            default_timeout_seconds=body.default_timeout_seconds,
            created_at=now,
            updated_at=now,
            is_default=body.is_default,
        )
        store.insert_template(record)
        saved = store.get_template(record.id) or record
        return SandboxTemplateView(**saved.__dict__)

    @app.put(
        "/v1/templates/{template_catalog_id}/default",
        response_model=SandboxTemplateView,
    )
    async def set_default_template(
        template_catalog_id: str,
        _: None = Depends(require_api_key),
    ) -> SandboxTemplateView:
        record = store.set_default_template(template_catalog_id)
        if record is None:
            raise TemplateNotFoundError(f"Template {template_catalog_id!r} was not found")
        return SandboxTemplateView(**record.__dict__)

    @app.delete("/v1/templates/{template_catalog_id}", response_model=SandboxTemplateView)
    async def delete_template(
        template_catalog_id: str,
        _: None = Depends(require_api_key),
    ) -> SandboxTemplateView:
        record = store.get_template(template_catalog_id)
        if record is None:
            raise TemplateNotFoundError(f"Template {template_catalog_id!r} was not found")
        store.delete_template(template_catalog_id)
        return SandboxTemplateView(**record.__dict__)

    @app.post("/v1/sandboxes", response_model=SandboxView, status_code=201)
    async def create_sandbox(
        body: CreateSandboxRequest, _: None = Depends(require_api_key)
    ) -> SandboxView:
        return await service.create(body)

    @app.get("/v1/sandboxes", response_model=list[SandboxView])
    async def list_sandboxes(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        _: None = Depends(require_api_key),
    ) -> list[SandboxView]:
        return service.list(limit)

    @app.get("/v1/sandboxes/{gateway_id}", response_model=SandboxView)
    async def get_sandbox(
        gateway_id: str,
        refresh: bool = False,
        _: None = Depends(require_api_key),
    ) -> SandboxView:
        return await service.get(gateway_id, refresh)

    @app.post("/v1/sandboxes/{gateway_id}/commands", response_model=CommandResult)
    async def run_command(
        gateway_id: str,
        body: CommandRequest,
        _: None = Depends(require_api_key),
    ) -> CommandResult:
        return await service.run_command(gateway_id, body)

    @app.post("/v1/sandboxes/{gateway_id}/processes", response_model=ProcessResult)
    async def start_process(
        gateway_id: str,
        body: StartProcessRequest,
        _: None = Depends(require_api_key),
    ) -> ProcessResult:
        return await service.start_process(gateway_id, body)

    @app.put("/v1/sandboxes/{gateway_id}/files", response_model=FileWriteResult)
    async def write_file(
        gateway_id: str,
        body: FileWriteRequest,
        _: None = Depends(require_api_key),
    ) -> FileWriteResult:
        return await service.write_file(gateway_id, body)

    @app.get("/v1/sandboxes/{gateway_id}/files", response_model=FileReadResult)
    async def read_file(
        gateway_id: str,
        path: str,
        _: None = Depends(require_api_key),
    ) -> FileReadResult:
        return await service.read_file(gateway_id, path)

    @app.get("/v1/sandboxes/{gateway_id}/preview/{port}", response_model=PreviewResult)
    async def preview(
        gateway_id: str,
        port: int,
        _: None = Depends(require_api_key),
    ) -> PreviewResult:
        if not 1 <= port <= 65535:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail="port must be between 1 and 65535")
        return await service.preview(gateway_id, port)

    @app.post(
        "/v1/sandboxes/{gateway_id}/preview/{port}/access",
        response_model=PreviewAccessResult,
    )
    async def create_preview_access(
        gateway_id: str,
        port: int,
        request: Request,
        _: None = Depends(require_api_key),
    ) -> PreviewAccessResult:
        if not 1 <= port <= 65535:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail="port must be between 1 and 65535")
        upstream_url, provider_headers = await service.preview_connection(
            gateway_id, port
        )
        token = secrets.token_urlsafe(32)
        expires_at = utc_now() + timedelta(minutes=15)
        preview_sessions[token] = {
            "token": token,
            "gateway_id": gateway_id,
            "port": port,
            "upstream_url": upstream_url,
            "provider_headers": provider_headers,
            "expires_at": expires_at,
        }
        proxy_path = f"/v1/sandboxes/{gateway_id}/proxy/{port}/"
        access_url = f"{str(request.base_url).rstrip('/')}{proxy_path}?preview_token={token}"
        return PreviewAccessResult(
            port=port,
            url=access_url,
            upstream_url=upstream_url,
            expires_at=expires_at,
        )

    @app.post("/v1/sandboxes/{gateway_id}/pause", response_model=SandboxView)
    async def pause(
        gateway_id: str, _: None = Depends(require_api_key)
    ) -> SandboxView:
        return await service.pause(gateway_id)

    @app.delete("/v1/sandboxes/{gateway_id}", response_model=SandboxView)
    async def kill(
        gateway_id: str, _: None = Depends(require_api_key)
    ) -> SandboxView:
        return await service.kill(gateway_id)

    @app.get("/v1/workspaces", response_model=list[WorkspaceView])
    async def list_workspaces(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        _: None = Depends(require_api_key),
    ) -> list[WorkspaceView]:
        return workspace_service.list(limit)

    @app.post("/v1/workspaces", response_model=WorkspaceView, status_code=201)
    async def create_workspace(
        body: WorkspaceCreateRequest,
        _: None = Depends(require_api_key),
    ) -> WorkspaceView:
        return workspace_service.create(body.name, body.description)

    @app.get("/v1/workspaces/{workspace_id}", response_model=WorkspaceView)
    async def get_workspace(
        workspace_id: str,
        _: None = Depends(require_api_key),
    ) -> WorkspaceView:
        return workspace_service.get(workspace_id)

    @app.get(
        "/v1/workspaces/{workspace_id}/files",
        response_model=list[WorkspaceFileView],
    )
    async def list_workspace_files(
        workspace_id: str,
        _: None = Depends(require_api_key),
    ) -> list[WorkspaceFileView]:
        return workspace_service.list_files(workspace_id)

    @app.get(
        "/v1/workspaces/{workspace_id}/file",
        response_model=WorkspaceFileReadResult,
    )
    async def read_workspace_file(
        workspace_id: str,
        path: str,
        version: str | None = None,
        _: None = Depends(require_api_key),
    ) -> WorkspaceFileReadResult:
        return workspace_service.read_file(workspace_id, path, version)

    @app.put(
        "/v1/workspaces/{workspace_id}/file",
        response_model=WorkspaceFileView,
    )
    async def write_workspace_file(
        workspace_id: str,
        body: WorkspaceFileWriteRequest,
        _: None = Depends(require_api_key),
    ) -> WorkspaceFileView:
        return workspace_service.write_file(
            workspace_id,
            body.path,
            text=body.text,
            content_base64=body.content_base64,
        )

    @app.post(
        "/v1/workspaces/{workspace_id}/commits",
        response_model=WorkspaceCommitResult,
    )
    async def commit_workspace(
        workspace_id: str,
        body: WorkspaceCommitRequest,
        _: None = Depends(require_api_key),
    ) -> WorkspaceCommitResult:
        return workspace_service.commit(workspace_id, body.message)

    @app.get(
        "/v1/workspaces/{workspace_id}/versions",
        response_model=list[WorkspaceVersionView],
    )
    async def list_workspace_versions(
        workspace_id: str,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        _: None = Depends(require_api_key),
    ) -> list[WorkspaceVersionView]:
        return workspace_service.history(workspace_id, limit)

    @app.post(
        "/v1/workspaces/{workspace_id}/sync",
        response_model=WorkspaceSyncResult,
    )
    async def sync_workspace(
        workspace_id: str,
        body: WorkspaceSyncRequest,
        _: None = Depends(require_api_key),
    ) -> WorkspaceSyncResult:
        return await workspace_service.sync(workspace_id, body.sandbox_id, body.version)

    @app.post(
        "/v1/workspaces/{workspace_id}/runs",
        response_model=WorkspaceRunResult,
    )
    async def run_workspace(
        workspace_id: str,
        body: WorkspaceRunRequest,
        _: None = Depends(require_api_key),
    ) -> WorkspaceRunResult:
        return await workspace_service.run(workspace_id, body)

    # Keep this catch-all mount last so FastAPI's REST, docs, and console routes win.
    app.mount("/", mcp_app, name="mcp")

    return app


def main() -> None:
    load_dotenv(Path.cwd() / ".env", override=False)
    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=_uvicorn_log_config(settings.log_dir),
    )


if __name__ == "__main__":
    main()
