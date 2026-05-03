from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from google.adk.events import Event
from google.adk.events.event_actions import EventActions
from google.genai import types

from app.agents.Transformation.streaming_tool import StreamingAgentTool, stream_queue_var


pytestmark = pytest.mark.eval


class DummyAgent:
    name = "EvalAgent"
    description = "Deterministic test agent"


class FakeSessionService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        state: dict[str, object],
    ) -> None:
        self.calls.append(
            {
                "app_name": app_name,
                "user_id": user_id,
                "session_id": session_id,
                "state": dict(state),
            }
        )


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run_async(self, *, user_id: str, session_id: str, new_message: types.Content):
        self.calls.append(
            {
                "user_id": user_id,
                "session_id": session_id,
                "new_message": new_message,
            }
        )
        yield Event(
            author="EvalAgent",
            actions=EventActions(stateDelta={"last_tool_result": "done"}),
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="Inner agent finished")],
            ),
        )


def test_streaming_agent_tool_eval_normalizes_request_and_propagates_state(monkeypatch) -> None:
    tool = StreamingAgentTool(agent=DummyAgent())
    fake_session_service = FakeSessionService()
    fake_runner = FakeRunner()

    monkeypatch.setattr(
        tool,
        "_get_or_create_runner",
        lambda: (fake_session_service, fake_runner, "streaming_EvalAgent"),
    )

    async def exercise() -> tuple[str, Event, list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        token = stream_queue_var.set(queue)
        try:
            tool_context = SimpleNamespace(
                state={"folder_id": "folder-789", "selected_model": "gemini-3.1-pro-preview"}
            )
            result = await tool.run_async(
                args={"request": {"query": "trim whitespace"}},
                tool_context=tool_context,
            )
            queued_event = queue.get_nowait()
            return (
                result,
                queued_event,
                fake_session_service.calls,
                fake_runner.calls,
                tool_context.state,
            )
        finally:
            stream_queue_var.reset(token)

    result, queued_event, session_calls, runner_calls, propagated_state = asyncio.run(exercise())

    assert result == "Inner agent finished"
    assert queued_event.author == "EvalAgent"
    assert session_calls[0]["state"]["folder_id"] == "folder-789"
    assert session_calls[0]["state"]["selected_model"] == "gemini-3.1-pro-preview"
    assert runner_calls[0]["new_message"].parts[0].text == "trim whitespace"
    assert propagated_state["last_tool_result"] == "done"
