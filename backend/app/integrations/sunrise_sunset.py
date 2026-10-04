"""Calculate sunrise and sunset locally without a provider request."""

from datetime import date, datetime, time, timedelta, timezone
import math

from ._local_only import LocalOnlyIntegrationError


class SolarError(LocalOnlyIntegrationError):
    def __init__(self, reason: str = "unavailable"):
        super().__init__("solar time calculation")
        self.reason = reason


def _event_utc(day: date, latitude: float, longitude: float, rising: bool):
    day_number = day.timetuple().tm_yday
    longitude_hour = longitude / 15.0
    approximate = day_number + ((6.0 if rising else 18.0) - longitude_hour) / 24.0
    mean_anomaly = 0.9856 * approximate - 3.289
    true_longitude = (mean_anomaly + 1.916 * math.sin(math.radians(mean_anomaly))
                      + 0.020 * math.sin(2 * math.radians(mean_anomaly)) + 282.634) % 360.0
    right_ascension = math.degrees(math.atan(0.91764 * math.tan(math.radians(true_longitude)))) % 360.0
    right_ascension += (math.floor(true_longitude / 90.0) * 90.0
                        - math.floor(right_ascension / 90.0) * 90.0)
    right_ascension /= 15.0
    sin_declination = 0.39782 * math.sin(math.radians(true_longitude))
    cos_declination = math.cos(math.asin(sin_declination))
    denominator = cos_declination * math.cos(math.radians(latitude))
    if abs(denominator) < 1e-12:
        return None
    cos_hour_angle = (math.cos(math.radians(90.833))
                      - sin_declination * math.sin(math.radians(latitude))) / denominator
    if cos_hour_angle > 1.0 or cos_hour_angle < -1.0:
        return None
    hour_angle = math.degrees(math.acos(cos_hour_angle))
    if rising:
        hour_angle = 360.0 - hour_angle
    local_mean_time = hour_angle / 15.0 + right_ascension - 0.06571 * approximate - 6.622
    unwrapped_utc_hours = local_mean_time - longitude_hour
    day_shift = math.floor(unwrapped_utc_hours / 24.0)
    utc_hours = unwrapped_utc_hours - day_shift * 24.0
    base = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return base + timedelta(days=day_shift, hours=utc_hours)


def get_solar_times(latitude: float, longitude: float, taken_on: str | date) -> dict:
    try:
        lat, lon = float(latitude), float(longitude)
        day = taken_on if isinstance(taken_on, date) else date.fromisoformat(str(taken_on)[:10])
    except (TypeError, ValueError) as exc:
        raise SolarError("invalid_input") from exc
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise SolarError("invalid_input")
    sunrise = _event_utc(day, lat, lon, True)
    sunset = _event_utc(day, lat, lon, False)
    return {
        "sunrise_at": sunrise.isoformat() if sunrise else None,
        "sunset_at": sunset.isoformat() if sunset else None,
    }
