from __future__ import annotations

import unittest
from typing import ClassVar
from unittest.mock import patch

from workspace_gateway.config import E2BProviderSettings
from workspace_gateway.models import ProviderName, SandboxState
from workspace_gateway.providers.e2b_compatible import E2BCompatibleProvider

SandboxNotFoundException = type("SandboxNotFoundException", (Exception,), {})


class FakeSandbox:
    connect_calls = 0
    kill_calls: ClassVar[list[str]] = []

    @classmethod
    def connect(cls, sandbox_id: str, **_: object) -> object:
        cls.connect_calls += 1
        raise SandboxNotFoundException(f"Sandbox {sandbox_id} not found")

    @classmethod
    def kill(cls, sandbox_id: str, **_: object) -> bool:
        cls.kill_calls.append(sandbox_id)
        return False


class E2BCompatibleProviderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeSandbox.connect_calls = 0
        FakeSandbox.kill_calls = []
        self.provider = E2BCompatibleProvider(
            E2BProviderSettings(
                name="pai",
                api_key="temporary-token",
                template_id="",
                timeout_seconds=900,
                domain="sandbox.example.com",
            ),
            ProviderName.PAI,
        )

    async def test_kill_calls_control_plane_without_connecting_first(self) -> None:
        with patch.object(self.provider, "_sandbox_class", return_value=FakeSandbox):
            await self.provider.kill("already-gone")

        self.assertEqual(FakeSandbox.connect_calls, 0)
        self.assertEqual(FakeSandbox.kill_calls, ["already-gone"])

    async def test_status_maps_provider_not_found_to_terminated(self) -> None:
        with patch.object(self.provider, "_sandbox_class", return_value=FakeSandbox):
            state = await self.provider.status("already-gone")

        self.assertEqual(state, SandboxState.TERMINATED)
