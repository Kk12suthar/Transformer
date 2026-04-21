from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import deps
from app.db import database
from app.main import app


@asynccontextmanager
async def _noop_lifespan(_: object) -> Iterator[None]:
    yield


@pytest.fixture
def test_app():
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        app.router.lifespan_context = original_lifespan


@pytest.fixture
def client_factory(test_app):
    @contextmanager
    def _factory(*, db_session=None, current_user=None) -> Iterator[TestClient]:
        if db_session is not None:
            test_app.dependency_overrides[database.get_db] = lambda: db_session
        if current_user is not None:
            test_app.dependency_overrides[deps.get_current_user] = lambda: current_user

        with TestClient(test_app) as client:
            yield client

        test_app.dependency_overrides.clear()

    return _factory
