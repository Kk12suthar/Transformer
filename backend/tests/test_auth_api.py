from __future__ import annotations

from app.core.security import hash_password

from .fakes import RecordingSession


def test_signup_creates_user_and_returns_token(client_factory) -> None:
    def handler(sql: str, _: dict[str, str]):
        if "select 1 from mvp.users" in sql:
            return []
        if "insert into mvp.users" in sql:
            return None
        raise AssertionError(f"Unexpected query: {sql}")

    db = RecordingSession(handler)

    with client_factory(db_session=db) as client:
        response = client.post(
            "/api/v1/auth/signup",
            json={
                "email": "alice@example.com",
                "password": "supersecure123",
                "full_name": "Alice Example",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["message"] == "Account created"
    assert payload["user"]["email"] == "alice@example.com"
    assert payload["access_token"]
    assert db.commits == 1

    insert_call = next(call for call in db.executed if "insert into mvp.users" in call["sql"])
    assert insert_call["params"]["password_hash"] != "supersecure123"
    assert "$" in insert_call["params"]["password_hash"]


def test_signup_rejects_duplicate_email(client_factory) -> None:
    def handler(sql: str, _: dict[str, str]):
        if "select 1 from mvp.users" in sql:
            return [1]
        raise AssertionError(f"Unexpected query: {sql}")

    db = RecordingSession(handler)

    with client_factory(db_session=db) as client:
        response = client.post(
            "/api/v1/auth/signup",
            json={
                "email": "alice@example.com",
                "password": "supersecure123",
                "full_name": "Alice Example",
            },
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "Email already exists"}
    assert db.commits == 0


def test_signin_returns_token_for_valid_credentials(client_factory) -> None:
    stored_password_hash = hash_password("supersecure123")

    def handler(sql: str, _: dict[str, str]):
        if "from mvp.users" in sql and "where email = :email" in sql:
            return [
                {
                    "id": "user-123",
                    "email": "alice@example.com",
                    "full_name": "Alice Example",
                    "password_hash": stored_password_hash,
                }
            ]
        raise AssertionError(f"Unexpected query: {sql}")

    db = RecordingSession(handler)

    with client_factory(db_session=db) as client:
        response = client.post(
            "/api/v1/auth/signin",
            json={"email": "alice@example.com", "password": "supersecure123"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["message"] == "Signed in"
    assert payload["user"]["id"] == "user-123"
    assert payload["access_token"]
    assert db.commits == 0


def test_signin_rejects_invalid_password(client_factory) -> None:
    stored_password_hash = hash_password("different-password")

    def handler(sql: str, _: dict[str, str]):
        if "from mvp.users" in sql and "where email = :email" in sql:
            return [
                {
                    "id": "user-123",
                    "email": "alice@example.com",
                    "full_name": "Alice Example",
                    "password_hash": stored_password_hash,
                }
            ]
        raise AssertionError(f"Unexpected query: {sql}")

    db = RecordingSession(handler)

    with client_factory(db_session=db) as client:
        response = client.post(
            "/api/v1/auth/signin",
            json={"email": "alice@example.com", "password": "supersecure123"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}
