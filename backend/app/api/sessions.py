import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.schemas.session import SessionCreateRequest, SessionOut, SessionTableOut


router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


@router.post("/sessions", response_model=SessionOut)
def create_session(
    payload: SessionCreateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> SessionOut:
    session_id = uuid.uuid4().hex
    title = payload.title or f"Chat {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"
    insert_q = text(
        f"""
        INSERT INTO {settings.app_schema}.chat_sessions
            (id, user_id, title, status)
        VALUES
            (:id, :user_id, :title, 'ACTIVE')
        """
    )
    db.execute(insert_q, {"id": session_id, "user_id": user["id"], "title": title})
    db.commit()

    row_q = text(
        f"""
        SELECT id, user_id, title, status, created_at, updated_at
        FROM {settings.app_schema}.chat_sessions
        WHERE id = :id
        """
    )
    row = db.execute(row_q, {"id": session_id}).mappings().first()
    return SessionOut(**row)


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> list[SessionOut]:
    q = text(
        f"""
        SELECT id, user_id, title, status, created_at, updated_at
        FROM {settings.app_schema}.chat_sessions
        WHERE user_id = :user_id
        ORDER BY updated_at DESC
        """
    )
    rows = db.execute(q, {"user_id": user["id"]}).mappings().all()
    return [SessionOut(**r) for r in rows]


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> None:
    verify_q = text(
        f"""
        SELECT 1
        FROM {settings.app_schema}.chat_sessions
        WHERE id = :session_id AND user_id = :user_id
        """
    )
    if not db.execute(verify_q, {"session_id": session_id, "user_id": user["id"]}).first():
        raise HTTPException(status_code=404, detail="Session not found")

    # Delete child rows first (messages, tables), then the session
    for tbl in ("chat_messages", "session_tables"):
        db.execute(
            text(f"DELETE FROM {settings.app_schema}.{tbl} WHERE session_id = :sid"),
            {"sid": session_id},
        )
    db.execute(
        text(f"DELETE FROM {settings.app_schema}.chat_sessions WHERE id = :sid"),
        {"sid": session_id},
    )
    db.commit()


@router.get("/sessions/{session_id}/tables", response_model=list[SessionTableOut])
def list_session_tables(
    session_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> list[SessionTableOut]:
    verify_q = text(
        f"""
        SELECT 1
        FROM {settings.app_schema}.chat_sessions
        WHERE id = :session_id AND user_id = :user_id
        """
    )
    if not db.execute(
        verify_q, {"session_id": session_id, "user_id": user["id"]}
    ).first():
        raise HTTPException(status_code=404, detail="Session not found")

    q = text(
        f"""
        SELECT id, table_name, table_role, source_file_id, created_at
        FROM {settings.app_schema}.session_tables
        WHERE session_id = :session_id
        ORDER BY created_at DESC
        """
    )
    rows = db.execute(q, {"session_id": session_id}).mappings().all()
    return [SessionTableOut(**r) for r in rows]
