"""命令处理模块 — 将 REPL 命令按功能分组

从 main.py 拆分出来的 13 个命令处理模块。
每个模块负责一组相关命令的解析和执行。
main.py 的 REPL 循环通过 if/elif 匹配命令前缀，
然后委托给对应模块的处理函数。
"""
from commands.model_cmds import show_models, handle_model_switch
from commands.spec_cmds import handle_spec
from commands.task_cmds import handle_task, get_active_harness
from commands.spawn_cmds import handle_spawn
from commands.mcp_cmds import handle_mcp
from commands.steer_cmds import handle_steer
from commands.workspace_cmds import handle_workspace
from commands.power_cmds import handle_power, apply_power_to_agent, connect_power_mcp, auto_activate_powers
from commands.agent_cmds import handle_agent
from commands.session_cmds import handle_load, handle_sessions
from commands.keys_cmds import handle_keys
from commands.harness_cmds import handle_harness
from commands.agency_cmds import handle_agency

__all__ = [
    "show_models", "handle_model_switch",
    "handle_spec",
    "handle_task", "get_active_harness",
    "handle_spawn",
    "handle_mcp",
    "handle_steer",
    "handle_workspace",
    "handle_power", "apply_power_to_agent", "connect_power_mcp", "auto_activate_powers",
    "handle_agent",
    "handle_load", "handle_sessions",
    "handle_keys",
    "handle_harness",
    "handle_agency",
]
