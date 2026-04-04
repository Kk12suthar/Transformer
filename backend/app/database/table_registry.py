"""
Lightweight table registry used by the copied Postgres MCP server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import create_engine, text


def _db_url() -> str:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    dbname = os.getenv("POSTGRES_DBNAME", "postgres")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"


APP_SCHEMA = os.getenv("APP_SCHEMA", "mvp")
UPLOADS_SCHEMA = os.getenv("UPLOADS_SCHEMA", "uploads")


class TableRegistry:
    def __init__(self) -> None:
        self.engine = create_engine(_db_url(), pool_pre_ping=True, future=True)
        self._ensure()

    def _ensure(self) -> None:
        ddl = f"""
        CREATE SCHEMA IF NOT EXISTS {UPLOADS_SCHEMA};
        CREATE TABLE IF NOT EXISTS {UPLOADS_SCHEMA}.table_registry (
          table_name TEXT PRIMARY KEY,
          friendly_name TEXT NULL,
          session_id TEXT NULL,
          folder_id TEXT NULL,
          is_protected BOOLEAN NOT NULL DEFAULT TRUE,
          created_by TEXT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
        with self.engine.begin() as conn:
            for stmt in ddl.split(";"):
                sql = stmt.strip()
                if sql:
                    conn.execute(text(sql))

    def register_table(
        self,
        table_name: str,
        *,
        friendly_name: str | None,
        session_id: str | None,
        folder_id: str | None,
        is_protected: bool,
        created_by: str | None = None,
    ) -> bool:
        q = text(
            f"""
            INSERT INTO {UPLOADS_SCHEMA}.table_registry
                (table_name, friendly_name, session_id, folder_id, is_protected, created_by)
            VALUES
                (:table_name, :friendly_name, :session_id, :folder_id, :is_protected, :created_by)
            ON CONFLICT (table_name) DO UPDATE SET
                friendly_name = EXCLUDED.friendly_name,
                session_id = EXCLUDED.session_id,
                folder_id = EXCLUDED.folder_id,
                is_protected = EXCLUDED.is_protected,
                created_by = EXCLUDED.created_by
            """
        )
        with self.engine.begin() as conn:
            conn.execute(
                q,
                {
                    "table_name": table_name.lower(),
                    "friendly_name": friendly_name.lower() if friendly_name else None,
                    "session_id": session_id,
                    "folder_id": folder_id,
                    "is_protected": is_protected,
                    "created_by": created_by,
                },
            )
        return True

    def unregister_table(self, table_name: str) -> None:
        q = text(f"DELETE FROM {UPLOADS_SCHEMA}.table_registry WHERE table_name = :table_name")
        with self.engine.begin() as conn:
            conn.execute(q, {"table_name": table_name.lower()})

    def get_table_info(self, table_name: str) -> dict | None:
        q = text(
            f"""
            SELECT table_name, friendly_name, session_id, folder_id, is_protected
            FROM {UPLOADS_SCHEMA}.table_registry
            WHERE table_name = :table_name
            """
        )
        with self.engine.begin() as conn:
            row = conn.execute(q, {"table_name": table_name.lower()}).mappings().first()
        return dict(row) if row else None

    def can_modify_table(
        self, table_name: str, session_id: str | None = None, folder_id: str | None = None
    ) -> tuple[bool, str]:
        info = self.get_table_info(table_name)
        if not info:
            return False, f"Table '{table_name}' not found in registry"
        if info["is_protected"]:
            return False, "Table is protected (uploaded source table)"
        if session_id and info.get("session_id") and info["session_id"] != session_id:
            return False, "Table belongs to a different session"
        if folder_id and info.get("folder_id") and info["folder_id"] != folder_id:
            return False, "Table belongs to a different folder"
        return True, "OK"

    def get_agent_created_tables(self, folder_id: str | None) -> dict[str, str]:
        q = text(
            f"""
            SELECT COALESCE(friendly_name, table_name) AS friendly_name, table_name
            FROM {UPLOADS_SCHEMA}.table_registry
            WHERE is_protected = FALSE
              AND (:folder_id IS NULL OR folder_id = :folder_id)
            ORDER BY created_at DESC
            """
        )
        with self.engine.begin() as conn:
            rows = conn.execute(q, {"folder_id": folder_id}).all()
        return {r[0]: r[1] for r in rows}

    def is_protected(self, table_name: str) -> bool:
        info = self.get_table_info(table_name)
        return bool(info["is_protected"]) if info else True


_registry: TableRegistry | None = None


def get_table_registry() -> TableRegistry:
    global _registry
    if _registry is None:
        _registry = TableRegistry()
    return _registry


def register_agent_table(
    table_name: str,
    session_id: str | None = None,
    folder_id: str | None = None,
    agent_name: str | None = None,
    friendly_name: str | None = None,
) -> bool:
    return get_table_registry().register_table(
        table_name,
        friendly_name=friendly_name,
        session_id=session_id,
        folder_id=folder_id,
        is_protected=False,
        created_by=agent_name or "agent",
    )


def can_drop_table(
    table_name: str, session_id: str | None = None, folder_id: str | None = None
) -> tuple[bool, str]:
    return get_table_registry().can_modify_table(table_name, session_id=session_id, folder_id=folder_id)


def is_table_protected(table_name: str) -> bool:
    return get_table_registry().is_protected(table_name)
