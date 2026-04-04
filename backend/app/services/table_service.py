import io
import re

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import engine


_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sanitize_identifier(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name.strip().lower())
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"
    return cleaned


def assert_valid_identifier(name: str) -> str:
    if not _VALID_IDENTIFIER.match(name):
        raise ValueError(f"Invalid table name '{name}'")
    return name


def is_table_in_session(db: Session, session_id: str, table_name: str) -> bool:
    q = text(
        f"""
        SELECT 1
        FROM {settings.app_schema}.session_tables
        WHERE session_id = :session_id AND table_name = :table_name
        LIMIT 1
        """
    )
    row = db.execute(q, {"session_id": session_id, "table_name": table_name}).first()
    return row is not None


def preview_table(table_name: str, page: int = 1, limit: int = 100) -> dict:
    table_name = assert_valid_identifier(table_name)
    offset = max(page - 1, 0) * limit

    with engine.begin() as conn:
        count_q = text(f'SELECT COUNT(*) FROM "{settings.uploads_schema}"."{table_name}"')
        total = conn.execute(count_q).scalar() or 0

        data_q = text(
            f'SELECT * FROM "{settings.uploads_schema}"."{table_name}" OFFSET :offset LIMIT :limit'
        )
        rows = conn.execute(data_q, {"offset": offset, "limit": limit}).mappings().all()

        columns_q = text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table_name
            ORDER BY ordinal_position
            """
        )
        cols = [
            r[0]
            for r in conn.execute(
                columns_q, {"schema": settings.uploads_schema, "table_name": table_name}
            ).all()
        ]

    return {
        "table_name": table_name,
        "columns": cols,
        "rows": [dict(r) for r in rows],
        "page": page,
        "limit": limit,
        "total": int(total),
    }


def table_as_csv_bytes(table_name: str) -> bytes:
    table_name = assert_valid_identifier(table_name)
    df = pd.read_sql_query(
        f'SELECT * FROM "{settings.uploads_schema}"."{table_name}"',
        engine,
    )
    buffer = io.StringIO()
    df.to_csv(buffer, index=False)
    return buffer.getvalue().encode("utf-8")
