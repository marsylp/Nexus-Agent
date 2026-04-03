"""Powers 相关命令: /power"""
from __future__ import annotations
from agent_core import Agent
from agent_core.powers import get_powers_manager
from agent_core.token_optimizer import cache_tools


def apply_power_to_agent(powers_mgr, agent: Agent):
    """将所有已激活 Power 的文档和 steering 注入 Agent"""
    doc = powers_mgr.get_all_doc_content()
    steering_files = powers_mgr.get_all_steering_files()
    steering_content = ""
    for sf in steering_files:
        try:
            with open(sf, "r", encoding="utf-8") as f:
                steering_content += f.read().strip() + "\n\n"
        except Exception:
            pass
    agent.inject_powers_content(doc, steering_content.strip())
    agent._all_tools = agent._get_filtered_tools()
    cache_tools(agent._all_tools)


def connect_power_mcp(power):
    """连接 Power 的 MCP 配置"""
    from agent_core.mcp_client import MCPClient
    from agent_core.mcp_manager import _register_tools
    for name, cfg in power.mcp_config.items():
        if cfg.get("disabled", False):
            continue
        print(f"  🔌 连接 Power MCP: {name} ...")
        client = MCPClient(name, cfg)
        if client.connect():
            _register_tools(client)
            print(f"  ✅ {name}: {len(client.tools)} 个工具")
        else:
            print(f"  ❌ {name}: 连接失败")


def auto_activate_powers(powers_mgr, agent: Agent, user_input: str):
    """根据用户输入自动激活匹配的 Power"""
    matched = powers_mgr.find_by_keyword(user_input)
    newly_activated = []
    for power in matched:
        if not power.activated:
            powers_mgr.activate(power.name)
            newly_activated.append(power.name)
            if power.mcp_config:
                connect_power_mcp(power)
    if newly_activated:
        print(f"  ⚡ 自动激活 Power: {', '.join(newly_activated)}")
        apply_power_to_agent(powers_mgr, agent)


def handle_power(cmd: str, agent: Agent):
    powers_mgr = get_powers_manager()
    parts = cmd.strip().split(maxsplit=2)
    sub = parts[1] if len(parts) > 1 else ""

    if sub == "activate" and len(parts) > 2:
        name = parts[2].strip()
        power = powers_mgr.activate(name)
        if power:
            print(f"  ✅ 已激活: {name}")
            if power.doc_content:
                print(f"  📄 文档: {power.description or power.doc_content[:80]}")
            if power.steering_files:
                print(f"  📋 Steering: {len(power.steering_files)} 个文件")
            if power.mcp_config:
                print(f"  🔌 MCP: {', '.join(power.mcp_config.keys())}")
            if power.skill_tools:
                print(f"  🛠️  工具: {', '.join(power.skill_tools)}")
            apply_power_to_agent(powers_mgr, agent)
            if power.mcp_config:
                connect_power_mcp(power)
        else:
            print(f"  ❌ 未找到: {name}")
        print()
        return

    if sub == "deactivate" and len(parts) > 2:
        name = parts[2].strip()
        if powers_mgr.deactivate(name):
            print(f"  ✅ 已停用: {name}")
            apply_power_to_agent(powers_mgr, agent)
            print()
        else:
            print(f"  ❌ 未找到或未激活: {name}\n")
        return

    if sub == "install" and len(parts) > 2:
        path = parts[2].strip()
        power = powers_mgr.install(path)
        if power:
            print(f"  ✅ 已安装: {power.name}\n")
        else:
            print(f"  ❌ 安装失败: {path}\n")
        return

    if sub == "uninstall" and len(parts) > 2:
        name = parts[2].strip()
        if powers_mgr.uninstall(name):
            print(f"  ✅ 已卸载: {name}\n")
        else:
            print(f"  ❌ 未找到: {name}\n")
        return

    # 列出所有 Powers
    powers = powers_mgr.list_powers()
    if not powers:
        print("  ℹ️  无已安装的 Power (放入 powers/ 目录)\n")
        return
    for p in powers:
        icon = "⚡" if p["activated"] else "⬜"
        parts_info = []
        if p["has_doc"]:
            parts_info.append("📄doc")
        if p["has_steering"]:
            parts_info.append("📋steering")
        if p["has_mcp"]:
            parts_info.append("🔌mcp")
        if p["has_skills"]:
            parts_info.append("🛠️skills")
        info = " ".join(parts_info)
        desc = f" — {p['description']}" if p["description"] else ""
        tools = f" [{','.join(p['tools'])}]" if p["tools"] else ""
        print(f"  {icon} {p['name']:25s} {info}{desc}{tools}")
    print(f"\n  💡 /power activate|deactivate|install|uninstall <名称>\n")
