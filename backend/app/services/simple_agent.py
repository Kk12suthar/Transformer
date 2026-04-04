import time
import uuid
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import engine
from app.services.table_service import sanitize_identifier


@dataclass
class TransformResult:
    summary: str
    table_name: str
    actions: list[str]
    source_table: str
    input_rows: int
    output_rows: int


class SimpleTransformationAgent:
    """
    Minimal agentic fallback for MVP.
    """

    def _find_latest_table(self, db: Session, session_id: str) -> str | None:
        q = text(
            f"""
            SELECT table_name
            FROM {settings.app_schema}.session_tables
            WHERE session_id = :session_id
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        row = db.execute(q, {"session_id": session_id}).first()
        return row[0] if row else None

    def _insert_table_metadata(
        self, db: Session, session_id: str, table_name: str, role: str = "cleaned"
    ) -> None:
        q = text(
            f"""
            INSERT INTO {settings.app_schema}.session_tables
                (id, session_id, table_name, table_role, source_file_id)
            VALUES
                (:id, :session_id, :table_name, :table_role, NULL)
            """
        )
        db.execute(
            q,
            {
                "id": uuid.uuid4().hex,
                "session_id": session_id,
                "table_name": table_name,
                "table_role": role,
            },
        )

    def run(self, db: Session, session_id: str, query: str) -> TransformResult:
        source_table = self._find_latest_table(db, session_id)
        if not source_table:
            raise ValueError("No uploaded table found for this session")

        df = pd.read_sql_query(
            f'SELECT * FROM "{settings.uploads_schema}"."{source_table}"',
            engine,
        )
        input_rows = len(df)
        q = query.lower()
        actions: list[str] = []

        # 1. normalize string columns
        if "trim" in q or "whitespace" in q:
            obj_cols = df.select_dtypes(include=["object"]).columns
            for c in obj_cols:
                df[c] = df[c].astype("string").str.strip()
            actions.append("trim_whitespace")

        if "lower" in q:
            obj_cols = df.select_dtypes(include=["object", "string"]).columns
            for c in obj_cols:
                df[c] = df[c].astype("string").str.lower()
            actions.append("lowercase_strings")

        # 2. null handling
        if "drop null" in q or "remove null" in q or "dropna" in q:
            before = len(df)
            df = df.dropna()
            if len(df) != before:
                actions.append("drop_null_rows")

        if "fill null" in q or "fillna" in q:
            df = df.fillna("")
            actions.append("fill_null_with_empty")

        # 3. deduplication
        if "dedup" in q or "duplicate" in q:
            before = len(df)
            df = df.drop_duplicates()
            if len(df) != before:
                actions.append("drop_duplicates")

        if not actions:
            actions.append("no_explicit_cleaning_rule_matched")

        target_table = sanitize_identifier(
            f"cleaned_{session_id.replace('-', '')[:8]}_{int(time.time())}"
        )
        df.to_sql(
            target_table,
            engine,
            schema=settings.uploads_schema,
            if_exists="replace",
            index=False,
            method="multi",
            chunksize=1000,
        )

        self._insert_table_metadata(db, session_id, target_table, "cleaned")
        output_rows = len(df)

        summary = (
            f"Created cleaned table '{target_table}' from '{source_table}'. "
            f"Applied: {', '.join(actions)}. Rows: {input_rows} -> {output_rows}."
        )
        return TransformResult(
            summary=summary,
            table_name=target_table,
            actions=actions,
            source_table=source_table,
            input_rows=input_rows,
            output_rows=output_rows,
        )
