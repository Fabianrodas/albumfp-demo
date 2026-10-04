"""Image tagging provider is disabled in the local-only Demo."""

from ._local_only import LocalOnlyIntegrationError

CONFIANZA_MINIMA = 0.55
CONFIANZA_MINIMA_CONOCIDA = 0.30
MAX_SUGERENCIAS = 12


class ImaggaError(LocalOnlyIntegrationError):
    def __init__(self, reason: str = "local_only"):
        super().__init__("automatic tag suggestions")
        self.reason = reason


def suggest_tags(*args, **kwargs):
    raise ImaggaError()
