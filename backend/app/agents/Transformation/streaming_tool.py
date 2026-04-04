"""
StreamingAgentTool - A custom AgentTool that streams inner agent events to a side-channel queue.

This enables hierarchical streaming where inner agent events (like SQL execution) 
are visible in the main event stream while maintaining Orchestrator control.

Based on DeepWiki documentation for google/adk-python:
- AgentTool.run_async signature: (self, *, args: dict[str, Any], tool_context: ToolContext) -> Any
- Runner.run_async requires: user_id, session_id, new_message (as types.Content)
- MUST call session_service.create_session() BEFORE runner.run_async()
- State propagation via event.actions.state_delta -> tool_context.state
"""

import asyncio
import logging
import uuid
from contextvars import ContextVar
from typing import Optional, Any

from google.adk.tools.agent_tool import AgentTool
from google.adk.tools import ToolContext
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

logger = logging.getLogger(__name__)

# ContextVar to hold the event queue for the current request
# This allows passing the queue through the async call stack without explicit parameter passing
stream_queue_var: ContextVar[Optional[asyncio.Queue]] = ContextVar("stream_queue", default=None)


class StreamingAgentTool(AgentTool):
    """
    An AgentTool that streams inner agent events to a side-channel queue
    if one is present in the context.
    
    The key insight from DeepWiki:
    - AgentTool.run_async is the method to override
    - MUST call session_service.create_session() before runner.run_async()
    - Runner.run_async requires user_id, session_id, and new_message as types.Content
    - Events have actions.state_delta that should be propagated to tool_context.state
    
    Performance: Runner and SessionService are cached per instance and reused.
    Retries are NOT done here — the outer transform_router already retries 503/429.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Cache runner + session service per instance to avoid re-creation per call
        self._cached_session_service: Optional[InMemorySessionService] = None
        self._cached_runner: Optional[Runner] = None
        self._app_name: Optional[str] = None

    def _get_or_create_runner(self) -> tuple:
        """Return cached (session_service, runner, app_name), creating if needed."""
        agent_name = self.agent.name if hasattr(self.agent, 'name') else 'UnknownAgent'
        app_name = f"streaming_{agent_name}"

        if self._cached_runner is not None and self._app_name == app_name:
            return self._cached_session_service, self._cached_runner, app_name

        svc = InMemorySessionService()
        runner = Runner(agent=self.agent, session_service=svc, app_name=app_name)
        self._cached_session_service = svc
        self._cached_runner = runner
        self._app_name = app_name
        return svc, runner, app_name

    async def run_async(
        self,
        *,
        args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Any:
        """
        Override run_async to intercept inner agent events and stream them.
        
        This method follows the same pattern as AgentTool but adds event streaming.
        """
        # Get the queue from context
        queue = stream_queue_var.get()
        
        agent_name = self.agent.name if hasattr(self.agent, 'name') else 'UnknownAgent'
        logger.info(f"StreamingAgentTool.run_async called for agent '{agent_name}'. Queue present: {queue is not None}")

        # If no queue is present, delegate to standard AgentTool behavior
        if queue is None:
            return await super().run_async(args=args, tool_context=tool_context)

        # Extract the request/prompt from args
        # AgentTool typically expects 'request' key in args
        raw_request = args.get('request') or args.get('prompt') or args
        
        # Handle case where request is a dict (LLM may pass {'query': '...'} instead of string)
        if isinstance(raw_request, dict):
            # Try to extract the actual query string
            request_text = (
                raw_request.get('query') or 
                raw_request.get('request') or 
                raw_request.get('prompt') or 
                raw_request.get('text') or
                str(raw_request)  # Fallback to string representation
            )
            # Final safety: ensure it's a string (nested dict case)
            if not isinstance(request_text, str):
                import json
                request_text = json.dumps(request_text) if isinstance(request_text, (dict, list)) else str(request_text)
        else:
            request_text = str(raw_request)
        
        logger.info(f"StreamingAgentTool executing inner agent '{agent_name}' with request: {request_text[:100]}...")

        # Reuse cached runner + session service (avoids re-creation per call)
        session_service, runner, app_name = self._get_or_create_runner()

        # Create new_message as types.Content (IMPORTANT: not a plain string!)
        new_message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=request_text)]
        )
        
        last_content = ""
        event_count = 0
        
        # Inherit state from parent tool_context to preserve folder_id
        # This ensures inner agents see the same folder context (for table filtering)
        try:
            if tool_context.state:
                if hasattr(tool_context.state, 'to_dict'):
                    parent_state = tool_context.state.to_dict()
                elif isinstance(tool_context.state, dict):
                    parent_state = dict(tool_context.state)
                else:
                    parent_state = {k: v for k, v in tool_context.state.items()}
            else:
                parent_state = {}
            logger.info(f"Inheriting parent state for inner agent: folder_id={parent_state.get('folder_id', 'NOT SET')}, keys={list(parent_state.keys())[:5]}")
        except Exception as state_err:
            logger.warning(f"Failed to inherit parent state: {state_err}. Using empty state.")
            parent_state = {}

        # Single attempt — retries are handled by the outer transform_router layer
        # to avoid double-retry latency (inner 3×3s + outer 3×3s = 18s wasted).
        try:
            user_id = f"streaming_user_{uuid.uuid4().hex[:8]}"
            session_id = f"streaming_session_{uuid.uuid4().hex[:8]}"
            
            # CRITICAL: Must create session BEFORE calling runner.run_async()
            await session_service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                state=parent_state
            )
            logger.info(f"Created session {session_id} for inner agent '{agent_name}' with inherited state")
            
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=new_message,
            ):
                event_count += 1
                
                # Push ALL events to side-channel queue for hierarchical streaming
                try:
                    await queue.put(event)
                except Exception as q_err:
                    logger.warning(f"Failed to push event to queue: {q_err}")
                
                # Propagate state changes from inner agent to parent context
                if hasattr(event, 'actions') and event.actions:
                    if hasattr(event.actions, 'state_delta') and event.actions.state_delta:
                        for key, value in event.actions.state_delta.items():
                            if not key.startswith('_') and not key.startswith('adk:'):
                                tool_context.state[key] = value
                
                # Capture the final response text
                if event.content and hasattr(event.content, 'parts') and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, 'text') and part.text:
                            if not getattr(part, 'thought', False):
                                last_content = part.text
            
            logger.info(f"StreamingAgentTool finished. Processed {event_count} events from '{agent_name}'")
            return last_content if last_content else f"Agent {agent_name} completed the task."
            
        except Exception as e:
            logger.error(
                f"Error in StreamingAgentTool for '{agent_name}': {e}",
                exc_info=True
            )
            # Let the error propagate so the outer retry layer handles it
            raise
