import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agents.Transformation.agent import create_transformation_agents
from app.agents.Transformation.streaming_tool import stream_queue_var
from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.schemas.transform import ActivateRequest, TransformRequest
from app.services.lock_manager import lock_manager
from app.services.model_config_service import (
    build_runtime_agent_config,
    consume_free_message_if_needed,
    get_chat_model_config,
    get_free_message_quota,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/adk-api/transform", tags=["transform"])

# ── ADK session service singleton ────────────────────────────────────────────
# Created once, shared across all requests so conversation history persists
# across turns for the lifetime of the API process.
_adk_session_service: InMemorySessionService | None = None


def _get_adk_session_service() -> InMemorySessionService:
    global _adk_session_service
    if _adk_session_service is None:
        _adk_session_service = InMemorySessionService()
    return _adk_session_service


def _resource_id(session_id: str, folder_id: str | None) -> str:
    return folder_id or session_id


def _as_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool, list, dict)):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return str(value)


def _to_sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _extract_text(event: Event) -> str:
    if not event.content or not event.content.parts:
        return ""
    chunks: list[str] = []
    for part in event.content.parts:
        if getattr(part, "thought", False):
            continue
        text_value = getattr(part, "text", "")
        if text_value:
            chunks.append(text_value)
    return "\n".join(chunks).strip()


def _event_payloads(event: Event) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    now = _as_timestamp()
    agent_name = event.author or "agent"

    if event.error_message:
        payloads.append(
            {
                "type": "error",
                "message": event.error_message,
                "code": event.error_code,
                "agent_name": agent_name,
                "timestamp": now,
            }
        )

    for function_call in event.get_function_calls():
        payloads.append(
            {
                "type": "function_request",
                "tool_name": function_call.name or "tool",
                "tool_args": _json_safe(function_call.args or {}),
                "agent_name": agent_name,
                "timestamp": now,
            }
        )

    for function_response in event.get_function_responses():
        payloads.append(
            {
                "type": "function_response",
                "tool_name": function_response.name or "tool",
                "response": _json_safe(function_response.response),
                "agent_name": agent_name,
                "timestamp": now,
            }
        )

    text_value = _extract_text(event)
    if text_value:
        payloads.append(
            {
                "type": "final_response" if event.is_final_response() else "agent_thinking",
                "text": text_value,
                "agent_name": agent_name,
                "timestamp": now,
            }
        )

    return payloads
def _insert_chat_message(db: Session, session_id: str, role: str, content: str) -> None:
    db.execute(
        text(
            f"""
            INSERT INTO {settings.app_schema}.chat_messages (id, session_id, role, content)
            VALUES (:id, :session_id, :role, :content)
            """
        ),
        {
            "id": uuid.uuid4().hex,
            "session_id": session_id,
            "role": role,
            "content": content,
        },
    )


def _touch_session(db: Session, session_id: str) -> None:
    db.execute(
        text(
            f"""
            UPDATE {settings.app_schema}.chat_sessions
            SET updated_at = NOW()
            WHERE id = :session_id
            """
        ),
        {"session_id": session_id},
    )


def _sync_agent_tables_into_session(db: Session, session_id: str) -> list[str]:
    existing_rows = db.execute(
        text(
            f"""
            SELECT table_name
            FROM {settings.app_schema}.session_tables
            WHERE session_id = :session_id
            """
        ),
        {"session_id": session_id},
    ).all()
    existing = {row[0] for row in existing_rows if row and row[0]}

    relation_name = f"{settings.uploads_schema}.table_registry"
    relation_exists = db.execute(
        text("SELECT to_regclass(:relation_name)"),
        {"relation_name": relation_name},
    ).scalar()
    if not relation_exists:
        return []

    registry_rows = db.execute(
        text(
            f"""
            SELECT table_name
            FROM {settings.uploads_schema}.table_registry
            WHERE session_id = :session_id
              AND is_protected = FALSE
            ORDER BY created_at DESC
            """
        ),
        {"session_id": session_id},
    ).all()

    new_tables: list[str] = []
    for row in registry_rows:
        table_name = row[0] if row else ""
        if not table_name or table_name in existing:
            continue
        db.execute(
            text(
                f"""
                INSERT INTO {settings.app_schema}.session_tables
                    (id, session_id, table_name, table_role, source_file_id)
                VALUES
                    (:id, :session_id, :table_name, 'cleaned', NULL)
                """
            ),
            {
                "id": uuid.uuid4().hex,
                "session_id": session_id,
                "table_name": table_name,
            },
        )
        existing.add(table_name)
        new_tables.append(table_name)
    return new_tables


def _get_session_table_context(db: Session, session_id: str) -> list[dict[str, str]]:
    context: dict[str, dict[str, str]] = {}

    session_rows = db.execute(
        text(
            f"""
            SELECT table_name, table_role
            FROM {settings.app_schema}.session_tables
            WHERE session_id = :session_id
            ORDER BY created_at ASC
            """
        ),
        {"session_id": session_id},
    ).all()
    for row in session_rows:
        table_name = row[0] if row else ""
        table_role = row[1] if row and row[1] else "unknown"
        if table_name:
            context[table_name] = {
                "table_name": table_name,
                "table_role": table_role,
            }

    relation_name = f"{settings.uploads_schema}.table_registry"
    relation_exists = db.execute(
        text("SELECT to_regclass(:relation_name)"),
        {"relation_name": relation_name},
    ).scalar()
    if relation_exists:
        registry_rows = db.execute(
            text(
                f"""
                SELECT table_name,
                       CASE WHEN is_protected THEN 'uploaded' ELSE 'cleaned' END AS table_role
                FROM {settings.uploads_schema}.table_registry
                WHERE session_id = :session_id
                ORDER BY created_at ASC
                """
            ),
            {"session_id": session_id},
        ).all()
        for row in registry_rows:
            table_name = row[0] if row else ""
            table_role = row[1] if row and row[1] else "unknown"
            if table_name and table_name not in context:
                context[table_name] = {
                    "table_name": table_name,
                    "table_role": table_role,
                }

    return list(context.values())


def _compose_query_with_session_context(
    *,
    query: str,
    session_id: str,
    folder_id: str | None,
    tables: list[dict[str, str]],
) -> str:
    lines = [
        "[SESSION_CONTEXT]",
        f"session_id={session_id}",
    ]
    if folder_id:
        lines.append(f"folder_id={folder_id}")

    if tables:
        lines.append("available_tables:")
        for table in tables:
            table_name = table.get("table_name", "").strip()
            table_role = table.get("table_role", "unknown").strip() or "unknown"
            if table_name:
                lines.append(f"- {table_name} (role={table_role})")
    else:
        lines.append("available_tables: none")

    lines.extend(
        [
            "[/SESSION_CONTEXT]",
            "",
            query,
        ]
    )
    return "\n".join(lines)


@router.post("/activate")
def activate_transform(
    payload: ActivateRequest,
    user: dict = Depends(get_current_user),
):
    resource_id = _resource_id(payload.session_id, payload.folder_id)
    ok, info = lock_manager.acquire(
        resource_id=resource_id,
        user_id=user["id"],
        username=user["email"],
        session_id=payload.session_id,
        activity_type="transform",
    )
    if not ok:
        return {
            "status": "denied",
            "folder_id": payload.folder_id,
            "owner_username": info["username"],
            "owner_user_id": info["user_id"],
            "is_mine": False,
            "message": f"{info['username']} is currently transforming this session",
        }

    return {
        "status": "ready",
        "session_id": payload.session_id,
        "owner_username": user["email"],
        "owner_user_id": user["id"],
        "message": "Transformation runner activated",
    }


@router.post("/heartbeat")
def transform_heartbeat(
    payload: dict,
    user: dict = Depends(get_current_user),
):
    session_id = payload.get("session_id")
    folder_id = payload.get("folder_id")
    if not session_id and not folder_id:
        raise HTTPException(status_code=400, detail="session_id or folder_id is required")
    resource_id = _resource_id(session_id or "", folder_id)
    success = lock_manager.refresh(resource_id, user["id"])
    return {"success": success}


@router.post("/deactivate")
def transform_deactivate(
    payload: dict,
    user: dict = Depends(get_current_user),
):
    session_id = payload.get("session_id")
    folder_id = payload.get("folder_id")
    if not session_id and not folder_id:
        raise HTTPException(status_code=400, detail="session_id or folder_id is required")
    resource_id = _resource_id(session_id or "", folder_id)
    success = lock_manager.release(resource_id, user["id"])
    return {"success": success}


@router.get("/runner-status")
def runner_status(
    session_id: str | None = None,
    folder_id: str | None = None,
    user: dict = Depends(get_current_user),
):
    if not session_id and not folder_id:
        raise HTTPException(status_code=400, detail="session_id or folder_id is required")
    resource_id = _resource_id(session_id or "", folder_id)
    info = lock_manager.status(resource_id)
    if not info:
        return {"active": False}
    return {
        "active": True,
        "owner_username": info["username"],
        "owner_user_id": info["user_id"],
        "is_mine": info["user_id"] == user["id"],
        "session_id": info["session_id"] if info["user_id"] == user["id"] else None,
    }


@router.post("/stream")
async def transform_stream(
    payload: TransformRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    resource_id = _resource_id(payload.session_id, payload.folder_id)
    owner = lock_manager.status(resource_id)
    if not owner:
        raise HTTPException(
            status_code=409,
            detail="No active transformation session. Please activate first.",
        )
    if owner["user_id"] != user["id"]:
        raise HTTPException(
            status_code=409,
            detail=f"{owner['username']} is currently transforming this session.",
        )

    session_check_q = text(
        f"""
        SELECT 1 FROM {settings.app_schema}.chat_sessions
        WHERE id = :session_id AND user_id = :user_id
        """
    )
    if not db.execute(
        session_check_q, {"session_id": payload.session_id, "user_id": user["id"]}
    ).first():
        raise HTTPException(status_code=404, detail="Session not found")

    chat_model_config = get_chat_model_config(db, user["id"])
    quota = get_free_message_quota(
        db=db,
        user_id=user["id"],
        config=chat_model_config,
        requested_model=payload.chat_model,
    )
    if quota.requires_api_key:
        raise HTTPException(
            status_code=403,
            detail=(
                "Free message limit reached. Add your own API key in Agent Panel "
                "to continue using the agent."
            ),
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        started = time.time()
        final_text = ""
        table_name = ""
        completion_success = True
        session_tables_context: list[dict[str, str]] = []
        side_queue: asyncio.Queue[Event | None] = asyncio.Queue()
        output_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        stream_token = stream_queue_var.set(side_queue)

        try:
            _insert_chat_message(db, payload.session_id, "user", payload.query)
            _sync_agent_tables_into_session(db, payload.session_id)
            session_tables_context = _get_session_table_context(db, payload.session_id)
            db.commit()

            now = _as_timestamp()
            yield _to_sse({"type": "status", "message": "Connected", "timestamp": now})
            yield _to_sse(
                {"type": "agent_start", "message": "Agent started", "timestamp": now}
            )

            app_name = settings.adk_app_name
            consumed_quota = consume_free_message_if_needed(
                db=db,
                user_id=user["id"],
                config=chat_model_config,
                requested_model=payload.chat_model,
            )
            if consumed_quota.requires_api_key:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Free message limit reached. Add your own API key in Agent Panel "
                        "to continue using the agent."
                    ),
                )
            runtime_agent_config = build_runtime_agent_config(
                config=chat_model_config,
                requested_model=payload.chat_model,
            )
            agent = create_transformation_agents(
                app_config=runtime_agent_config,
                folder_id=payload.folder_id,
                session_id=payload.session_id,
            )
            session_service = _get_adk_session_service()
            runner = Runner(agent=agent, session_service=session_service, app_name=app_name)

            runner_state = {"session_id": payload.session_id}
            if payload.folder_id:
                runner_state["folder_id"] = payload.folder_id
            runner_state["selected_model"] = runtime_agent_config.get("default_model")
            runner_state["available_tables"] = [
                table["table_name"] for table in session_tables_context if table.get("table_name")
            ]

            # Get-or-create: reuse existing ADK session so history is preserved
            # across every turn. Only create on the very first query.
            existing_adk_session = await session_service.get_session(
                app_name=app_name,
                user_id=str(user["id"]),
                session_id=payload.session_id,
            )
            if existing_adk_session is None:
                await session_service.create_session(
                    app_name=app_name,
                    user_id=str(user["id"]),
                    session_id=payload.session_id,
                    state=runner_state,
                )

            contextual_query = _compose_query_with_session_context(
                query=payload.query,
                session_id=payload.session_id,
                folder_id=payload.folder_id,
                tables=session_tables_context,
            )
            query_content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=contextual_query)],
            )

            async def produce_runner_events() -> None:
                try:
                    async for event in runner.run_async(
                        user_id=str(user["id"]),
                        session_id=payload.session_id,
                        new_message=query_content,
                        state_delta=runner_state,
                    ):
                        for stream_payload in _event_payloads(event):
                            await output_queue.put(stream_payload)
                except Exception as exc:
                    error_message = f"{type(exc).__name__}: {exc}"
                    logger.exception("Transformation stream failed inside runner: %s", error_message)
                    await output_queue.put(
                        {
                            "type": "error",
                            "message": error_message,
                            "timestamp": _as_timestamp(),
                        }
                    )
                finally:
                    await side_queue.put(None)

            async def forward_side_channel() -> None:
                try:
                    while True:
                        inner_event = await side_queue.get()
                        if inner_event is None:
                            break
                        for stream_payload in _event_payloads(inner_event):
                            await output_queue.put(stream_payload)
                finally:
                    await output_queue.put(None)

            runner_task = asyncio.create_task(produce_runner_events())
            side_task = asyncio.create_task(forward_side_channel())

            try:
                while True:
                    stream_payload = await output_queue.get()
                    if stream_payload is None:
                        break

                    if (
                        stream_payload.get("type") == "final_response"
                        and stream_payload.get("text")
                    ):
                        final_text = stream_payload["text"]
                    if stream_payload.get("type") == "error":
                        completion_success = False
                    yield _to_sse(stream_payload)
            finally:
                await runner_task
                await side_task

            if not completion_success:
                db.rollback()
                now = _as_timestamp()
                completion_payload = {
                    "type": "completion",
                    "success": False,
                    "final_output": "",
                    "time_taken": round(time.time() - started, 2),
                    "timestamp": now,
                }
                yield _to_sse(completion_payload)
                return

            created_tables = _sync_agent_tables_into_session(db, payload.session_id)
            if created_tables:
                table_name = created_tables[0]

            if final_text:
                _insert_chat_message(db, payload.session_id, "assistant", final_text)
            _touch_session(db, payload.session_id)
            db.commit()

            now = _as_timestamp()
            completion_payload = {
                "type": "completion",
                "success": True,
                "final_output": final_text,
                "time_taken": round(time.time() - started, 2),
                "created_tables": created_tables,
                "table_name": table_name,
                "timestamp": now,
            }
            yield _to_sse(completion_payload)
        except Exception as exc:
            completion_success = False
            db.rollback()
            now = _as_timestamp()
            error_message = f"{type(exc).__name__}: {exc}"
            logger.exception("Transformation stream failed: %s", error_message)
            yield _to_sse(
                {"type": "error", "message": error_message, "timestamp": now}
            )
            completion_payload = {
                "type": "completion",
                "success": False,
                "final_output": "",
                "error": error_message,
                "time_taken": round(time.time() - started, 2),
                "timestamp": now,
            }
            yield _to_sse(completion_payload)
        finally:
            stream_queue_var.reset(stream_token)
            if not completion_success:
                logger.debug("Transformation stream closed with error")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream",
        },
    )
