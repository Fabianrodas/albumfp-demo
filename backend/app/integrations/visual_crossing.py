"""Weather provider is disabled in the local-only Demo."""

from ._local_only import LocalOnlyIntegrationError


class WeatherError(LocalOnlyIntegrationError):
    def __init__(self, reason: str = "local_only"):
        super().__init__("weather enrichment")
        self.reason = reason


def get_historical_weather(*args, **kwargs):
    raise WeatherError()


def get_current_weather(*args, **kwargs):
    raise WeatherError()
