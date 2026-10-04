"""Geocoding and map provider are disabled in the local-only Demo."""

from ._local_only import LocalOnlyIntegrationError


class LocationIQError(LocalOnlyIntegrationError):
    def __init__(self, reason: str = "local_only"):
        super().__init__("online place search")
        self.reason = reason


def reverse_geocode(*args, **kwargs):
    raise LocationIQError()


def search_places(*args, **kwargs):
    raise LocationIQError()


def fetch_static_map(*args, **kwargs):
    raise LocationIQError()
