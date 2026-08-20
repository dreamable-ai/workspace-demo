from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import Client
from test_service import FakeProvider, FakeRegistry

from workspace_gateway.errors import ProviderOperationError, WorkspacePathError
from workspace_gateway.mcp_server import create_mcp_server
from workspace_gateway.models import (
    CreateSandboxRequest,
    ProviderName,
    SandboxState,
    WorkspaceRunRequest,
    utc_now,
)
from workspace_gateway.service import SandboxGatewayService
from workspace_gateway.storage import SandboxStore, SandboxTemplateRecord
from workspace_gateway.workspace_service import WorkspaceService


class WorkspaceServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = SandboxStore(root / "gateway.db")
        self.store.open()
        self.provider = FakeProvider()
        sandbox_service = SandboxGatewayService(  # type: ignore[arg-type]
            FakeRegistry(self.provider), self.store
        )
        self.sandbox_service = sandbox_service
        self.service = WorkspaceService(self.store, root / "workspaces", sandbox_service)
        self.service.open()

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    async def test_git_files_versions_and_sandbox_sync(self) -> None:
        workspace = self.service.create("Node App", "Persistent source")
        self.assertFalse(workspace.dirty)
        self.assertEqual(workspace.file_count, 0)

        written = self.service.write_file(
            workspace.id, "src/app.js", text="console.log('ok')"
        )
        self.assertEqual(written.path, "src/app.js")
        self.assertTrue(self.service.get(workspace.id).dirty)
        self.assertEqual(len(self.service.list_files(workspace.id)), 1)

        commit = self.service.commit(workspace.id, "Add application")
        self.assertTrue(commit.created)
        self.assertEqual(commit.version.message, "Add application")
        self.assertFalse(self.service.get(workspace.id).dirty)
        read = self.service.read_file(
            workspace.id, "src/app.js", commit.version.version
        )
        self.assertEqual(read.version, commit.version.version)

        with self.assertRaises(WorkspacePathError):
            self.service.write_file(workspace.id, "../outside", text="no")

        sandbox = await self.sandbox_service.create(
            CreateSandboxRequest(provider=ProviderName.PAI)
        )
        synced = await self.service.sync(
            workspace.id, sandbox.id, commit.version.version
        )
        self.assertEqual(synced.version, commit.version.version)
        self.assertIn("/workspace/.workspace-gateway-source.tar.gz", self.provider.files)

    async def test_run_auto_commits_and_reuses_or_creates_sandbox(self) -> None:
        workspace = self.service.create("Runner")
        self.service.write_file(workspace.id, "package.json", text='{"scripts":{}}')
        now = utc_now()
        self.store.insert_template(
            SandboxTemplateRecord(
                id="tpl_default",
                provider=ProviderName.PAI,
                template_id="code-interpreter",
                name="Code Interpreter",
                description="",
                default_timeout_seconds=900,
                created_at=now,
                updated_at=now,
                is_default=True,
            )
        )

        run = await self.service.run(
            workspace.id,
            WorkspaceRunRequest(command="node --version", commit_message="Run node"),
        )
        self.assertTrue(run.created_sandbox)
        self.assertEqual(run.result.stdout, "node --version")
        self.assertFalse(self.service.get(workspace.id).dirty)
        runs = self.store.list_workspace_runs(workspace.id)
        self.assertEqual(runs[0].sandbox_id, run.sandbox.id)
        self.assertEqual(runs[0].version, run.version)

    async def test_sync_retries_transient_provider_file_failure(self) -> None:
        workspace = self.service.create("Retry sync")
        self.service.write_file(workspace.id, "index.js", text="console.log('ok')")
        commit = self.service.commit(workspace.id, "Add app")
        sandbox = await self.sandbox_service.create(
            CreateSandboxRequest(provider=ProviderName.PAI)
        )
        original_write = self.provider.write_file
        attempts = 0

        async def flaky_write(*args: object, **kwargs: object) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ProviderOperationError("sandbox channel is not ready")
            await original_write(*args, **kwargs)  # type: ignore[arg-type]

        self.provider.write_file = flaky_write  # type: ignore[method-assign]
        with patch(
            "workspace_gateway.workspace_service._SYNC_PROVIDER_RETRY_DELAYS",
            (0.0,),
        ):
            synced = await self.service.sync(
                workspace.id, sandbox.id, commit.version.version
            )

        self.assertEqual(attempts, 2)
        self.assertEqual(synced.version, commit.version.version)

    async def test_run_cleans_up_new_sandbox_when_sync_never_becomes_ready(self) -> None:
        workspace = self.service.create("Failed sync")
        self.service.write_file(workspace.id, "index.js", text="console.log('ok')")
        now = utc_now()
        self.store.insert_template(
            SandboxTemplateRecord(
                id="tpl_cleanup",
                provider=ProviderName.PAI,
                template_id="code-interpreter",
                name="Code Interpreter",
                description="",
                default_timeout_seconds=900,
                created_at=now,
                updated_at=now,
                is_default=True,
            )
        )

        async def failed_write(*_: object, **__: object) -> None:
            raise ProviderOperationError("sandbox channel is not ready")

        self.provider.write_file = failed_write  # type: ignore[method-assign]
        with patch(
            "workspace_gateway.workspace_service._SYNC_PROVIDER_RETRY_DELAYS",
            (0.0,),
        ), self.assertRaises(ProviderOperationError):
            await self.service.run(
                workspace.id,
                WorkspaceRunRequest(command="node index.js"),
            )

        sandboxes = self.sandbox_service.list()
        self.assertEqual(len(sandboxes), 1)
        self.assertEqual(sandboxes[0].state, SandboxState.TERMINATED)
        self.assertTrue(self.provider.killed)

    async def test_mcp_exposes_workspace_without_provider_catalog(self) -> None:
        mcp = create_mcp_server(self.sandbox_service, self.service)
        async with Client(mcp) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            self.assertEqual(len(names), 14)
            self.assertIn("workspace_create", names)
            self.assertIn("workspace_write_file", names)
            self.assertIn("workspace_commit", names)
            self.assertIn("workspace_run", names)
            self.assertNotIn("sandbox_create", names)
            self.assertNotIn("sandbox_write_file", names)
            self.assertNotIn("sandbox_read_file", names)
            self.assertIn("sandbox_run_command", names)
            self.assertIn("sandbox_start_process", names)
            self.assertIn("sandbox_get_preview", names)
            self.assertNotIn("sandbox_list", names)
            self.assertNotIn("sandbox_list_providers", names)
            self.assertNotIn("sandbox_list_templates", names)

            created = await client.call_tool(
                "workspace_create", {"name": "MCP Project"}
            )
            assert created.structured_content is not None
            workspace_id = created.structured_content["id"]
            written = await client.call_tool(
                "workspace_write_file",
                {
                    "workspace_id": workspace_id,
                    "path": "index.js",
                    "text": "console.log('mcp workspace')",
                },
            )
            self.assertFalse(written.is_error)
            package_written = await client.call_tool(
                "workspace_write_file",
                {
                    "workspace_id": workspace_id,
                    "path": "package.json",
                    "text": '{"scripts":{"start":"node index.js"}}',
                },
            )
            self.assertFalse(package_written.is_error)
            committed = await client.call_tool(
                "workspace_commit",
                {"workspace_id": workspace_id, "message": "Add index"},
            )
            self.assertFalse(committed.is_error)
