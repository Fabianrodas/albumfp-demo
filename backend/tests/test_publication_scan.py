from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import publication_scan


def test_private_infrastructure_ipv4_is_rejected_but_documentation_ip_is_allowed() -> None:
    address = "10." + "44.3.9"
    findings = publication_scan.content_findings("README.md", f"database host: {address}")
    assert "private infrastructure address" in findings
    assert publication_scan.content_findings("README.md", "example: 192.0.2.10") == []
    wildcard_address = "0.0" + ".0.0"
    assert "private infrastructure address" in publication_scan.content_findings(
        "backend/app/server.py", f"bind to {wildcard_address}"
    )


def test_private_ipv6_and_private_dns_names_in_ordinary_text_are_rejected() -> None:
    ula_address = "fd00" + "::1234"
    private_host = "media" + ".internal"
    findings = publication_scan.content_findings(
        "docs/setup.md", f"PostgreSQL at {private_host}; local route {ula_address}"
    )
    assert "private host alias" in findings
    assert "private infrastructure address" in findings
    assert publication_scan.content_findings("README.md", "example: 2001:db8::10") == []
    wildcard_address = ":" + ":"
    assert "private infrastructure address" in publication_scan.content_findings(
        "README.md", f"listen on {wildcard_address}"
    )


def test_albumfp_specific_production_terms_and_roots_are_rejected() -> None:
    samples = (
        "Rack" + "Nerd",
        "ng" + "inx",
        "gun" + "icorn",
        "/srv/" + "albumfp/media",
        "MEDIA_" + "ORIGIN_TOKEN",
        "MEDIA_" + "ORIGIN_BASE_URL",
        "MEDIA_" + "EXPECTED_MOUNTPOINT",
        "NG" + "INX_INTERNAL_MEDIA_URI",
        "X-" + "Accel-Buffering",
        "remote " + "media origin",
    )
    for sample in samples:
        assert "production-specific terminology" in publication_scan.content_findings("README.md", sample)


def test_private_ssh_alias_and_wireguard_material_are_rejected() -> None:
    ssh_config = "Host demo\n  HostName media" + ".internal\n"
    wireguard = "[Interface]\n" + "Private" + "Key = " + "A" * 43 + "=\n"
    assert "private host alias" in publication_scan.content_findings("docs/network.md", ssh_config)
    assert "private network configuration" in publication_scan.content_findings("docs/network.md", wireguard)
    assert "private operations file path" in publication_scan.path_findings(".ssh/config")
    assert "private network configuration path" in publication_scan.path_findings("secrets/wg0.conf")


def test_deployment_dump_log_and_database_paths_are_rejected() -> None:
    deployment = "deploy" + "ment/site.conf"
    assert "private operations file path" in publication_scan.path_findings(deployment)
    assert "database or runtime artifact path" in publication_scan.path_findings("backups/cluster.dump")
    assert "database or runtime artifact path" in publication_scan.path_findings("logs/postgresql.log")
    assert "database or runtime artifact path" in publication_scan.path_findings("backend/local/pgdata/base/1")
    assert publication_scan.path_findings("backend/schemas/schema.sql") == []


def test_environment_template_requires_local_generated_placeholders() -> None:
    safe = """APP_ENV=development
FLASK_SECRET_KEY=GENERATED_BY_SETUP
DATABASE_URL=postgresql+psycopg2://albumfp_demo:GENERATED_BY_SETUP@127.0.0.1:55432/albumfp_demo
TEST_DATABASE_URL=postgresql+psycopg2://albumfp_demo:GENERATED_BY_SETUP@127.0.0.1:55432/albumfp_demo_test
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=55432
POSTGRES_USER=albumfp_demo
POSTGRES_PASSWORD=GENERATED_BY_SETUP
POSTGRES_ADMIN_USER=albumfp_demo_admin
POSTGRES_ADMIN_PASSWORD=GENERATED_BY_SETUP
"""
    assert publication_scan.environment_template_findings(safe) == []

    private_host = "192.168." + "1.20"
    unsafe = safe.replace("127.0.0.1", private_host).replace(
        "POSTGRES_PASSWORD=GENERATED_BY_SETUP",
        "POSTGRES_PASSWORD=" + "real" + "local" + "password",
    )
    assert "environment template is not local and placeholder-only" in publication_scan.environment_template_findings(unsafe)


def test_secret_patterns_detect_private_keys_and_common_tokens() -> None:
    key_marker = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
    token = "ghp_" + "A" * 35
    assert "credential or private-key pattern" in publication_scan.content_findings(
        "docs/example.txt", key_marker
    )
    assert "credential or private-key pattern" in publication_scan.content_findings(
        "docs/example.txt", token
    )


def test_runtime_source_external_urls_are_rejected() -> None:
    source = "requests.get('https://service" + ".example/v1')"
    assert "external runtime URL" in publication_scan.content_findings("backend/app/client.py", source)
    assert publication_scan.content_findings(
        "backend/app/client.py", "namespace='http://www.w3.org/2000/svg'"
    ) == []


def test_scanner_rule_definitions_do_not_self_match() -> None:
    source = (REPO_ROOT / "scripts" / "publication_scan.py").read_text(encoding="utf-8")
    assert publication_scan.content_findings("scripts/publication_scan.py", source) == []


def test_test_files_and_ignored_runtime_paths_are_classified() -> None:
    assert publication_scan.is_test("frontend/src/app/example.spec.ts")
    assert publication_scan.is_test("backend/tests/test_example.py")
    for path in (".env", "frontend/node_modules/x", "backend/.venv/x", "postgresql/data", "pgdata/base"):
        assert publication_scan.is_ignored_path(path)
    assert not publication_scan.is_ignored_path(".env.example")
