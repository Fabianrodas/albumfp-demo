from __future__ import annotations

import hashlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_public_screenshot_urls_use_a_revision_matching_the_asset_bytes() -> None:
    capture_root = ROOT / "frontend" / "public" / "capturas"
    digest = hashlib.sha256()
    captures = sorted(capture_root.rglob("*.webp"))
    assert captures, "the Demo must keep its synthetic public screenshots"
    for image in captures:
        digest.update(image.relative_to(capture_root).as_posix().encode())
        digest.update(image.read_bytes())
    revision = digest.hexdigest()[:10]

    source = (ROOT / "frontend" / "src" / "app" / "components" / "ui" / "shot-frame" / "shot-frame.ts").read_text(encoding="utf-8")
    assert re.search(rf"CAPTURAS_REV\s*=\s*'{revision}'", source)
    assert "?v=${CAPTURAS_REV}" in source
