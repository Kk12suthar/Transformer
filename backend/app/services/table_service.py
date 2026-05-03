import io
import re
import uuid
import warnings

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import engine
from app.database.table_registry import register_agent_table


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


def _missing_mask(series: pd.Series) -> pd.Series:
    mask = series.isna()
    if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
        stripped = series.astype("string").str.strip()
        mask = mask | stripped.isna() | stripped.eq("")
    return mask


def _column_kind(series: pd.Series, missing: pd.Series) -> tuple[str, float]:
    non_missing = series[~missing]
    if non_missing.empty:
        return "empty", 0.0
    numeric_ratio = pd.to_numeric(non_missing, errors="coerce").notna().mean()
    if numeric_ratio >= 0.85:
        return "numeric", float(numeric_ratio)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        date_ratio = pd.to_datetime(non_missing, errors="coerce").notna().mean()
    if date_ratio >= 0.7:
        return "date", float(date_ratio)
    unique_ratio = non_missing.nunique(dropna=True) / max(len(non_missing), 1)
    if unique_ratio <= 0.2:
        return "category", float(1 - unique_ratio)
    return "text", float(unique_ratio)


def _safe_pct(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 2)


def profile_table(table_name: str, sample_limit: int = 5000) -> dict:
    table_name = assert_valid_identifier(table_name)
    sample_limit = max(100, min(sample_limit, 20000))

    with engine.begin() as conn:
        total_q = text(f'SELECT COUNT(*) FROM "{settings.uploads_schema}"."{table_name}"')
        total_rows = int(conn.execute(total_q).scalar() or 0)
        sample_q = text(
            f'SELECT * FROM "{settings.uploads_schema}"."{table_name}" LIMIT :limit'
        )
        df = pd.read_sql_query(sample_q, conn, params={"limit": sample_limit})

    sample_rows = len(df)
    columns = list(df.columns)
    total_cells = max(sample_rows * max(len(columns), 1), 1)
    column_profiles: list[dict] = []
    missing_cells = 0
    whitespace_cells = 0
    unnamed_count = 0
    constant_count = 0
    date_columns: list[str] = []
    numeric_columns: list[str] = []
    category_columns: list[str] = []
    candidate_id_columns: list[str] = []

    for column in columns:
        series = df[column]
        missing = _missing_mask(series)
        missing_count = int(missing.sum())
        non_missing_count = max(sample_rows - missing_count, 0)
        unique_count = int(series[~missing].nunique(dropna=True))
        kind, confidence = _column_kind(series, missing)
        unique_ratio = unique_count / max(non_missing_count, 1)

        if column.lower().startswith("unnamed") or not column.strip():
            unnamed_count += 1
        if non_missing_count and unique_count <= 1:
            constant_count += 1
        if kind == "date":
            date_columns.append(column)
        elif kind == "numeric":
            numeric_columns.append(column)
        elif kind == "category":
            category_columns.append(column)
        if non_missing_count and unique_ratio >= 0.98 and missing_count == 0:
            candidate_id_columns.append(column)

        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            as_text = series.astype("string")
            stripped = as_text.str.replace(r"\s+", " ", regex=True).str.strip()
            changed = (as_text.fillna("") != stripped.fillna("")).sum()
            whitespace_cells += int(changed)

        missing_cells += missing_count
        column_profiles.append(
            {
                "name": column,
                "kind": kind,
                "confidence": round(confidence, 2),
                "missing": missing_count,
                "missing_percent": _safe_pct(missing_count, sample_rows),
                "unique": unique_count,
                "unique_percent": _safe_pct(unique_count, max(non_missing_count, 1)),
            }
        )

    duplicate_rows = int(df.duplicated().sum()) if sample_rows else 0
    missing_pct = missing_cells / total_cells
    duplicate_pct = duplicate_rows / max(sample_rows, 1)
    whitespace_pct = whitespace_cells / total_cells
    score = round(
        100
        - (missing_pct * 35)
        - (duplicate_pct * 25)
        - (whitespace_pct * 10)
        - (unnamed_count * 3)
        - (constant_count * 2)
    )
    score = int(max(0, min(100, score)))

    cleaning_plan: list[dict[str, object]] = []
    if whitespace_cells:
        cleaning_plan.append(
            {
                "action": "Trim and normalize whitespace",
                "reason": f"{whitespace_cells:,} sampled text cells have leading, trailing, or repeated spaces.",
                "risk": "low",
            }
        )
    if missing_cells:
        cleaning_plan.append(
            {
                "action": "Standardize missing values",
                "reason": f"{missing_cells:,} sampled cells are blank or null.",
                "risk": "medium",
            }
        )
    if duplicate_rows:
        cleaning_plan.append(
            {
                "action": "Remove exact duplicate rows",
                "reason": f"{duplicate_rows:,} duplicate rows found in the sample.",
                "risk": "medium",
            }
        )
    if date_columns:
        cleaning_plan.append(
            {
                "action": "Validate date/time columns",
                "reason": f"Detected date-like columns: {', '.join(date_columns[:4])}.",
                "risk": "medium",
            }
        )
    if unnamed_count:
        cleaning_plan.append(
            {
                "action": "Rename unclear columns",
                "reason": f"{unnamed_count} column name looks generated or unclear.",
                "risk": "low",
            }
        )
    if not cleaning_plan:
        cleaning_plan.append(
            {
                "action": "No automatic cleanup needed",
                "reason": "The sampled rows look consistent enough for analysis.",
                "risk": "low",
            }
        )

    insights: list[dict[str, object]] = [
        {
            "label": "Shape",
            "value": f"{total_rows:,} rows x {len(columns):,} columns",
            "detail": f"Profile based on {sample_rows:,} sampled rows.",
        }
    ]
    if candidate_id_columns:
        insights.append(
            {
                "label": "Possible identifiers",
                "value": ", ".join(candidate_id_columns[:3]),
                "detail": "These columns are mostly unique and can be useful for record matching.",
            }
        )
    if date_columns:
        insights.append(
            {
                "label": "Date/time fields",
                "value": ", ".join(date_columns[:3]),
                "detail": "These can support recency, lifecycle, and duration analysis.",
            }
        )
    if numeric_columns:
        insights.append(
            {
                "label": "Measures",
                "value": ", ".join(numeric_columns[:3]),
                "detail": "These columns are candidates for totals, averages, outliers, or scoring.",
            }
        )
    if category_columns:
        insights.append(
            {
                "label": "Segments",
                "value": ", ".join(category_columns[:3]),
                "detail": "These columns are useful for grouping and filtering.",
            }
        )

    top_issues = sorted(
        column_profiles,
        key=lambda item: (item["missing"], item["unique_percent"] == 0),
        reverse=True,
    )[:5]

    return {
        "table_name": table_name,
        "score": score,
        "total_rows": total_rows,
        "sample_rows": sample_rows,
        "column_count": len(columns),
        "duplicate_rows_sample": duplicate_rows,
        "missing_cells_sample": int(missing_cells),
        "whitespace_cells_sample": int(whitespace_cells),
        "columns": column_profiles,
        "top_issues": top_issues,
        "cleaning_plan": cleaning_plan,
        "insights": insights,
        "retention_hours": settings.data_retention_hours,
    }


def clean_table_with_report(table_name: str, session_id: str, db: Session) -> dict:
    table_name = assert_valid_identifier(table_name)
    before = profile_table(table_name)

    df = pd.read_sql_query(
        f'SELECT * FROM "{settings.uploads_schema}"."{table_name}"',
        engine,
    )

    operations: list[dict[str, object]] = []
    whitespace_cells = 0
    blanks_standardized = 0
    for column in df.columns:
        series = df[column]
        if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
            continue
        as_text = series.astype("string")
        normalized = as_text.str.replace(r"\s+", " ", regex=True).str.strip()
        blank_mask = normalized.eq("")
        blanks_standardized += int(blank_mask.sum())
        normalized = normalized.mask(blank_mask, pd.NA)
        changed = int((as_text.fillna("") != normalized.fillna("")).sum())
        if changed:
            whitespace_cells += changed
            df[column] = normalized

    if whitespace_cells:
        operations.append(
            {
                "action": "Normalized text cells",
                "detail": f"Trimmed whitespace and standardized {whitespace_cells:,} text cells.",
            }
        )
    if blanks_standardized:
        operations.append(
            {
                "action": "Standardized blanks",
                "detail": f"Converted {blanks_standardized:,} blank strings to null values.",
            }
        )

    before_rows = len(df)
    df = df.drop_duplicates()
    duplicate_rows_removed = before_rows - len(df)
    if duplicate_rows_removed:
        operations.append(
            {
                "action": "Removed duplicates",
                "detail": f"Removed {duplicate_rows_removed:,} exact duplicate rows.",
            }
        )

    if not operations:
        operations.append(
            {
                "action": "Created clean copy",
                "detail": "No low-risk automatic changes were needed, so a clean copy was created.",
            }
        )

    target_table = sanitize_identifier(f"cleaned_{table_name[:28]}_{uuid.uuid4().hex[:8]}")
    df.to_sql(
        target_table,
        engine,
        schema=settings.uploads_schema,
        if_exists="replace",
        index=False,
        method="multi",
        chunksize=1000,
    )

    db.execute(
        text(
            f"""
            INSERT INTO {settings.app_schema}.session_tables
                (id, session_id, table_name, table_role, source_file_id)
            VALUES
                (:id, :session_id, :table_name, 'cleaned', NULL)
            """
        ),
        {"id": uuid.uuid4().hex, "session_id": session_id, "table_name": target_table},
    )
    db.commit()
    register_agent_table(
        target_table,
        session_id=session_id,
        friendly_name=target_table,
        agent_name="mvp_quality_cleaner",
    )

    after = profile_table(target_table)
    return {
        "source_table": table_name,
        "table_name": target_table,
        "before": before,
        "after": after,
        "operations": operations,
        "rows_before": before_rows,
        "rows_after": len(df),
        "rows_removed": duplicate_rows_removed,
        "score_delta": after["score"] - before["score"],
        "summary": (
            f"Created {target_table}. Quality score changed from "
            f"{before['score']} to {after['score']}."
        ),
    }
