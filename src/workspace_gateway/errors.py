"""Domain errors translated to stable Gateway HTTP responses."""


class GatewayError(RuntimeError):
    """Base class for expected Gateway failures."""


class ProviderNotConfiguredError(GatewayError):
    """Raised when a requested provider has no usable credentials or endpoint."""


class ProviderConfigurationError(GatewayError):
    """Raised when a provider configuration cannot be validated or persisted."""


class SandboxNotFoundError(GatewayError):
    """Raised when the Gateway mapping does not exist."""


class ProviderOperationError(GatewayError):
    """Raised when a provider rejects or fails an operation."""


class WorkspacePathError(GatewayError):
    """Raised when a remote path escapes the configured workspace root."""


class WorkspaceNotFoundError(GatewayError):
    """Raised when a persistent Workspace does not exist."""


class WorkspaceConflictError(GatewayError):
    """Raised when a Workspace operation conflicts with its current state."""


class WorkspaceVersionError(GatewayError):
    """Raised when a requested Workspace version cannot be resolved."""


class TemplateConflictError(GatewayError):
    """Raised when the same provider template is registered twice."""


class TemplateNotFoundError(GatewayError):
    """Raised when a template catalog entry does not exist."""
