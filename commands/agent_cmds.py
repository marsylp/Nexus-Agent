"""自定义 Agent 相关命令: /agent"""
from __future__ import annotations
from agent_core import Agent
from agent_core.custom_agents import get_agent_manager


def handle_agent(cmd: str, agent: Agent):
    agent_mgr = get_agent_manager()
    parts = cmd.strip().split(maxsplit=2)
    sub = parts[1] if len(parts) > 1 else ""

    if sub == "run" and len(parts) > 2:
        rest = parts[2].strip()
        space_idx = rest.find(" ")
        if space_idx == -1:
            print(f"  用法: /agent run <名称> <任务>\n")
            return
        name = rest[:space_idx]
        task = rest[space_idx + 1:]
        cfg = agent_mgr.get(name)
        if not cfg:
            print(f"  ❌ 未找到 Agent: {name}\n")
            return
        print(f"  🤖 调用 {name}: {cfg.description}")
        print(f"  📝 任务: {task}\n")
        result = agent_mgr.invoke(name, task, parent_agent=agent)
        print(f"\n  🤖 {name}: {result}\n")
        return

    if sub == "create" and len(parts) > 2:
        name = parts[2].strip()
        print(f"  📝 创建自定义 Agent: {name}")
        desc = input("  描述: ").strip()
        prompt = input("  系统提示词: ").strip()
        model = input("  模型 (留空跟随主Agent): ").strip() or None
        tools_str = input("  工具列表 (逗号分隔，留空=全部): ").strip()
        tools = [t.strip() for t in tools_str.split(",") if t.strip()] if tools_str else None
        cfg = agent_mgr.create_config(name, desc, prompt, model, tools)
        print(f"  ✅ 已创建: {cfg.name}\n")
        return

    if sub == "delete" and len(parts) > 2:
        name = parts[2].strip()
        if agent_mgr.delete_config(name):
            print(f"  ✅ 已删除: {name}\n")
        else:
            print(f"  ❌ 未找到: {name}\n")
        return

    if sub == "info" and len(parts) > 2:
        name = parts[2].strip()
        cfg = agent_mgr.get(name)
        if not cfg:
            print(f"  ❌ 未找到: {name}\n")
            return
        print(f"  🤖 {cfg.name}: {cfg.description}")
        print(f"  📝 提示词: {cfg.system_prompt[:100]}...")
        if cfg.model:
            print(f"  📡 模型: {cfg.model}")
        if cfg.tools is not None:
            print(f"  🛠️  工具: {', '.join(cfg.tools) if cfg.tools else '无'}")
        print(f"  🔄 最大迭代: {cfg.max_iterations}")
        if cfg.auto_approve:
            print(f"  ✅ 自动放行: {', '.join(cfg.auto_approve)}")
        print()
        return

    agents = agent_mgr.list_agents()
    if not agents:
        print("  ℹ️  无自定义 Agent (放入 agents/ 目录)\n")
        return
    for a in agents:
        model = f" [{a.get('model', '默认')}]" if a.get("model") else ""
        tools_count = f" {len(a['tools'])}工具" if a.get("tools") is not None else " 全部工具"
        print(f"  🤖 {a['name']:20s}{model}{tools_count} — {a['description']}")
    print(f"\n  💡 /agent run <名称> <任务> | create <名称> | info <名称> | delete <名称>\n")
