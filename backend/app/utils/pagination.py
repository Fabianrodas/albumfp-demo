from math import ceil
from flask import Request


def get_pagination_args(request: Request, default_per_page: int = 20, max_per_page: int = 100):
    page_raw = request.args.get("page", "1")
    per_page_raw = request.args.get("per_page", str(default_per_page))

    try:
        page = int(page_raw)
    except ValueError:
        page = 1

    try:
        per_page = int(per_page_raw)
    except ValueError:
        per_page = default_per_page

    page = max(1, page)
    per_page = max(1, min(max_per_page, per_page))
    offset = (page - 1) * per_page

    return page, per_page, offset


def build_pagination_meta(page: int, per_page: int, total: int):
    total_pages = ceil(total / per_page) if total else 0
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
    }
