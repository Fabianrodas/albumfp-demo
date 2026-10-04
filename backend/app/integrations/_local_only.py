"""Shared failure type for provider features disabled in the local Demo."""


class LocalOnlyIntegrationError(RuntimeError):
    def __init__(self, provider: str):
        self.reason = "local_only"
        super().__init__(f"{provider} is unavailable in the local-only Demo")
