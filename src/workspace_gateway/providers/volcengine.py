"""Volcengine REST bridge adapter.

This adapter deliberately targets an explicit bridge contract instead of
guessing undocumented provider OpenAPI action names. It can be placed in front
of an official Volcengine sandbox API client once account-specific endpoint and
signing requirements are known. The contract is documented in docs/technical-solution.md.
"""

from __future__ import annotations

import base64

import httpx

from ..config import VolcengineSettings
from ..errors import ProviderNotConfiguredError, ProviderOperationError
from ..models import CommandResult, ProviderCapabilities, ProviderName, SandboxState
from .base import CreatedSandbox


class VolcengineRestProvider:
    def __init__(self, settings: VolcengineSettings) -> None:
        self.settings = settings

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=ProviderName.VOLCENGINE,
            configured=self.settings.configured,
            endpoint=self.settings.base_url or self.settings.e2b_domain,
            default_template_id=self.settings.template_id or None,
            default_timeout_seconds=self.settings.timeout_seconds,
            notes=[
                "REST mode uses the versioned Volcengine bridge contract.",
                "Provider-specific signing remains inside the bridge, never in callers.",
            ],
        )

    def _check(self) -> None:
        if not self.settings.configured or self.settings.mode != "rest":
            raise ProviderNotConfiguredError(
                "Volcengine REST provider requires base URL, API key and template ID"
            )

    def _url(self, path: str) -> str:
        self._check()
        return f"{self.settings.base_url.rstrip('/')}{path}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.request(
                    method, self._url(path), headers=self._headers(), **kwargs
                )
            response.raise_for_status()
            if not response.content:
                return {}
            data = response.json()
            if not isinstance(data, dict):
                raise TypeError("Bridge response must be a JSON object")
            return data
        except Exception as exc:
            raise ProviderOperationError(f"volcengine bridge request failed: {exc}") from exc

    async def create(
        self,
        *,
        template_id: str | None,
        timeout_seconds: int | None,
        env: dict[str, str],
        metadata: dict[str, str],
    ) -> CreatedSandbox:
        selected_template = template_id or self.settings.template_id
        if not selected_template:
            raise ProviderNotConfiguredError(
                "A template_id from the sandbox template catalog is required"
            )
        selected_timeout = timeout_seconds or self.settings.timeout_seconds
        data = await self._request(
            "POST",
            "/v1/sandboxes",
            json={
                "template_id": selected_template,
                "timeout_seconds": selected_timeout,
                "env": env,
                "metadata": metadata,
            },
        )
        sandbox_id = str(data.get("sandbox_id") or data.get("id") or "")
        if not sandbox_id:
            raise ProviderOperationError("Volcengine bridge omitted sandbox_id")
        return CreatedSandbox(
            sandbox_id,
            SandboxState.RUNNING,
            selected_template,
            selected_timeout,
        )

    async def status(self, sandbox_id: str) -> SandboxState:
        data = await self._request("GET", f"/v1/sandboxes/{sandbox_id}")
        value = str(data.get("state", "unknown")).lower()
        return SandboxState(value) if value in SandboxState._value2member_map_ else SandboxState.UNKNOWN

    async def run_command(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str,
        timeout_seconds: float,
        env: dict[str, str],
    ) -> CommandResult:
        data = await self._request(
            "POST",
            f"/v1/sandboxes/{sandbox_id}/commands",
            json={
                "command": command,
                "cwd": cwd,
                "timeout_seconds": timeout_seconds,
                "env": env,
            },
        )
        return CommandResult(
            exit_code=int(data.get("exit_code", -1)),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
        )

    async def start_process(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str,
        env: dict[str, str],
    ) -> int | None:
        data = await self._request(
            "POST",
            f"/v1/sandboxes/{sandbox_id}/processes",
            json={"command": command, "cwd": cwd, "env": env},
        )
        pid = data.get("pid")
        return int(pid) if pid is not None else None

    async def write_file(self, sandbox_id: str, path: str, content: bytes) -> None:
        await self._request(
            "PUT",
            f"/v1/sandboxes/{sandbox_id}/files",
            json={"path": path, "content_base64": base64.b64encode(content).decode()},
        )

    async def read_file(self, sandbox_id: str, path: str) -> bytes:
        data = await self._request(
            "GET", f"/v1/sandboxes/{sandbox_id}/files", params={"path": path}
        )
        value = data.get("content_base64")
        if not isinstance(value, str):
            raise ProviderOperationError("Volcengine bridge omitted content_base64")
        return base64.b64decode(value, validate=True)

    async def preview_url(self, sandbox_id: str, port: int) -> str:
        url, _ = await self.preview_connection(sandbox_id, port)
        return url

    async def preview_connection(
        self, sandbox_id: str, port: int
    ) -> tuple[str, dict[str, str]]:
        data = await self._request(
            "GET", f"/v1/sandboxes/{sandbox_id}/preview", params={"port": port}
        )
        url = str(data.get("url", ""))
        if not url:
            raise ProviderOperationError("Volcengine bridge omitted preview URL")
        return url, {}

    async def pause(self, sandbox_id: str) -> None:
        await self._request("POST", f"/v1/sandboxes/{sandbox_id}/pause")

    async def kill(self, sandbox_id: str) -> None:
        await self._request("DELETE", f"/v1/sandboxes/{sandbox_id}")
