import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.services.table_service import (
    clean_table_with_report,
    is_table_in_session,
    profile_table,
    preview_table,
    table_as_csv_bytes,
)


router = APIRouter(prefix="/api/v1/tables", tags=["tables"])


def _assert_session_table_access(db: Session, user: dict, session_id: str, table_name: str) -> None:
    owns_session = db.execute(
        text(
            f"""
        SELECT 1 FROM {settings.app_schema}.chat_sessions
        WHERE id = :session_id AND user_id = :user_id
        """
        ),
        {"session_id": session_id, "user_id": user["id"]},
    ).first()
    if not owns_session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not is_table_in_session(db, session_id, table_name):
        raise HTTPException(status_code=403, detail="Table does not belong to this session")


@router.get("/{table_name}/preview")
def get_preview(
    table_name: str,
    session_id: str = Query(...),
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _assert_session_table_access(db, user, session_id, table_name)
    return preview_table(table_name, page=page, limit=limit)


@router.get("/{table_name}/download")
def download_table_csv(
    table_name: str,
    session_id: str = Query(...),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _assert_session_table_access(db, user, session_id, table_name)
    content = table_as_csv_bytes(table_name)
    filename = f"{table_name}.csv"
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{table_name}/quality")
def get_table_quality(
    table_name: str,
    session_id: str = Query(...),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _assert_session_table_access(db, user, session_id, table_name)
    return profile_table(table_name)


@router.post("/{table_name}/clean")
def clean_table(
    table_name: str,
    session_id: str = Query(...),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _assert_session_table_access(db, user, session_id, table_name)
    return clean_table_with_report(table_name, session_id, db)
