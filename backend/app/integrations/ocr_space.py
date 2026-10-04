"""Cloud OCR is disabled in the local-only Demo."""

from ._local_only import LocalOnlyIntegrationError


class OcrError(LocalOnlyIntegrationError):
    def __init__(self, reason: str = "local_only"):
        super().__init__("cloud text recognition")
        self.reason = reason


def extract_text(*args, **kwargs):
    raise OcrError()
