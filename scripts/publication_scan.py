"""Deterministic public-release checks for the AlbumFP Demo Git tree.

Run from any directory with ``python scripts/publication_scan.py``. The
scanner reads staged Git blobs when present and committed blobs otherwise; it
never prints matching secret text.
"""

from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
import sys
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
)
IPV4 = re.compile(r"(?<![A-Za-z0-9_])(?:\d{1,3}\.){3}\d{1,3}(?![A-Za-z0-9_])")
URL = re.compile(r"https?://[^\s\"'<>`)]+", re.IGNORECASE)
USER_PATH = re.compile(r"(?:(?i:[A-Z]:\\Users\\[^\\\s]+\\)|/Users/[^/\s]+/|/home/[^/\s]+/)")
PLACEHOLDER = re.compile(r"(?i)^(?:generated_by_setup|placeholder|example|test|dummy|redacted|secret|unused)$")
DATABASE_PASSWORD = re.compile(
    r"(?i)(?:DATABASE_URL|TEST_DATABASE_URL)\s*[:=]\s*['\"]?\w+://[^:\s]+:([^@/\s'\"]+)@"
)
ALLOWED_RUNTIME_HOSTS = {"localhost", "127.0.0.1", "::1", "www.w3.org"}
DOC_RANGES = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)
IGNORED_PATHS = (
    ".env",
    "frontend/dist/",
    "frontend/node_modules/",
    "backend/.venv/",
    "backend/storage/",
)


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def tracked_paths() -> list[str]:
    return [part.decode("utf-8", "surrogateescape") for part in git("ls-files", "-z").split(b"\0") if part]


def blob(path: str) -> bytes:
    staged = subprocess.run(
        ["git", "show", f":{path}"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    if staged.returncode == 0:
        return staged.stdout
    return git("show", f"HEAD:{path}")


def is_test(path: str) -> bool:
    return (
        "/tests/" in f"/{path}/"
        or path.startswith("tests/")
        or bool(re.search(r"\.(?:spec|test)\.[^.]+$", path, re.IGNORECASE))
    )


def is_ignored_path(path: str, ignored_paths: tuple[str, ...] = IGNORED_PATHS) -> bool:
    for ignored in ignored_paths:
        if ignored.endswith("/"):
            if path == ignored[:-1] or path.startswith(ignored):
                return True
        elif path == ignored:
            return True
    return False


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
        raw = blob(path)
        if b"\0" in raw:
            continue
        text = raw.decode("utf-8", "replace")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                failures.append((path, "credential or private-key pattern"))
                break

        for match in DATABASE_PASSWORD.finditer(text):
            if not PLACEHOLDER.fullmatch(match.group(1)):
                failures.append((path, "database URL contains a literal password"))
                break

        if path != "scripts/publication_scan.py":
            if USER_PATH.search(text):
                failures.append((path, "user-specific filesystem path"))
            if not is_test(path):
                for literal in IPV4.findall(text):
                    try:
                        address = ipaddress.ip_address(literal)
                    except ValueError:
                        continue
                    if address.is_loopback or any(address in network for network in DOC_RANGES):
                        continue
                    if address.is_private or address.is_global or address.is_unspecified:
                        failures.append((path, "non-loopback IP literal"))
                        break

        if is_runtime_source(path) and not is_test(path):
            for candidate in URL.findall(text):
                host = (urlsplit(candidate.rstrip(".,;:)")).hostname or "").lower()
                if host in ALLOWED_RUNTIME_HOSTS or host.endswith(".invalid"):
                    continue
                if host == "albumfp.com" and (path.endswith("site.ts") or path.startswith("frontend/src/app/pages/about/")):
                    continue
                failures.append((path, "external runtime URL"))
                break

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
