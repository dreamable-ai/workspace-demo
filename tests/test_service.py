from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workspace_gateway.errors import WorkspacePathError
from workspace_gateway.models import (
    CommandRequest,
    CommandResult,
    CreateSandboxRequest,
    FileWriteRequest,
    ProviderCapabilities,
    ProviderName,
    SandboxState,
)
from workspace_gateway.providers.base import CreatedSandbox
from workspace_gateway.service import SandboxGatewayService
from workspace_gateway.storage import SandboxStore


class FakeProvider:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.killed = False
        self.kill_calls = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(provider=ProviderName.PAI, configured=True)

    async def create(self, **kwargs: object) -> CreatedSandbox:
        template_id = str(kwargs.get("template_id") or "node-template")
        timeout_seconds = int(kwargs.get("timeout_seconds") or 900)
        return CreatedSandbox(
            "remote-1", SandboxState.RUNNING, template_id, timeout_seconds
        )

    async def status(self, _: str) -> SandboxState:
        return SandboxState.RUNNING

    async def run_command(self, _id: str, command: str, **_: object) -> CommandResult:
        return CommandResult(exit_code=0, stdout=command, stderr="")

    async def start_process(self, *_: object, **__: object) -> int:
        return 123

    async def write_file(self, _id: str, path: str, content: bytes) -> None:
        self.files[path] = content

    async def read_file(self, _id: str, path: str) -> bytes:
        return self.files[path]

    async def preview_url(self, _id: str, port: int) -> str:
        return f"https://{port}-remote.example"

    async def preview_connection(
        self, _id: str, port: int
    ) -> tuple[str, dict[str, str]]:
        return f"https://{port}-remote.example", {"X-Access-Token": "secret"}

    async def pause(self, _id: str) -> None:
        return None

    async def kill(self, _id: str) -> None:
        self.killed = True
        self.kill_calls += 1


class FakeRegistry:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider

    def get(self, _: ProviderName) -> FakeProvider:
        return self.provider


class ServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = SandboxStore(Path(self.temp.name) / "gateway.db")
        self.store.open()
        self.provider = FakeProvider()
        self.service = SandboxGatewayService(FakeRegistry(self.provider), self.store)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    async def test_create_command_files_and_kill(self) -> None:
        sandbox = await self.service.create(CreateSandboxRequest(provider=ProviderName.PAI))
        self.assertTrue(sandbox.id.startswith("sbxgw_"))
        self.assertEqual(sandbox.provider_sandbox_id, "remote-1")
        self.assertEqual(sandbox.timeout_seconds, 900)

        command = await self.service.run_command(
            sandbox.id, CommandRequest(command="node --version")
        )
        self.assertEqual(command.stdout, "node --version")

        written = await self.service.write_file(
            sandbox.id, FileWriteRequest(path="app.js", text="console.log('ok')")
        )
        self.assertEqual(written.path, "/workspace/app.js")
        read = await self.service.read_file(sandbox.id, "app.js")
        self.assertEqual(read.size, len("console.log('ok')"))

        killed = await self.service.kill(sandbox.id)
        self.assertEqual(killed.state, SandboxState.TERMINATED)
        self.assertTrue(self.provider.killed)

        killed_again = await self.service.kill(sandbox.id)
        self.assertEqual(killed_again.state, SandboxState.TERMINATED)
        self.assertEqual(self.provider.kill_calls, 1)

    async def test_rejects_path_outside_workspace(self) -> None:
        sandbox = await self.service.create(CreateSandboxRequest(provider=ProviderName.PAI))
        with self.assertRaises(WorkspacePathError):
            await self.service.write_file(
                sandbox.id, FileWriteRequest(path="/etc/passwd", text="no")
            )
