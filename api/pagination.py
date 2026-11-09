"""Offset pagination for list endpoints. The body stays a plain JSON array (backwards compatible); the total
count is in the X-Total-Count header and the next page's offset in X-Next-Offset when there is one."""

from __future__ import annotations

from fastapi import Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select


class Page:
    def __init__(self, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        self.offset = offset
        self.limit = limit


def paginate(db: Session, stmt: Select, page: Page, response: Response) -> list:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.scalars(stmt.offset(page.offset).limit(page.limit)).all()
    set_headers(response, total, page, len(rows))
    return list(rows)


def set_headers(response: Response, total: int, page: Page, returned: int) -> None:
    response.headers["X-Total-Count"] = str(total)
    if page.offset + returned < total:
        response.headers["X-Next-Offset"] = str(page.offset + returned)


def slice_list(items: list, page: Page, response: Response) -> list:
    set_headers(response, len(items), page, min(page.limit, max(0, len(items) - page.offset)))
    return items[page.offset: page.offset + page.limit]
