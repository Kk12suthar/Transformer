from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.core.config import settings
from app.db.database import engine
from app.services.table_service import assert_valid_identifier


def _collect_expired_tables(conn, hours: int) -> set[str]:
    rows = conn.execute(
        text(
            f"""
            SELECT DISTINCT st.table_name
            FROM {settings.app_schema}.session_tables st
            JOIN {settings.app_schema}.chat_sessions cs ON cs.id = st.session_id
            WHERE cs.updated_at < NOW() - (:hours * INTERVAL '1 hour')
            UNION
            SELECT DISTINCT uf.table_name
            FROM {settings.app_schema}.uploaded_files uf
            JOIN {settings.app_schema}.chat_sessions cs ON cs.id = uf.session_id
            WHERE cs.updated_at < NOW() - (:hours * INTERVAL '1 hour')
            """
        ),
        {"hours": hours},
    ).all()
    return {row[0] for row in rows if row and row[0]}


def cleanup_old_data(hours: int, dry_run: bool = False) -> dict[str, object]:
    dropped_tables: list[str] = []
    deleted_sessions = 0

    with engine.begin() as conn:
        tables = sorted(_collect_expired_tables(conn, hours))
        expired_sessions = int(
            conn.execute(
                text(
                    f"""
                    SELECT COUNT(*)
                    FROM {settings.app_schema}.chat_sessions
                    WHERE updated_at < NOW() - (:hours * INTERVAL '1 hour')
                    """
                ),
                {"hours": hours},
            ).scalar()
            or 0
        )
        for table_name in tables:
            try:
                safe_name = assert_valid_identifier(table_name)
            except ValueError:
                continue
            if not dry_run:
                conn.execute(
                    text(f'DROP TABLE IF EXISTS "{settings.uploads_schema}"."{safe_name}"')
                )
            dropped_tables.append(safe_name)

        if not dry_run:
            relation_exists = conn.execute(
                text("SELECT to_regclass(:relation_name)"),
                {"relation_name": f"{settings.uploads_schema}.table_registry"},
            ).scalar()
            if relation_exists and dropped_tables:
                for table_name in dropped_tables:
                    conn.execute(
                        text(
                            f"""
                            DELETE FROM {settings.uploads_schema}.table_registry
                            WHERE table_name = :table_name
                            """
                        ),
                        {"table_name": table_name},
                    )

            result = conn.execute(
                text(
                    f"""
                    DELETE FROM {settings.app_schema}.chat_sessions
                    WHERE updated_at < NOW() - (:hours * INTERVAL '1 hour')
                    """
                ),
                {"hours": hours},
            )
            deleted_sessions = int(result.rowcount or 0)
        else:
            deleted_sessions = expired_sessions

    return {
        "hours": hours,
        "dry_run": dry_run,
        "dropped_tables": dropped_tables,
        "deleted_sessions": deleted_sessions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete prototype data older than a retention window.")
    parser.add_argument("--hours", type=int, default=settings.data_retention_hours)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = cleanup_old_data(args.hours, dry_run=args.dry_run)
    print(result)


if __name__ == "__main__":
    main()
