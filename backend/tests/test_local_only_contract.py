"""Regression checks for the Demo's no-egress and local-storage contract."""

import os
import re
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.storage.backends import (
    get_storage_backend,
    reset_storage_backend,
    storage_backend_mode,
    validate_storage_configuration,
)
from app.storage.contracts import StorageConfigurationError


class LocalStorageContractTests(unittest.TestCase):
    def tearDown(self):
        reset_storage_backend()

    def test_local_filesystem_is_the_only_storage_mode(self):
        with patch.dict(os.environ, {"MEDIA_STORAGE_BACKEND": "local"}, clear=False):
            validate_storage_configuration()
            self.assertEqual(storage_backend_mode(), "local")
            self.assertIsNotNone(get_storage_backend())

    def test_nonlocal_storage_mode_fails_closed(self):
        reset_storage_backend()
        with patch.dict(os.environ, {"MEDIA_STORAGE_BACKEND": "remote"}, clear=False):
            with self.assertRaises(StorageConfigurationError):
                validate_storage_configuration()

    def test_media_root_inside_repository_is_rejected(self):
        repository = Path(__file__).resolve().parents[2]
        local_path = repository / "backend" / "runtime-media"
        with patch.dict(
            os.environ,
            {"MEDIA_STORAGE_BACKEND": "local", "MEDIA_STORAGE_ROOT": str(local_path)},
            clear=False,
        ):
            with self.assertRaises(StorageConfigurationError):
                validate_storage_configuration()


class LocalOriginContractTests(unittest.TestCase):
    def test_browser_origins_are_limited_to_the_loopback_frontend(self):
        from app.utils.origins import cors_origins, trusted_origins

        with patch.dict(
            os.environ,
            {"CORS_ORIGINS": "http://localhost:4200", "PUBLIC_ORIGIN": "http://localhost:4200"},
            clear=False,
        ):
            self.assertEqual(cors_origins(), ["http://localhost:4200"])
            self.assertEqual(trusted_origins(), {"http://localhost:4200"})

    def test_public_origins_outside_loopback_are_rejected(self):
        from app.utils.origins import cors_origins

        with patch.dict(os.environ, {"CORS_ORIGINS": "https://demo.invalid"}, clear=False):
            with self.assertRaises(ValueError):
                cors_origins()


class OfflineIntegrationContractTests(unittest.TestCase):
    def test_password_breach_check_is_local_only_and_keeps_fail_open_contract(self):
        from app.integrations.pwned_passwords import is_password_pwned

        self.assertIsNone(is_password_pwned("synthetic demo password"))

    def test_provider_integrations_fail_without_network_access(self):
        from app.integrations.imagga import ImaggaError, suggest_tags
        from app.integrations.locationiq import LocationIQError, fetch_static_map, reverse_geocode, search_places
        from app.integrations.nager_date import HolidayError, get_holidays
        from app.integrations.ocr_space import OcrError, extract_text
        from app.integrations.visual_crossing import WeatherError, get_historical_weather

        cases = [
            (ImaggaError, suggest_tags),
            (LocationIQError, reverse_geocode),
            (LocationIQError, search_places),
            (LocationIQError, fetch_static_map),
            (HolidayError, get_holidays),
            (OcrError, extract_text),
            (WeatherError, get_historical_weather),
        ]
        for error_type, operation in cases:
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(error_type) as raised:
                    operation()
                self.assertEqual(raised.exception.reason, "local_only")

    def test_solar_times_are_calculated_without_a_provider(self):
        from app.integrations.sunrise_sunset import get_solar_times

        times = get_solar_times(0, 0, "2026-03-20")
        sunrise = datetime.fromisoformat(times["sunrise_at"])
        sunset = datetime.fromisoformat(times["sunset_at"])
        self.assertEqual(sunrise.utcoffset().total_seconds(), 0)
        self.assertEqual(sunset.utcoffset().total_seconds(), 0)
        self.assertTrue(5.5 <= sunrise.hour + sunrise.minute / 60 <= 6.5)
        self.assertTrue(17.5 <= sunset.hour + sunset.minute / 60 <= 18.5)

    def test_application_code_contains_no_outbound_http_client_calls(self):
        repository = Path(__file__).resolve().parents[2]
        roots = (repository / "backend" / "app", repository / "frontend" / "src")
        outbound = re.compile(
            r"(?i)(?:urllib\.request\.urlopen|requests\.(?:get|post|put|delete)\s*\(|"
            r"httpx\.(?:Client|AsyncClient|get|post)\s*\(|"
            r"fetch\s*\(\s*['\"]https?://|new\s+WebSocket\s*\()"
        )
        matches = []
        for root in roots:
            for source in root.rglob("*"):
                if source.is_file() and source.suffix.lower() in {".py", ".ts", ".js", ".html"}:
                    if source.name.endswith(".spec.ts"):
                        continue
                    if outbound.search(source.read_text(encoding="utf-8", errors="ignore")):
                        matches.append(str(source.relative_to(repository)))
        self.assertEqual(matches, [])


if __name__ == "__main__":
    unittest.main()
