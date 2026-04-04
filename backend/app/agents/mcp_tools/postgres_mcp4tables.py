import asyncio
import json
import logging
import os
import re
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import Error, pool
from mcp.server import Server
from mcp.types import TextContent, Tool

from app.database.table_registry import can_drop_table, register_agent_table

logger = logging.getLogger("mvp_postgres_mcp")
logging.basicConfig(level=os.getenv("MCP_LOG_LEVEL", "WARNING").upper())

app = Server("mvp_postgres_mcp")

_POOL: Optional[pool.SimpleConnectionPool] = None

_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CREATE_TABLE_PATTERN = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:(?:\"?([A-Za-z0-9_]+)\"?)\.)?(?:\"?([A-Za-z0-9_]+)\"?)",
    re.IGNORECASE,
)
_DROP_TABLE_PATTERN = re.compile(
    r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:(?:\"?([A-Za-z0-9_]+)\"?)\.)?(?:\"?([A-Za-z0-9_]+)\"?)",
    re.IGNORECASE,
)


def get_db_config() -> dict:
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "imsuperuser"),
        "database": os.getenv("POSTGRES_DBNAME", "mvp"),
        "app_schema": os.getenv("APP_SCHEMA", "mvp"),
        "uploads_schema": os.getenv("UPLOADS_SCHEMA", "uploads"),
        "session_id": os.getenv("MCP_SESSION_ID"),
        "folder_id": os.getenv("MCP_FOLDER_ID"),
    }


def _get_pool() -> pool.SimpleConnectionPool:
    global _POOL
    if _POOL is None:
        cfg = get_db_config()
        _POOL = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            host=cfg["host"],
            port=cfg["port"],
            user=cfg["user"],
            password=cfg["password"],
            dbname=cfg["database"],
            connect_timeout=15,
        )
    return _POOL


@contextmanager
def get_connection():
    p = _get_pool()
    conn = p.getconn()
    try:
        yield conn
    finally:
        p.putconn(conn)


def _table_registry_exists(cursor, uploads_schema: str) -> bool:
    cursor.execute("SELECT to_regclass(%s)", (f"{uploads_schema}.table_registry",))
    return bool(cursor.fetchone()[0])


def _session_tables_with_roles(
    cursor,
    app_schema: str,
    uploads_schema: str,
    session_id: Optional[str],
) -> list[tuple[str, str]]:
    if not session_id:
        return []

    merged: dict[str, str] = {}

    cursor.execute(
        f"""
        SELECT table_name, table_role
        FROM {app_schema}.session_tables
        WHERE session_id = %s
        """,
        (session_id,),
    )
    for row in cursor.fetchall():
        table_name = row[0] if row else ""
        table_role = row[1] if row and row[1] else "unknown"
        if table_name:
            merged[table_name.lower()] = table_role

    try:
        if _table_registry_exists(cursor, uploads_schema):
            cursor.execute(
                f"""
                SELECT table_name,
                       CASE WHEN is_protected THEN 'uploaded' ELSE 'cleaned' END AS table_role
                FROM {uploads_schema}.table_registry
                WHERE session_id = %s
                """,
                (session_id,),
            )
            for row in cursor.fetchall():
                table_name = row[0] if row else ""
                table_role = row[1] if row and row[1] else "unknown"
                if table_name and table_name.lower() not in merged:
                    merged[table_name.lower()] = table_role
    except Exception as e:
        logger.warning("session table merge with registry failed: %s", e)

    return sorted((table_name, table_role) for table_name, table_role in merged.items())


def _allowed_tables_for_session(
    cursor,
    app_schema: str,
    uploads_schema: str,
    session_id: Optional[str],
) -> set[str]:
    return {
        table_name
        for table_name, _ in _session_tables_with_roles(
            cursor, app_schema, uploads_schema, session_id
        )
    }


# System schemas that must never be accessible
_BLOCKED_SCHEMAS = frozenset({
    "pg_catalog", "pg_toast",
    "pg_temp", "pg_internal", "cardinal_number",
})

# SQL patterns considered safe read-only with no table access
_SAFE_NO_TABLE_RE = re.compile(
    r"^\s*select\s+(now|current_timestamp|current_date|version|pg_postmaster_start_time)\s*\(\)",
    re.IGNORECASE,
)


def _extract_table_candidates(query: str) -> set[str]:
    """
    Extract bare table names from a SQL query.

    Handles:
    - Unqualified:  FROM tablename
    - Schema-qual:  FROM schema.tablename  (strips schema, returns tablename)
    - Double-quoted identifiers are lowercased
    """
    q = query.lower()
    # Match optional schema prefix: (schema.)?table
    ident = r'(?:[a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)'
    patterns = [
        rf"\bfrom\s+{ident}",
        rf"\bjoin\s+{ident}",
        rf"\bupdate\s+{ident}",
        rf"\binto\s+{ident}",
        rf"\btable\s+(?:if\s+not\s+exists\s+)?{ident}",
    ]
    out: set[str] = set()
    for p in patterns:
        for m in re.finditer(p, q):
            out.add(m.group(1))
    return out


def _references_blocked_schema(query: str) -> str | None:
    """
    Return the first blocked schema name if the query references one, else None.
    Checks for schema.object patterns.
    """
    q = query.lower()
    for schema in _BLOCKED_SCHEMAS:
        if re.search(rf"\b{re.escape(schema)}\s*\.", q):
            return schema
    return None


def _has_multiple_statements(query: str) -> bool:
    return ";" in query.strip().rstrip(";")


@app.list_tools()
async def list_tools() -> list[Tool]:
    cfg = get_db_config()
    uploads_schema = cfg["uploads_schema"]
    app_schema = cfg["app_schema"]
    session_id = cfg["session_id"]

    # Resolve the actual tables available for this session so the LLM
    # knows them upfront — no need to discover via information_schema.
    session_tables_list: list[str] = []
    if session_id:
        try:
            with get_connection() as conn:
                with conn.cursor() as cursor:
                    table_rows = _session_tables_with_roles(
                        cursor, app_schema, uploads_schema, session_id
                    )
                    for table_name, table_role in table_rows:
                        session_tables_list.append(f"{table_name} [role={table_role}]")
        except Exception as e:
            logger.warning("list_tools: could not fetch session tables: %s", e)

    if session_tables_list:
        tables_block = "\n".join(f"  - {t}" for t in session_tables_list)
        desc = (
            f"Execute SQL on the PostgreSQL database.\n\n"
            f"IMPORTANT: You MUST use ONLY the following tables for this session "
            f"(session_id={session_id}). Do NOT query information_schema or any "
            f"other tables — they will be rejected.\n\n"
            f"Available tables:\n{tables_block}\n\n"
            f"Use table names DIRECTLY without any schema prefix (e.g. SELECT * FROM table_name).\n"
            f"To see column info, query 'information_schema.columns' but you MUST include "
            f"'WHERE table_schema = current_schema()' to prevent access to other sessions."
        )
    elif session_id:
        desc = (
            f"Execute SQL on the PostgreSQL database. "
            f"No tables have been uploaded to session {session_id} yet. "
            f"Ask the user to upload a file first.\n"
            f"To see column info, query 'information_schema.columns' but you MUST include "
            f"'WHERE table_schema = current_schema()'."
        )
    else:
        desc = f"Execute SQL on the PostgreSQL database."

    return [
        Tool(
            name="execute_sql",
            description=desc,
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            f"Single SQL SELECT/INSERT/UPDATE/CREATE TABLE statement. "
                            f"Use bare table names without any schema prefix."
                        ),
                    }
                },
                "required": ["query"],
            },
        )
    ]



@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "execute_sql":
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    query = (arguments or {}).get("query")
    if not query or not isinstance(query, str):
        return [TextContent(type="text", text="Error: query is required")]

    if _has_multiple_statements(query):
        return [TextContent(type="text", text="Error: multiple SQL statements are not allowed")]

    # ── Block any access to system schemas immediately ──────────────────────
    blocked_schema = _references_blocked_schema(query)
    if blocked_schema:
        return [TextContent(
            type="text",
            text=(
                f"Error: access to system schema '{blocked_schema}' is not allowed. "
                f"Use only the tables listed in the tool description for this session."
            ),
        )]

    cfg = get_db_config()
    app_schema = cfg["app_schema"]
    uploads_schema = cfg["uploads_schema"]
    session_id = cfg["session_id"]
    folder_id = cfg["folder_id"]

    # ── information_schema isolation enforcement ────────────────────────────
    # Allow metadata discovery (columns, types) but ONLY for the current session.
    if "information_schema" in query.lower():
        if "current_schema()" not in query.lower() and uploads_schema.lower() not in query.lower():
            return [TextContent(
                type="text",
                text=(
                    "Error: queries to 'information_schema' MUST include a filter for "
                    "your session schema to prevent data leakage. Use 'WHERE table_schema = current_schema()' "
                    f"or 'WHERE table_schema = '{uploads_schema}'."
                ),
            )]

    create_match = _CREATE_TABLE_PATTERN.search(query)
    drop_match = _DROP_TABLE_PATTERN.search(query)

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                # Narrow search_path so unqualified table names resolve only
                # within the uploads schema — no accidental cross-schema access.
                cursor.execute(
                    f'SET search_path TO "{uploads_schema}", pg_catalog'
                )

                # ── Session allowlist enforcement ───────────────────────────
                if session_id:
                    allowed = _allowed_tables_for_session(
                        cursor, app_schema, uploads_schema, session_id
                    )
                    referenced = _extract_table_candidates(query)

                    # Build the effective allowed set:
                    # existing session tables + the table being created (if CREATE TABLE)
                    allowed_dynamic = set(allowed)
                    if create_match:
                        created_name = create_match.group(2).lower()
                        allowed_dynamic.add(created_name)

                    if referenced:
                        # Every referenced table must be in the session's allowlist
                        unauthorized = referenced - allowed_dynamic
                        if unauthorized:
                            return [TextContent(
                                type="text",
                                text=(
                                    f"Error: the following tables are not part of your "
                                    f"session and cannot be accessed: "
                                    f"{', '.join(sorted(unauthorized))}. "
                                    f"Allowed tables: {', '.join(sorted(allowed_dynamic)) or 'none uploaded yet'}."
                                ),
                            )]
                    elif not create_match and not _SAFE_NO_TABLE_RE.match(query):
                        # Query references no table at all and isn't a known-safe
                        # constant expression — block it to prevent schema discovery.
                        return [TextContent(
                            type="text",
                            text=(
                                "Error: query does not reference any session table. "
                                "Use only the tables listed in the tool description."
                            ),
                        )]

                # ── DROP permission check ───────────────────────────────────
                if drop_match:
                    drop_name = drop_match.group(2)
                    can_drop, reason = can_drop_table(
                        drop_name, session_id=session_id, folder_id=folder_id
                    )
                    if not can_drop:
                        return [TextContent(
                            type="text",
                            text=f"Error: cannot drop table '{drop_name}' - {reason}",
                        )]

                cursor.execute(query)

                if cursor.description is not None:
                    # SELECT — return rows, no commit needed
                    columns = [d[0] for d in cursor.description]
                    rows = cursor.fetchall()
                    lines = [",".join(columns)]
                    lines.extend(
                        ",".join("" if v is None else str(v) for v in row)
                        for row in rows
                    )
                    return [TextContent(type="text", text="\n".join(lines))]

                # Mutation (INSERT/UPDATE/DELETE/CREATE/DROP) — commit
                conn.commit()

                if create_match and session_id:
                    table_name = create_match.group(2)
                    register_agent_table(
                        table_name=table_name,
                        session_id=session_id,
                        folder_id=folder_id,
                        agent_name="mvp_transformation_agent",
                        friendly_name=table_name,
                    )

                return [TextContent(type="text", text=f"OK. Rows affected: {cursor.rowcount}")]
    except Error as e:
        return [TextContent(type="text", text=f"Error: {e}")]


async def main() -> None:
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
