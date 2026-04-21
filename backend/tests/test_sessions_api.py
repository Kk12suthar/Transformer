from __future__ import annotations

from datetime import UTC, datetime

from .fakes import RecordingSession


TEST_USER = {
    "id": "user-123",
    "email": "alice@example.com",
    "full_name": "Alice Example",
}


def test_create_session_returns_new_session(client_factory) -> None:
    now = datetime.now(UTC)
    state: dict[str, object] = {}

    def handler(sql: str, params: dict[str, str]):
        if "insert into mvp.chat_sessions" in sql:
            state["row"] = {
                "id": params["id"],
                "user_id": params["user_id"],
                "title": params["title"],
                "status": "ACTIVE",
                "created_at": now,
                "updated_at": now,
            }
            return None
        if "from mvp.chat_sessions" in sql and "where id = :id" in sql:
            return [state["row"]]
        raise AssertionError(f"Unexpected query: {sql}")

    db = RecordingSession(handler)

    with client_factory(db_session=db, current_user=TEST_USER) as client:
        response = client.post("/api/v1/chat/sessions", json={"title": "March cleanup"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == TEST_USER["id"]
    assert payload["title"] == "March cleanup"
    assert payload["status"] == "ACTIVE"
    assert db.commits == 1


def test_list_sessions_returns_existing_sessions_for_current_user(client_factory) -> None:
    now = datetime.now(UTC)

    def handler(sql: str, params: dict[str, str]):
        if "from mvp.chat_sessions" in sql and "where user_id = :user_id" in sql:
            assert params["user_id"] == TEST_USER["id"]
            return [
                {
                    "id": "session-2",
                    "user_id": TEST_USER["id"],
                    "title": "Latest session",
                    "status": "ACTIVE",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "session-1",
                    "user_id": TEST_USER["id"],
                    "title": "Older session",
                    "status": "ACTIVE",
                    "created_at": now,
                    "updated_at": now,
                },
            ]
        raise AssertionError(f"Unexpected query: {sql}")

    db = RecordingSession(handler)

    with client_factory(db_session=db, current_user=TEST_USER) as client:
        response = client.get("/api/v1/chat/sessions")

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == ["session-2", "session-1"]
    assert payload[0]["title"] == "Latest session"


def test_delete_session_returns_not_found_for_unknown_session(client_factory) -> None:
    def handler(sql: str, _: dict[str, str]):
        if "select 1 from mvp.chat_sessions" in sql:
            return []
        raise AssertionError(f"Unexpected query: {sql}")

    db = RecordingSession(handler)

    with client_factory(db_session=db, current_user=TEST_USER) as client:
        response = client.delete("/api/v1/chat/sessions/missing-session")

    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}
    assert db.commits == 0
