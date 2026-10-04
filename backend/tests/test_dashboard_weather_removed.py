from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class DashboardWeatherRemovalContract(unittest.TestCase):
    """Only the broken current-weather dashboard surface is removed."""

    def test_backend_current_weather_route_is_removed(self):
        self.assertFalse(
            (ROOT / "backend/app/api/weather.py").exists(),
            "backend/app/api/weather.py must be removed",
        )

        api_init = text("backend/app/api/__init__.py")

        self.assertNotIn(
            "weather_bp",
            api_init,
            "weather_bp must no longer be imported or registered",
        )

    def test_dashboard_current_weather_component_is_removed(self):
        component_root = (
            ROOT
            / "frontend/src/app/components/ui/current-weather"
        )

        self.assertFalse(
            component_root.exists(),
            "current-weather dashboard component must be removed",
        )

        home_ts = text(
            "frontend/src/app/pages/dashboard/home/home.ts"
        )

        home_html = text(
            "frontend/src/app/pages/dashboard/home/home.html"
        )

        self.assertNotIn(
            "CurrentWeather",
            home_ts,
            "home.ts must no longer import the current-weather component",
        )

        self.assertNotIn(
            "app-current-weather",
            home_html,
            "dashboard must no longer render app-current-weather",
        )

    def test_frontend_current_weather_api_contract_is_removed(self):
        album_api = text(
            "frontend/src/app/core/services/album-api.ts"
        )

        self.assertNotIn(
            "/api/weather/current",
            album_api,
            "Angular API service must not expose current weather endpoint",
        )

        self.assertNotIn(
            "currentWeather(",
            album_api,
            "Angular currentWeather() method must be removed",
        )

    def test_historical_photo_weather_stays_local_only(self):
        from app.integrations.visual_crossing import WeatherError
        from app.media.context import _fetch_weather
        from unittest.mock import patch

        with patch("app.media.context.try_reserve", return_value=True), patch.dict(
            "os.environ", {"VISUAL_CROSSING_API_KEY": "unused-local-test-value"}
        ):
            self.assertEqual(
                "local_only", _fetch_weather(object(), 1, 0, 0, "2026-01-01")
            )
        with self.assertRaises(WeatherError) as raised:
            from app.integrations.visual_crossing import get_historical_weather

            get_historical_weather(0, 0, "2026-01-01", api_key="unused")
        self.assertEqual("local_only", raised.exception.reason)

        media_context = text(
            "backend/app/media/context.py"
        )

        migration = text(
            "backend/migrations/versions/0008_weather_context.py"
        )

        detail_ts = text(
            "frontend/src/app/pages/dashboard/media-detail/media-detail.ts"
        )

        detail_html = text(
            "frontend/src/app/pages/dashboard/media-detail/media-detail.html"
        )

        self.assertIn(
            "_fetch_weather",
            media_context,
        )

        self.assertIn(
            "weather_temp_c",
            migration,
        )

        self.assertIn(
            "weatherLabel",
            detail_ts,
        )

        self.assertNotRegex(detail_html.lower(), r"https?://")
        self.assertIn("localmente", detail_html.lower())


    def test_solar_context_is_preserved(self):
        integration = text(
            "backend/app/integrations/sunrise_sunset.py"
        )

        context = text(
            "backend/app/media/context.py"
        )

        migration = text(
            "backend/migrations/versions/0006_solar_context.py"
        )

        detail_ts = text(
            "frontend/src/app/pages/dashboard/media-detail/media-detail.ts"
        )

        self.assertIn(
            "get_solar_times",
            integration,
        )

        self.assertIn(
            "_try_auto_solar",
            context,
        )

        self.assertIn(
            "sunrise_at",
            migration,
        )

        self.assertIn(
            "hasSolar",
            detail_ts,
        )


if __name__ == "__main__":
    unittest.main()
