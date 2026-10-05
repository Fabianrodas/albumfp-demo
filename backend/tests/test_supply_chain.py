"""Public Demo dependency and local-runtime boundary checks."""

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class PublicDependencyBoundaryTests(unittest.TestCase):
    def test_backend_dependencies_do_not_include_network_provider_clients(self):
        requirements = ROOT / "backend" / "requirements.txt"
        lines = [line.strip().lower() for line in requirements.read_text(encoding="utf-8").splitlines()]
        direct = [line for line in lines if line and not line.startswith(("#", "-r "))]
        names = {re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip() for line in direct}
        self.assertIn("psycopg2-binary", names)
        self.assertTrue({"flask", "sqlalchemy", "alembic"}.issubset(names))
        self.assertFalse({"httpx", "requests", "boto3", "gunicorn"} & names)

    def test_frontend_package_and_lockfile_agree(self):
        package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual("albumfp-demo", package["name"])
        self.assertEqual("1.3.1", package["version"])
        self.assertEqual(package["name"], lock["name"])
        self.assertEqual(package["version"], lock["version"])
        self.assertIn("127.0.0.1", package["scripts"]["start"])

    def test_environment_template_has_loopback_origins_and_no_provider_secrets(self):
        template = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("PUBLIC_ORIGIN=http://localhost:4200", template)
        self.assertIn("CORS_ORIGINS=http://localhost:4200", template)
        self.assertNotRegex(template, re.compile(r"(?im)^\s*[A-Z0-9_]+_API_(?:KEY|SECRET)\s*="))


if __name__ == "__main__":
    unittest.main()
