from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DETAIL_CSS = ROOT / "frontend/src/app/pages/dashboard/media-detail/media-detail.css"


def test_media_detail_targets_follow_pointer_capability() -> None:
    css = DETAIL_CSS.read_text(encoding="utf-8")
    compact = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    desktop_button = re.search(r"\.detail-actions \.btn\s*\{([^}]*)\}", compact)
    assert desktop_button, "media detail must keep a base rule for action buttons"
    assert re.search(r"min-height\s*:\s*38px", desktop_button.group(1)), (
        "mouse layouts must retain the established 38px action density"
    )

    coarse = re.search(
        r"@media\s*\(\s*pointer\s*:\s*coarse\s*\)\s*\{([^}]+)\}", compact
    )
    assert coarse, "touch sizing must follow coarse pointer capability, not viewport width"
    rules = coarse.group(1)
    assert "min-height:44px" in re.sub(r"\s+", "", rules), (
        "coarse-pointer controls must provide a 44px touch target"
    )
    for selector in (
        ".back-link",
        ".detail-actions .btn",
        ".detail-actions .tema",
        ".detail-navigation .text-action",
        ".info-body .btn",
    ):
        assert selector in rules, f"{selector} must receive touch sizing"
