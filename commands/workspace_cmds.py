"""工作区相关命令: /workspace"""
from __future__ import annotations
import os
from agent_core import Agent
from agent_core.workspace import get_workspace_manager
from agent_core.mcp_manager import reload_mcp_servers
from agent_core.hooks import load_hooks


def handle_workspace(cmd: str, agent: Agent):
    ws_mgr = get_workspace_manager()
    parts = cmd.strip().split(maxsplit=2)
    sub = parts[1] if len(parts) > 1 else ""

    if sub == "add" and len(parts) > 2:
        path = parts[2].strip()
        ws = ws_mgr.add(path)
        print(f"  ✅ 已添加工作区: {ws.name} ({ws.path})\n")
        return
    if sub == "remove" and len(parts) > 2:
        name = parts[2].strip()
        if ws_mgr.remove(name):
            print(f"  ✅ 已移除: {name}\n")
        else:
            print(f"  ❌ 未找到: {name}\n")
        return
    if sub == "switch" and len(parts) > 2:
        name = parts[2].strip()
        ws = ws_mgr.switch(name)
        if ws:
            print(f"  ✅ 已切换到: {ws.name} ({ws.path})")
            os.chdir(ws.path)
            print("  🔄 重载 Agent 配置 ...")
            reload_mcp_servers()
            load_hooks(agent)
            agent.reload_config()
            print(f"  ✅ 配置已重载\n")
        else:
            print(f"  ❌ 未找到: {name}\n")
        return

    for w in ws_mgr.list_workspaces():
        icon = "📂" if w["current"] else "  "
        active = "✅" if w["active"] else "⬜"
        exists = "" if w["exists"] else " ⚠️路径不存在"
        print(f"  {icon} {active} {w['name']:20s} {w['path']}{exists}")
    print(f"\n  💡 /workspace add <路径> | remove <名称> | switch <名称>\n")
