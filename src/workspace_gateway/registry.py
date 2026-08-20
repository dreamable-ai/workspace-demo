"""Provider registration and capability discovery."""

from __future__ import annotations

from dataclasses import dataclass

from .config import E2BProviderSettings, VolcengineSettings
from .errors import ProviderConfigurationError, ProviderNotConfiguredError
from .models import (
    ProviderCapabilities,
    ProviderConfigurationRequest,
    ProviderName,
    utc_now,
)
from .providers import E2BCompatibleProvider, SandboxProvider, VolcengineRestProvider
from .storage import ProviderConfigurationRecord


@dataclass(frozen=True)
class PreparedProviderConfiguration:
    provider: ProviderName
    adapter: SandboxProvider
    record: ProviderConfigurationRecord


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[ProviderName, SandboxProvider] = {
            ProviderName.PAI: E2BCompatibleProvider(
                E2BProviderSettings("pai", "", "", 900), ProviderName.PAI
            ),
            ProviderName.E2B: E2BCompatibleProvider(
                E2BProviderSettings("e2b", "", "", 900), ProviderName.E2B
            ),
            ProviderName.VOLCENGINE: VolcengineRestProvider(
                VolcengineSettings("rest", "", "", 900)
            ),
        }

    @staticmethod
    def _adapter_from_record(record: ProviderConfigurationRecord) -> SandboxProvider:
        if record.provider in {ProviderName.PAI, ProviderName.E2B}:
            settings = E2BProviderSettings(
                name=record.provider.value,
                api_key=record.api_key,
                domain=record.domain if record.provider == ProviderName.PAI else None,
                template_id="",
                timeout_seconds=record.timeout_seconds,
            )
            return E2BCompatibleProvider(settings, record.provider)

        settings = VolcengineSettings(
            mode=record.mode or "rest",
            api_key=record.api_key,
            template_id="",
            timeout_seconds=record.timeout_seconds,
            e2b_domain=record.e2b_domain,
            base_url=record.base_url,
        )
        if settings.mode == "e2b":
            return E2BCompatibleProvider(
                E2BProviderSettings(
                    name="volcengine",
                    api_key=settings.api_key,
                    template_id="",
                    timeout_seconds=settings.timeout_seconds,
                    domain=settings.e2b_domain,
                ),
                ProviderName.VOLCENGINE,
            )
        return VolcengineRestProvider(settings)

    def apply_saved_configuration(self, record: ProviderConfigurationRecord) -> None:
        self._providers[record.provider] = self._adapter_from_record(record)

    def get(self, provider: ProviderName) -> SandboxProvider:
        adapter = self._providers.get(provider)
        if adapter is None:
            raise ProviderNotConfiguredError(f"Unknown provider: {provider.value}")
        return adapter

    def capabilities(self) -> list[ProviderCapabilities]:
        return [provider.capabilities for provider in self._providers.values()]

    def prepare_configuration(
        self, provider: ProviderName, body: ProviderConfigurationRequest
    ) -> PreparedProviderConfiguration:
        current = self.get(provider)
        current_settings = getattr(current, "settings", None)
        secret = body.api_key.get_secret_value() if body.api_key else None

        if provider in {ProviderName.PAI, ProviderName.E2B}:
            if not isinstance(current_settings, E2BProviderSettings):
                raise ProviderConfigurationError("Provider settings are unavailable")
            settings = E2BProviderSettings(
                name=provider.value,
                api_key=secret or current_settings.api_key,
                domain=(body.domain or current_settings.domain) if provider == ProviderName.PAI else None,
                template_id=current_settings.template_id,
                timeout_seconds=body.timeout_seconds or current_settings.timeout_seconds,
            )
            if not settings.configured:
                required = "Domain 和 API Token" if provider == ProviderName.PAI else "API Token"
                raise ProviderConfigurationError(f"{provider.value.upper()} 需要配置 {required}")
            adapter: SandboxProvider = E2BCompatibleProvider(settings, provider)
            record = ProviderConfigurationRecord(
                provider=provider,
                api_key=settings.api_key,
                mode="e2b",
                domain=settings.domain,
                timeout_seconds=settings.timeout_seconds,
                updated_at=utc_now(),
            )
            return PreparedProviderConfiguration(provider, adapter, record)

        if not isinstance(current_settings, VolcengineSettings):
            # E2B mode exposes E2BProviderSettings; preserve what the UI can safely infer.
            current_settings = VolcengineSettings(
                mode="e2b",
                api_key=current_settings.api_key if isinstance(current_settings, E2BProviderSettings) else "",
                template_id=current_settings.template_id if isinstance(current_settings, E2BProviderSettings) else "",
                timeout_seconds=current_settings.timeout_seconds if isinstance(current_settings, E2BProviderSettings) else 900,
                e2b_domain=current_settings.domain if isinstance(current_settings, E2BProviderSettings) else None,
            )
        mode = body.mode or current_settings.mode
        settings = VolcengineSettings(
            mode=mode,
            api_key=secret or current_settings.api_key,
            template_id=current_settings.template_id,
            timeout_seconds=body.timeout_seconds or current_settings.timeout_seconds,
            e2b_domain=body.e2b_domain or current_settings.e2b_domain,
            base_url=body.base_url or current_settings.base_url,
        )
        if not settings.configured:
            endpoint = "E2B Domain" if mode == "e2b" else "Bridge Base URL"
            raise ProviderConfigurationError(
                f"火山引擎需要配置 {endpoint} 和 API Token"
            )
        if mode == "e2b":
            adapter = E2BCompatibleProvider(
                E2BProviderSettings(
                    name="volcengine", api_key=settings.api_key,
                    template_id=settings.template_id,
                    timeout_seconds=settings.timeout_seconds,
                    domain=settings.e2b_domain,
                ),
                ProviderName.VOLCENGINE,
            )
        else:
            adapter = VolcengineRestProvider(settings)
        record = ProviderConfigurationRecord(
            provider=provider,
            api_key=settings.api_key,
            mode=settings.mode,
            e2b_domain=settings.e2b_domain,
            base_url=settings.base_url,
            timeout_seconds=settings.timeout_seconds,
            updated_at=utc_now(),
        )
        return PreparedProviderConfiguration(provider, adapter, record)

    def apply_configuration(self, prepared: PreparedProviderConfiguration) -> None:
        self._providers[prepared.provider] = prepared.adapter
