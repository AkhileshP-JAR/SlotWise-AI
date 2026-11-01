"""Shared pagination and sorting helpers for SQLAlchemy 2.x repositories."""

from __future__ import annotations

import math

from backend.app.schemas.common import PaginationMeta


def clamp_pagination(page: int = 1, page_size: int = 25, max_page_size: int = 250) -> tuple[int, int, int]:
    """Validate and return (safe_page, safe_page_size, offset)."""
    safe_page = max(1, int(page))
    safe_size = max(1, min(int(page_size), max_page_size))
    offset = (safe_page - 1) * safe_size
    return safe_page, safe_size, offset


def build_pagination_meta(page: int, page_size: int, total_items: int) -> PaginationMeta:
    """Construct standard PaginationMeta from total record count."""
    total_pages = math.ceil(total_items / page_size) if total_items > 0 else 0
    return PaginationMeta(
        page=page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
    )
