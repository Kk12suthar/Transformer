import io
import os
import uuid
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db, engine
from app.schemas.upload import UploadResponse
from app.services.table_service import sanitize_identifier


router = APIRouter(prefix="/api/v1/upload", tags=["upload"])


def _read_dataframe(upload: UploadFile, file_bytes: bytes) -> pd.DataFrame:
    ext = Path(upload.filename or "").suffix.lower()
    if ext == ".csv":
        return pd.read_csv(io.BytesIO(file_bytes))
    if ext in {".xlsx", ".xls"}:
        return pd.read_excel(io.BytesIO(file_bytes))
    raise HTTPException(status_code=400, detail="Only CSV/XLSX/XLS files are supported")


@router.post("/files", response_model=UploadResponse)
async def upload_file(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> UploadResponse:
    session_q = text(
        f"""
        SELECT id
        FROM {settings.app_schema}.chat_sessions
        WHERE id = :session_id AND user_id = :user_id
        """
    )
    if not db.execute(
        session_q, {"session_id": session_id, "user_id": user["id"]}
    ).first():
        raise HTTPException(status_code=404, detail="Session not found")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(file_bytes) > settings.upload_max_bytes:
        max_mb = settings.upload_max_bytes / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"File is too large for prototype mode. Upload files up to {max_mb:.0f} MB.",
        )

    df = _read_dataframe(file, file_bytes)
    if df.empty:
        raise HTTPException(status_code=400, detail="Uploaded file has no rows")
    if len(df) > settings.upload_max_rows:
        raise HTTPException(
            status_code=413,
            detail=f"File has too many rows for prototype mode. Upload up to {settings.upload_max_rows:,} rows.",
        )

    base_name = sanitize_identifier(Path(file.filename or "table").stem)
    table_name = sanitize_identifier(f"{base_name}_{uuid.uuid4().hex[:8]}")

    df.columns = [sanitize_identifier(str(c)) for c in df.columns]
    df.to_sql(
        table_name,
        engine,
        schema=settings.uploads_schema,
        if_exists="replace",
        index=False,
        method="multi",
        chunksize=1000,
    )

    file_id = uuid.uuid4().hex
    insert_file_q = text(
        f"""
        INSERT INTO {settings.app_schema}.uploaded_files
            (id, session_id, original_name, stored_name, table_name)
        VALUES
            (:id, :session_id, :original_name, :stored_name, :table_name)
        """
    )
    db.execute(
        insert_file_q,
        {
            "id": file_id,
            "session_id": session_id,
            "original_name": file.filename or "upload",
            "stored_name": f"{file_id}_{file.filename or 'upload'}",
            "table_name": table_name,
        },
    )

    insert_table_q = text(
        f"""
        INSERT INTO {settings.app_schema}.session_tables
            (id, session_id, table_name, table_role, source_file_id)
        VALUES
            (:id, :session_id, :table_name, 'uploaded', :source_file_id)
        """
    )
    db.execute(
        insert_table_q,
        {
            "id": uuid.uuid4().hex,
            "session_id": session_id,
            "table_name": table_name,
            "source_file_id": file_id,
        },
    )

    touch_q = text(
        f"""
        UPDATE {settings.app_schema}.chat_sessions
        SET updated_at = NOW()
        WHERE id = :session_id
        """
    )
    db.execute(touch_q, {"session_id": session_id})
    db.commit()

    return UploadResponse(
        success=True,
        session_id=session_id,
        file_id=file_id,
        table_name=table_name,
        message=f"Uploaded and created table {settings.uploads_schema}.{table_name}",
    )
