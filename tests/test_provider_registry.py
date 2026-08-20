from __future__ import annotations

import unittest

from pydantic import SecretStr

from workspace_gateway.models import ProviderConfigurationRequest, ProviderName
from workspace_gateway.registry import ProviderRegistry


class ProviderRegistryTest(unittest.TestCase):
    def test_prepared_pai_configuration_is_database_record(self) -> None:
        registry = ProviderRegistry()
        prepared = registry.prepare_configuration(
            ProviderName.PAI,
            ProviderConfigurationRequest(
                domain="sandbox.example.com",
                api_key=SecretStr("secret"),
                timeout_seconds=1200,
            ),
        )

        self.assertEqual(prepared.record.provider, ProviderName.PAI)
        self.assertEqual(prepared.record.api_key, "secret")
        self.assertEqual(prepared.record.domain, "sandbox.example.com")
        self.assertEqual(prepared.record.timeout_seconds, 1200)
        self.assertTrue(prepared.adapter.capabilities.configured)

        registry.apply_saved_configuration(prepared.record)
        capabilities = next(
            item
            for item in registry.capabilities()
            if item.provider == ProviderName.PAI
        )
        self.assertTrue(capabilities.configured)
        self.assertEqual(capabilities.endpoint, "sandbox.example.com")


if __name__ == "__main__":
    unittest.main()
