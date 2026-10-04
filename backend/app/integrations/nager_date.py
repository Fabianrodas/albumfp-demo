"""Holiday provider is disabled in the local-only Demo."""

from ._local_only import LocalOnlyIntegrationError


class HolidayError(LocalOnlyIntegrationError):
    def __init__(self, reason: str = "local_only"):
        super().__init__("public holiday lookup")
        self.reason = reason


def get_holidays(*args, **kwargs):
    raise HolidayError()


def find_holiday(holidays: list[dict], target_date: str) -> dict | None:
    return next((holiday for holiday in holidays if holiday.get("date") == target_date), None)
