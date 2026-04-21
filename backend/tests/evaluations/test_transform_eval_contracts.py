from __future__ import annotations

import json
from pathlib import Path

import pytest
from google.adk.events import Event
from google.genai import types

from app.api.transform import _compose_query_with_session_context, _event_payloads


pytestmark = pytest.mark.eval

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _strip_timestamps(payloads: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{k: v for k, v in payload.items() if k != "timestamp"} for payload in payloads]


def test_transform_event_trajectory_matches_expected_fixture() -> None:
    events = [
        Event(
            author="Orchestrator",
            content=types.Content(
                role="model",
                parts=[
                    types.Part.from_function_call(
                        name="postgres_db_toolset",
                        args={"sql": "SELECT * FROM uploads.customers"},
                    )
                ],
            ),
        ),
        Event(
            author="DataOperations",
            content=types.Content(
                role="tool",
                parts=[
                    types.Part.from_function_response(
                        name="postgres_db_toolset",
                        response={"status": "ok", "row_count": 42},
                    )
                ],
            ),
        ),
        Event(
            author="Analysis",
            partial=True,
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="Planning a trim + dedupe cleanup")],
            ),
        ),
        Event(
            author="Orchestrator",
            content=types.Content(
                role="model",
                parts=[
                    types.Part.from_text(
                        text="Created cleaned_customers and removed duplicates."
                    )
                ],
            ),
        ),
    ]

    actual_payloads = _strip_timestamps(
        [payload for event in events for payload in _event_payloads(event)]
    )
    expected_payloads = json.loads(
        (FIXTURES_DIR / "transform_event_trajectory.json").read_text(encoding="utf-8")
    )

    assert actual_payloads == expected_payloads


def test_transform_error_event_maps_to_frontend_error_shape() -> None:
    event = Event(author="Orchestrator", errorCode="tool_error", errorMessage="DB timeout")

    payloads = _event_payloads(event)

    assert len(payloads) == 1
    assert payloads[0]["type"] == "error"
    assert payloads[0]["code"] == "tool_error"
    assert payloads[0]["message"] == "DB timeout"
    assert payloads[0]["agent_name"] == "Orchestrator"


def test_query_context_eval_preserves_session_and_table_metadata() -> None:
    contextual_query = _compose_query_with_session_context(
        query="Trim whitespace and deduplicate the latest cleaned table.",
        session_id="session-123",
        folder_id="folder-456",
        tables=[
            {"table_name": "customers_raw", "table_role": "uploaded"},
            {"table_name": "customers_cleaned", "table_role": "cleaned"},
        ],
    )

    assert contextual_query == "\n".join(
        [
            "[SESSION_CONTEXT]",
            "session_id=session-123",
            "folder_id=folder-456",
            "available_tables:",
            "- customers_raw (role=uploaded)",
            "- customers_cleaned (role=cleaned)",
            "[/SESSION_CONTEXT]",
            "",
            "Trim whitespace and deduplicate the latest cleaned table.",
        ]
    )
