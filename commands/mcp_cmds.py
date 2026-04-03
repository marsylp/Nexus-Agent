"""MCP 相关命令: /mcp"""
from __future__ import annotations
from agent_core.mcp_manager import reload_mcp_servers, get_mcp_status


def handle_mcp(cmd: str):
    parts = cmd.strip().split()
    if len(parts) >= 2 and parts[1] == "reload":
        tools = reload_mcp_servers()
        print(f"  🔄 重新加载完成，共 {len(tools)} 个 MCP 工具\n")
        return

    status = get_mcp_status()
    if not status:
        print("  ℹ️  无 MCP 配置\n")
        return
    for s in status:
        if s["disabled"]:
            icon = "⬜"
            state = "已禁用"
        elif s["connected"]:
            icon = "✅"
            state = f"{s['tools']} 个工具"
        else:
            icon = "❌"
            state = "未连接"
        auto = f" [autoApprove: {','.join(s['autoApprove'])}]" if s["autoApprove"] else ""
        desc = f" — {s['description']}" if s["description"] else ""
        print(f"  {icon} {s['name']}: {state}{auto}{desc}")
    print(f"\n  💡 /mcp reload 重新加载配置\n")
