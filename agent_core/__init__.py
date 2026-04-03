"""agent_core — 轻量级 Agent 开发框架"""
from agent_core.agent import Agent
from agent_core.tools import tool, get_tool_specs, call_tool
from agent_core.llm import chat_completion, provider_info, switch_provider, list_providers, get_auth_manager
from agent_core.approval import TOOL_SAFETY
from agent_core.spec import Spec
from agent_core.steering import load_steering, get_steering_manager
from agent_core.workspace import get_workspace_manager
from agent_core.powers import get_powers_manager
from agent_core.custom_agents import get_agent_manager
from agent_core.hooks import get_hook_manager
from agent_core.session_store import SessionStore
from agent_core.task_harness import TaskHarness, TaskConfig
from agent_core.orchestrator import (
    AgentOrchestrator, SharedContext, Task, Pipeline, PipelineMode,
    AgentRole, ReducerStrategy, ConditionalEdge, TaskResult, TaskStatus,
)

__version__ = "0.0.2"
__all__ = ["Agent", "Spec", "tool", "get_tool_specs", "call_tool",
           "chat_completion", "provider_info", "switch_provider", "list_providers",
           "get_auth_manager",
           "TOOL_SAFETY", "load_steering", "get_steering_manager",
           "get_workspace_manager", "get_powers_manager", "get_agent_manager",
           "get_hook_manager", "SessionStore", "TaskHarness", "TaskConfig",
           "AgentOrchestrator", "SharedContext", "Task", "Pipeline", "PipelineMode"]
