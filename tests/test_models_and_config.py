from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from workspace_gateway.config import E2BProviderSettings, Settings
from workspace_gateway.models import FileWriteRequest, ProviderConfigurationRequest


class ModelsAndConfigTest(unittest.TestCase):
    def test_runtime_paths_and_port_are_loaded_from_environment(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "GATEWAY_PORT": "9090",
                "GATEWAY_LOG_DIR": "/srv/workspace-gateway/logs",
                "GATEWAY_AUTH_ENABLED": "false",
                "GATEWAY_SANDBOX_CODE_DIR": "/sandbox/project",
                "GATEWAY_WORKSPACE_STORAGE_PATH": "/srv/workspace-gateway/workspaces",
            },
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.port, 9090)
        self.assertEqual(settings.log_dir, Path("/srv/workspace-gateway/logs"))
        self.assertFalse(settings.auth_enabled)
        self.assertEqual(settings.sandbox_code_dir, "/sandbox/project")
        self.assertEqual(
            settings.workspace_storage_path,
            Path("/srv/workspace-gateway/workspaces"),
        )

    def test_file_write_requires_one_valid_content_form(self) -> None:
        self.assertEqual(FileWriteRequest(path="a", text="ok").text, "ok")
        with self.assertRaises(ValidationError):
            FileWriteRequest(path="a")
        with self.assertRaises(ValidationError):
            FileWriteRequest(path="a", text="x", content_base64="eA==")
        with self.assertRaises(ValidationError):
            FileWriteRequest(path="a", content_base64="not-base64")

    def test_custom_e2b_provider_requires_domain(self) -> None:
        custom = E2BProviderSettings(
            name="volcengine",
            api_key="secret",
            template_id="template",
            timeout_seconds=900,
        )
        cloud = E2BProviderSettings(
            name="e2b",
            api_key="secret",
            template_id="base",
            timeout_seconds=900,
        )
        self.assertFalse(custom.configured)
        self.assertTrue(cloud.configured)

    def test_e2b_domain_accepts_hostname_and_rejects_complete_api_url(self) -> None:
        request = ProviderConfigurationRequest(
            domain=" sandbox01.cn-shanghai.pai-eas.aliyuncs.com "
        )
        self.assertEqual(
            request.domain, "sandbox01.cn-shanghai.pai-eas.aliyuncs.com"
        )

        for invalid_domain in (
            "https://api.sandbox01.cn-shanghai.pai-eas.aliyuncs.com",
            "api.sandbox01.cn-shanghai.pai-eas.aliyuncs.com",
            "sandbox01.cn-shanghai.pai-eas.aliyuncs.com/path",
        ):
            with self.subTest(domain=invalid_domain), self.assertRaises(
                ValidationError
            ):
                ProviderConfigurationRequest(domain=invalid_domain)
