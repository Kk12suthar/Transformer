from app.main import app


def test_health_endpoint_returns_ok(client_factory) -> None:
    with client_factory() as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert app.title == "MVP Data Cleaning API"
