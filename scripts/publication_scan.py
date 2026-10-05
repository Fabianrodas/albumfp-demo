"""Deterministic publication checks for tracked AlbumFP Demo files.

Run from any directory with ``python scripts/publication_scan.py``. The scan
checks index blobs and modified working copies, never ignored or untracked
files, and reports locations/reasons without printing matched secret text.
It complements a general secret scanner; it does not replace one.
"""

from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{24,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{24,}={0,2}"),
    re.compile(r"(?im)^\s*(?:Private" + r"Key|Preshared" + r"Key)\s*=\s*[A-Za-z0-9+/]{43}=?\s*$"),
)
IPV4 = re.compile(r"(?<![A-Za-z0-9_])(?:\d{1,3}\.){3}\d{1,3}(?![A-Za-z0-9_])")
IPV6 = re.compile(r"(?<![A-Za-z0-9_:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![A-Za-z0-9_:])")
URL = re.compile(r"https?://[^\s\"'<>`)]+", re.IGNORECASE)
USER_PATH = re.compile(r"(?:(?i:[A-Z]:\\Users\\[^\\\s]+\\)|/Users/[^/\s]+/|/home/[^/\s]+/)")
PLACEHOLDER = re.compile(r"(?i)^(?:generated_by_setup|placeholder|example|test|dummy|redacted|secret|unused)$")
DATABASE_PASSWORD = re.compile(
    r"(?i)(?:DATABASE_URL|TEST_DATABASE_URL)\s*[:=]\s*['\"]?\w+://[^:\s]+:([^@/\s'\"]+)@"
)
ALLOWED_RUNTIME_HOSTS = {"localhost", "127.0.0.1", "::1", "www.w3.org"}
RUNTIME_PRODUCT_LINK = "albumfp" + ".com"
DOC_RANGES = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)
PRIVATE_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "10" + ".0.0.0/8",
        "172.16" + ".0.0/12",
        "192.168" + ".0.0/16",
        "169.254" + ".0.0/16",
    )
)
IGNORED_PATHS = (
    ".env",
    "frontend/dist/",
    "frontend/node_modules/",
    "backend/.venv/",
    "backend/storage/",
    "backend/uploads/",
    "backend/data/",
    "backend/runtime/",
    "backend/tmp/",
    "backend/quarantine/",
    "postgresql/",
    "pgdata/",
    "pg_wal/",
    "data/",
    "runtime/",
    "media/",
    "tmp/",
    "quarantine/",
    "exports/",
    "backups/",
    ".npm-cache/",
    ".angular/",
    ".pytest_cache/",
)
PRODUCTION_TERMS = tuple(
    "".join(parts)
    for parts in (
        ("rack", "nerd"),
        ("/srv/", "albumfp/media"),
        ("ng", "inx"),
        ("gun", "icorn"),
        ("MEDIA_", "ORIGIN_", "TOKEN"),
        ("MEDIA_", "ORIGIN_", "BASE_URL"),
        ("NGINX_", "INTERNAL_MEDIA_URI"),
        ("X-", "Accel-Buffering"),
        ("X-", "Accel-Redirect"),
        ("proxy_", "request_buffering"),
        ("MEDIA_", "EXPECTED_MOUNTPOINT"),
        ("remote ", "media origin"),
        ("private ", "media origin"),
    )
)
PRIVATE_DNS_SUFFIXES = (".internal", ".corp", ".lan", ".local")
PRIVATE_DNS_NAME = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"(?:internal|corp|lan|local)\b"
)
DOC_IPV6_RANGES = (ipaddress.ip_network("2001:db8::/32"),)
SSH_HOST_BLOCK = re.compile(
    r"(?ims)^\s*Host\s+([^\r\n#]+)(.*?)(?=^\s*Host\s+|\Z)"
)
SSH_HOSTNAME = re.compile(r"(?im)^\s*Host" + r"Name\s+([^\s#]+)")
WIREGUARD_BLOCK = re.compile(r"(?im)^\s*\[(?:Interface|Peer)\]\s*$")
PRIVATE_OPS_COMPONENTS = {
    ".agents", ".aws", ".codex", ".ssh", "deploy", "deployment",
    "infrastructure", "infra", "operations", "ops", "rollback", "backups",
}
DATA_ARTIFACT_COMPONENTS = {"backups", "exports", "postgresql", "pgdata", "pg_wal", "runtime"}
RUNTIME_ARTIFACT_COMPONENTS = {
    ".angular", ".codex-local", ".git", ".mypy_cache", ".npm-cache",
    ".pytest_cache", ".ruff_cache", ".superpowers", "coverage", "dist",
    "node_modules", "pg_wal", "pgdata", "postgresql", "playwright-report",
    "runtime", "test-results", "venv", ".venv",
}
SECRET_FILE_SUFFIXES = (".dump", ".backup", ".bak", ".log", ".sqlite", ".sqlite3", ".db")
ENV_SECRET_KEYS = ("FLASK_SECRET_KEY", "POSTGRES_PASSWORD", "POSTGRES_ADMIN_PASSWORD")
ENV_URL_KEYS = ("DATABASE_URL", "TEST_DATABASE_URL")


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def tracked_paths() -> list[str]:
    return [part.decode("utf-8", "surrogateescape") for part in git("ls-files", "-z").split(b"\0") if part]


def candidate_blobs(path: str) -> list[bytes]:
    """Return indexed and working copies of an indexed file, if either exists."""
    candidates: list[bytes] = []
    staged = subprocess.run(
        ["git", "show", f":{path}"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    if staged.returncode == 0:
        candidates.append(staged.stdout)

    working = ROOT / Path(path)
    try:
        if working.is_symlink():
            raw = str(working.readlink()).encode("utf-8", "surrogateescape")
        elif working.is_file():
            raw = working.read_bytes()
        else:
            raw = None
    except OSError:
        raw = None
    if raw is not None and raw not in candidates:
        candidates.append(raw)
    if not candidates:
        committed = subprocess.run(
            ["git", "show", f"HEAD:{path}"], cwd=ROOT, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if committed.returncode == 0:
            candidates.append(committed.stdout)
    return candidates


def is_test(path: str) -> bool:
    return (
        "/tests/" in f"/{path}/"
        or path.startswith("tests/")
        or bool(re.search(r"\.(?:spec|test)\.[^.]+$", path, re.IGNORECASE))
    )


def is_ignored_path(path: str, ignored_paths: tuple[str, ...] = IGNORED_PATHS) -> bool:
    normalized = path.replace("\\", "/").strip("/").lower()
    if normalized == ".env" or (normalized.startswith(".env.") and normalized != ".env.example"):
        return True
    for ignored in ignored_paths:
        prefix = ignored.replace("\\", "/").strip("/").lower()
        if ignored.endswith("/") and (normalized == prefix or normalized.startswith(prefix + "/")):
            return True
        if not ignored.endswith("/") and normalized == prefix:
            return True
    components = set(normalized.split("/"))
    return bool(components.intersection(RUNTIME_ARTIFACT_COMPONENTS))


def path_findings(path: str) -> list[str]:
    normalized = path.replace("\\", "/").strip("/").lower()
    components = set(normalized.split("/"))
    findings: list[str] = []
    if is_ignored_path(path):
        findings.append("runtime or secret artifact path")
    if components.intersection(PRIVATE_OPS_COMPONENTS) or normalized.startswith("docs/superpowers/"):
        findings.append("private operations file path")
    if components.intersection(DATA_ARTIFACT_COMPONENTS):
        findings.append("database or runtime artifact path")
    if components.intersection({"wireguard", "wireguard-config"}) or re.search(
        r"(?:^|/)(?:wg\w*|wireguard)[^/]*\.conf$", normalized
    ):
        findings.append("private network configuration path")
    suffix = Path(normalized).suffix
    if suffix in SECRET_FILE_SUFFIXES or normalized.endswith((".sql.gz", ".sql.zip")):
        findings.append("database or runtime artifact path")
    if suffix == ".sql" and normalized != "backend/schemas/schema.sql":
        findings.append("database or runtime artifact path")
    ssh_name = Path(normalized).name
    if ssh_name in {"config", "ssh_config"} and (".ssh" in components or "ssh" in components):
        findings.append("private operations file path")
    return sorted(set(findings))


def environment_template_findings(text: str) -> list[str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")

    required = {
        "APP_ENV", "FLASK_SECRET_KEY", "DATABASE_URL", "TEST_DATABASE_URL",
        "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER", "POSTGRES_PASSWORD",
        "POSTGRES_ADMIN_USER", "POSTGRES_ADMIN_PASSWORD",
    }
    if not required.issubset(values):
        return ["environment template is not local and placeholder-only"]
    if values["APP_ENV"].lower() != "development" or values["POSTGRES_HOST"].lower() not in {
        "localhost", "127.0.0.1", "::1"
    } or values["POSTGRES_PORT"] != "55432":
        return ["environment template is not local and placeholder-only"]
    if any(not PLACEHOLDER.fullmatch(values[key]) for key in ENV_SECRET_KEYS):
        return ["environment template is not local and placeholder-only"]
    for key, expected_database in zip(ENV_URL_KEYS, ("albumfp_demo", "albumfp_demo_test")):
        try:
            parts = urlsplit(values[key])
            username = parts.username
            password = parts.password
            host = parts.hostname
            port = parts.port
        except ValueError:
            return ["environment template is not local and placeholder-only"]
        if (
            parts.scheme != "postgresql+psycopg2" or username != "albumfp_demo"
            or password is None or not PLACEHOLDER.fullmatch(password)
            or host not in {"localhost", "127.0.0.1", "::1"} or port != 55432
            or parts.path != f"/{expected_database}" or parts.query or parts.fragment
        ):
            return ["environment template is not local and placeholder-only"]
    if values["POSTGRES_USER"] != "albumfp_demo" or values["POSTGRES_ADMIN_USER"] != "albumfp_demo_admin":
        return ["environment template is not local and placeholder-only"]
    return []


def content_findings(path: str, text: str) -> list[str]:
    findings: set[str] = set()
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            findings.add("credential or private-key pattern")
            break

    for match in DATABASE_PASSWORD.finditer(text):
        if not PLACEHOLDER.fullmatch(match.group(1)):
            findings.add("database URL contains a literal password")
            break

    if path == ".env.example" and environment_template_findings(text):
        findings.add("environment template is not local and placeholder-only")

    if path != "scripts/publication_scan.py":
        lowered = text.casefold()
        if any(term.casefold() in lowered for term in PRODUCTION_TERMS):
            findings.add("production-specific terminology")
        if USER_PATH.search(text):
            findings.add("user-specific filesystem path")

    for literal in IPV4.findall(text):
        try:
            address = ipaddress.ip_address(literal)
        except ValueError:
            continue
        if (
            address.is_loopback
            or any(address in network for network in DOC_RANGES)
            or (address.is_unspecified and is_test(path))
        ):
            continue
        if address.is_unspecified or any(address in network for network in PRIVATE_IPV4_NETWORKS):
            findings.add("private infrastructure address")
            break

    for literal in IPV6.findall(text):
        try:
            address = ipaddress.ip_address(literal)
        except ValueError:
            continue
        if (
            address.is_loopback
            or any(address in network for network in DOC_IPV6_RANGES)
            or (address.is_unspecified and is_test(path))
        ):
            continue
        if address.is_private or address.is_link_local or address.is_unspecified:
            findings.add("private infrastructure address")
            break

    if path != "scripts/publication_scan.py":
        if PRIVATE_DNS_NAME.search(text):
            findings.add("private host alias")
        for block in SSH_HOST_BLOCK.finditer(text):
            hostname = SSH_HOSTNAME.search(block.group(2))
            if hostname:
                host = hostname.group(1).strip("[]").lower()
                if host.endswith(PRIVATE_DNS_SUFFIXES) or "." not in host and host != "localhost":
                    findings.add("private host alias")
                    break
        if WIREGUARD_BLOCK.search(text) and any(
            term.casefold() in text.casefold() for term in ("Private" + "Key", "Preshared" + "Key")
        ):
            findings.add("private network configuration")

    if is_runtime_source(path) and not is_test(path):
        for candidate in URL.findall(text):
            try:
                host = (urlsplit(candidate.rstrip(".,;:)")).hostname or "").lower()
            except ValueError:
                host = ""
            if host in ALLOWED_RUNTIME_HOSTS or host.endswith(".invalid"):
                continue
            if host == RUNTIME_PRODUCT_LINK and (
                path.endswith("site.ts") or path.startswith("frontend/src/app/pages/about/")
            ):
                continue
            findings.add("external runtime URL")
            break
    return sorted(findings)


def is_runtime_source(path: str) -> bool:
    return path.startswith(("backend/app/", "backend/scripts/", "frontend/src/")) or path in {
        "backend/app.py",
        "backend/application.py",
    }


def main() -> int:
    failures: list[tuple[str, str]] = []
    paths = tracked_paths()
    path_set = set(paths)

    for required_ignore in IGNORED_PATHS:
        if any(is_ignored_path(path, (required_ignore,)) for path in paths):
            failures.append((required_ignore, "runtime or secret artifact is tracked"))
    if ".env.example" not in path_set:
        failures.append((".env.example", "safe environment template is missing"))

    for path in paths:
        for reason in path_findings(path):
            failures.append((path, reason))
        for raw in candidate_blobs(path):
            if b"\0" in raw:
                continue
            text = raw.decode("utf-8", "replace")
            for reason in content_findings(path, text):
                failures.append((path, reason))

    if failures:
        for path, reason in sorted(set(failures)):
            print(f"FAIL {reason}: {path}")
        print(f"PUBLICATION_SCAN=FAIL ({len(set(failures))} finding(s))")
        return 1

    if shutil.which("gitleaks"):
        print("GITLEAKS=AVAILABLE (run separately with the repository's approved version)")
    else:
        print("GITLEAKS=UNAVAILABLE; deterministic Git-tree checks completed")
    print(f"PUBLICATION_SCAN=PASS ({len(paths)} tracked file(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
