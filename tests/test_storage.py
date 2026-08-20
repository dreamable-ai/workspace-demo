from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import inspect

from workspace_gateway.models import ProviderName, SandboxState, utc_now
from workspace_gateway.storage import (
    ProviderConfigurationRecord,
    SandboxRecord,
    SandboxStore,
    SandboxTemplateRecord,
)


class StorageTest(unittest.TestCase):
    def test_provider_configuration_round_trip_and_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SandboxStore(Path(directory) / "gateway.db")
            store.open()
            store.upsert_provider_configuration(
                ProviderConfigurationRecord(
                    provider=ProviderName.PAI,
                    api_key="first-secret",
                    domain="sandbox.example.com",
                    timeout_seconds=900,
                    updated_at=utc_now(),
                    mode="e2b",
                )
            )
            stored = store.get_provider_configuration(ProviderName.PAI)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.api_key, "first-secret")  # type: ignore[union-attr]
            self.assertEqual(len(store.list_provider_configurations()), 1)

            store.upsert_provider_configuration(
                ProviderConfigurationRecord(
                    provider=ProviderName.PAI,
                    api_key="second-secret",
                    domain="sandbox.example.com",
                    timeout_seconds=1200,
                    updated_at=utc_now(),
                    mode="e2b",
                )
            )
            updated = store.get_provider_configuration(ProviderName.PAI)
            self.assertEqual(updated.api_key, "second-secret")  # type: ignore[union-attr]
            self.assertEqual(updated.timeout_seconds, 1200)  # type: ignore[union-attr]
            store.close()

    def test_round_trip_and_state_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SandboxStore(Path(directory) / "gateway.db")
            store.open()
            now = utc_now()
            store.insert(
                SandboxRecord(
                    id="sbxgw_1",
                    provider=ProviderName.E2B,
                    provider_sandbox_id="provider-1",
                    state=SandboxState.RUNNING,
                    template_id="base",
                    created_at=now,
                    updated_at=now,
                    timeout_seconds=1800,
                    metadata={"workspace_id": "ws_1"},
                    env_keys=["NODE_ENV"],
                )
            )
            record = store.get("sbxgw_1")
            self.assertIsNotNone(record)
            self.assertEqual(record.provider, ProviderName.E2B)  # type: ignore[union-attr]
            self.assertEqual(record.timeout_seconds, 1800)  # type: ignore[union-attr]
            self.assertEqual(record.metadata["workspace_id"], "ws_1")  # type: ignore[union-attr]
            self.assertEqual(record.env_keys, ["NODE_ENV"])  # type: ignore[union-attr]
            updated = store.update_state("sbxgw_1", SandboxState.PAUSED)
            self.assertEqual(updated.state, SandboxState.PAUSED)  # type: ignore[union-attr]
            store.close()

    def test_template_catalog_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SandboxStore(Path(directory) / "gateway.db")
            store.open()
            now = utc_now()
            store.insert_template(
                SandboxTemplateRecord(
                    id="tpl_1",
                    provider=ProviderName.PAI,
                    template_id="code-interpreter",
                    name="Code Interpreter",
                    description="Coding workspace",
                    default_timeout_seconds=900,
                    created_at=now,
                    updated_at=now,
                )
            )
            templates = store.list_templates()
            self.assertEqual(len(templates), 1)
            self.assertEqual(templates[0].template_id, "code-interpreter")
            self.assertTrue(templates[0].is_default)
            self.assertEqual(store.get_default_template().id, "tpl_1")  # type: ignore[union-attr]

            store.insert_template(
                SandboxTemplateRecord(
                    id="tpl_2",
                    provider=ProviderName.E2B,
                    template_id="base",
                    name="E2B Base",
                    description="Base workspace",
                    default_timeout_seconds=1200,
                    created_at=now,
                    updated_at=now,
                )
            )
            selected = store.set_default_template("tpl_2")
            self.assertTrue(selected.is_default)  # type: ignore[union-attr]
            self.assertEqual(store.get_default_template().id, "tpl_2")  # type: ignore[union-attr]
            self.assertTrue(store.delete_template("tpl_1"))
            self.assertEqual(len(store.list_templates()), 1)
            store.close()

    def test_migrates_original_mvp_schema(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE sandboxes (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_sandbox_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    template_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(provider, provider_sandbox_id)
                )
                """
            )
            connection.commit()
            connection.close()

            store = SandboxStore(path)
            store.open()
            columns = {
                column["name"] for column in inspect(store.engine).get_columns("sandboxes")
            }
            self.assertTrue(
                {"timeout_seconds", "metadata_json", "env_keys_json"}.issubset(columns)
            )
            store.close()
