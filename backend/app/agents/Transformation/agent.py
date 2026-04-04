"""
Transformation Agent - pipeline agents for data transformation operations.

MVP variant:
- no RAG
- no guardrails
- no model callbacks
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools import google_search
from google.adk.tools.mcp_tool.mcp_toolset import (
    MCPToolset,
    StdioConnectionParams,
    StdioServerParameters,
)

from app.agents.Transformation.streaming_tool import StreamingAgentTool
from app.utils.model_factory import create_model_from_config

from .prompts import (
    ANALYSIS_AGENT_INSTRUCTION,
    DATA_OPS_AGENT_INSTRUCTION,
    ORCHESTRATOR_INSTRUCTION,
)

logger = logging.getLogger(__name__)


def create_transformation_agents(
    app_config: dict[str, Any],
    folder_id: str | None = None,
    session_id: str | None = None,
) -> LlmAgent:
    """
    Create transformation orchestrator and its sub-agents.
    """
    logger.debug(
        "Creating transformation agents (folder=%s, session=%s)",
        folder_id[:8] if folder_id else "none",
        session_id[:8] if session_id else "none",
    )

    mcp_env = {**os.environ}
    if folder_id:
        mcp_env["MCP_FOLDER_ID"] = folder_id
    if session_id:
        mcp_env["MCP_SESSION_ID"] = session_id

    postgres_toolset = MCPToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=sys.executable,
                args=["-m", "app.agents.mcp_tools.postgres_mcp4tables"],
                spawn_server=True,
                env=mcp_env,
            ),
            timeout=120,
        ),
    )
    postgres_toolset.name = "postgres_db_toolset"

    data_ops_agent = LlmAgent(
        model=create_model_from_config(app_config, "transformation", "operations_agent"),
        name="DataOperations",
        instruction=DATA_OPS_AGENT_INSTRUCTION,
        tools=[postgres_toolset],
        output_key="data_ops_agent_output",
    )

    analysis_agent = LlmAgent(
        model=create_model_from_config(app_config, "transformation", "analysis_agent"),
        name="Analysis",
        instruction=ANALYSIS_AGENT_INSTRUCTION,
        tools=[postgres_toolset],
        output_key="analysis_agent_output",
    )

    search_agent = LlmAgent(
        model=create_model_from_config(app_config, "transformation", "search_agent"),
        name="SearchAgent",
        instruction="You're a specialist in Google Search",
        tools=[google_search],
    )

    orchestrator = LlmAgent(
        model=create_model_from_config(app_config, "transformation", "orchestrator"),
        name="Orchestrator_Agent",
        instruction=ORCHESTRATOR_INSTRUCTION,
        tools=[
            StreamingAgentTool(agent=search_agent),
            StreamingAgentTool(agent=data_ops_agent),
            StreamingAgentTool(agent=analysis_agent),
        ],
        )

    logger.debug("Transformation agents created")
    return orchestrator
