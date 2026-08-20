from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from workspace_gateway.app import create_app
from workspace_gateway.config import Settings
from workspace_gateway.models import ProviderName


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            host="127.0.0.1",
            port=8080,
            api_key="",
            allow_insecure_local=True,
            database_path=Path(self.temp.name) / "gateway.db",
            sandbox_code_dir="/workspace",
            workspace_storage_path=Path(self.temp.name) / "workspaces",
        )
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def test_health_and_discovery(self) -> None:
        self.assertEqual(self.client.get("/health").status_code, 200)
        console = self.client.get("/console")
        self.assertEqual(console.status_code, 200)
        self.assertIn("Workspace Gateway Console", console.text)
        self.assertEqual(
            self.client.get("/console/assets/console.css").status_code, 200
        )
        self.assertEqual(
            self.client.get("/console/assets/console.js").status_code, 200
        )
        providers = self.client.get("/v1/providers")
        self.assertEqual(providers.status_code, 200)
        self.assertEqual(
            {item["provider"] for item in providers.json()},
            {"pai", "e2b", "volcengine"},
        )
        sandboxes = self.client.get("/v1/sandboxes")
        self.assertEqual(sandboxes.status_code, 200)
        self.assertEqual(sandboxes.json(), [])

    def test_authentication_can_be_disabled_explicitly(self) -> None:
        unsecured_settings = replace(
            self.settings,
            host="0.0.0.0",
            api_key="gateway-secret",
            allow_insecure_local=False,
            auth_enabled=False,
        )
        with TestClient(create_app(unsecured_settings)) as unsecured:
            self.assertEqual(unsecured.get("/v1/providers").status_code, 200)

    def test_mcp_streamable_http_initialize(self) -> None:
        response = self.client.post(
            "/mcp",
            headers={
                "Host": "127.0.0.1:8080",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-11-25",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "gateway-test", "version": "1"},
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["result"]["serverInfo"]["name"], "workspace-gateway")
        self.assertIn("tools", response.json()["result"]["capabilities"])

    def test_mcp_uses_gateway_api_key_as_bearer_token(self) -> None:
        secured_settings = replace(self.settings, api_key="gateway-secret")
        with TestClient(create_app(secured_settings)) as secured:
            unauthorized = secured.post("/mcp")
            self.assertEqual(unauthorized.status_code, 401)
            self.assertEqual(unauthorized.headers["www-authenticate"], "Bearer")

            authorized = secured.post(
                "/mcp",
                headers={
                    "Host": "127.0.0.1:8080",
                    "Authorization": "Bearer gateway-secret",
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-11-25",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "gateway-test", "version": "1"},
                    },
                },
            )
            self.assertEqual(authorized.status_code, 200, authorized.text)

    def test_configure_pai_provider_without_returning_secret(self) -> None:
        token = "temporary-secret-token"
        response = self.client.put(
            "/v1/providers/pai/configuration",
            json={
                "domain": "sandbox01.cn-shanghai.pai-eas.aliyuncs.com",
                "api_key": token,
                "timeout_seconds": 1200,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["configured"])
        self.assertIsNone(body["default_template_id"])
        self.assertNotIn(token, response.text)

        stored = self.client.app.state.store.get_provider_configuration(
            ProviderName.PAI
        )
        self.assertIsNotNone(stored)
        self.assertEqual(stored.api_key, token)  # type: ignore[union-attr]
        self.assertEqual(  # type: ignore[union-attr]
            stored.domain,
            "sandbox01.cn-shanghai.pai-eas.aliyuncs.com",
        )
        self.assertFalse((Path(self.temp.name) / ".env").exists())

        discovery = self.client.get("/v1/providers").json()
        pai = next(item for item in discovery if item["provider"] == "pai")
        self.assertTrue(pai["configured"])
        self.assertNotIn(token, str(discovery))

    def test_rejects_incomplete_pai_provider_configuration(self) -> None:
        response = self.client.put(
            "/v1/providers/pai/configuration",
            json={"domain": "sandbox.example.com"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("API Token", response.json()["detail"])

    def test_template_catalog_lifecycle(self) -> None:
        created = self.client.post(
            "/v1/templates",
            json={
                "provider": "pai",
                "template_id": "code-interpreter",
                "name": "PAI Code Interpreter",
                "description": "Node.js coding workspace",
                "default_timeout_seconds": 1200,
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        template = created.json()
        self.assertTrue(template["id"].startswith("tpl_"))
        self.assertEqual(template["provider"], "pai")
        self.assertTrue(template["is_default"])

        listed = self.client.get("/v1/templates")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)

        duplicate = self.client.post(
            "/v1/templates",
            json={
                "provider": "pai",
                "template_id": "code-interpreter",
                "name": "Duplicate",
            },
        )
        self.assertEqual(duplicate.status_code, 409)

        second = self.client.post(
            "/v1/templates",
            json={
                "provider": "e2b",
                "template_id": "base",
                "name": "E2B Base",
            },
        )
        self.assertEqual(second.status_code, 201, second.text)
        selected = self.client.put(f"/v1/templates/{second.json()['id']}/default")
        self.assertEqual(selected.status_code, 200, selected.text)
        self.assertTrue(selected.json()["is_default"])
        defaults = [item for item in self.client.get("/v1/templates").json() if item["is_default"]]
        self.assertEqual([item["id"] for item in defaults], [second.json()["id"]])

        deleted = self.client.delete(f"/v1/templates/{template['id']}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(len(self.client.get("/v1/templates").json()), 1)

    def test_workspace_file_and_version_lifecycle(self) -> None:
        created = self.client.post(
            "/v1/workspaces",
            json={"name": "Next App", "description": "Persistent project"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        workspace = created.json()
        self.assertTrue(workspace["id"].startswith("ws_"))
        self.assertFalse(workspace["dirty"])

        written = self.client.put(
            f"/v1/workspaces/{workspace['id']}/file",
            json={"path": "app.js", "text": "console.log('workspace')"},
        )
        self.assertEqual(written.status_code, 200, written.text)
        self.assertEqual(written.json()["path"], "app.js")

        detail = self.client.get(f"/v1/workspaces/{workspace['id']}").json()
        self.assertTrue(detail["dirty"])
        self.assertEqual(detail["file_count"], 1)

        committed = self.client.post(
            f"/v1/workspaces/{workspace['id']}/commits",
            json={"message": "Add app"},
        )
        self.assertEqual(committed.status_code, 200, committed.text)
        version = committed.json()["version"]["version"]
        self.assertTrue(committed.json()["created"])

        history = self.client.get(
            f"/v1/workspaces/{workspace['id']}/versions"
        ).json()
        self.assertEqual(history[0]["message"], "Add app")
        read = self.client.get(
            f"/v1/workspaces/{workspace['id']}/file",
            params={"path": "app.js", "version": version},
        )
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(read.json()["version"], version)

        escaped = self.client.put(
            f"/v1/workspaces/{workspace['id']}/file",
            json={"path": "../secret", "text": "no"},
        )
        self.assertEqual(escaped.status_code, 400)
