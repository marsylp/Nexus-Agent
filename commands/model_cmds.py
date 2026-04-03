"""模型相关命令: /models, /model"""
from __future__ import annotations
from agent_core import Agent, list_providers
from agent_core.model_router import MODEL_TIERS
from agent_core.ui import (
    section, success, error, hint, bold, dim, cyan, green, gray, yellow,
    kv_table, separator,
)


def show_models(agent: Agent):
    current_mode = agent.current_mode

    section("模型列表")
    print()

    # Auto 模式
    auto_icon = green("●") if current_mode == "auto" else gray("○")
    print(f"    {auto_icon} {bold('auto'):14s} {dim('智能路由（简单→轻量，复杂→强模型）')}")
    print(f"    {separator(char='·', width=50)}")

    # 各 Provider
    for p in list_providers():
        is_active = current_mode == p["name"]
        icon = green("●") if is_active else gray("○")
        key_icon = green("✓") if p["configured"] else yellow("?")

        tiers = MODEL_TIERS.get(p["name"], {})
        if tiers:
            t1, t2 = tiers.get(1, ""), tiers.get(2, "")
            tier_info = t1 if t1 == t2 else f"{dim('轻:')} {t1} {dim('|')} {dim('强:')} {t2}"
        else:
            tier_info = p["model"]

        name_display = bold(p["name"]) if is_active else p["name"]
        print(f"    {icon} {name_display:14s} {key_icon}  {tier_info}")

    print()
    kv_table([("当前", cyan(f"{'Auto 🧠' if current_mode == 'auto' else current_mode}"))])
    hint("/model auto | /model <名称>")
    print()


def handle_model_switch(agent: Agent, cmd: str):
    parts = cmd.split(maxsplit=1)
    if len(parts) < 2:
        show_models(agent)
        return
    name = parts[1].strip().lower()
    if name == "auto":
        agent.set_mode("auto")
        success("Auto 模式")
        print()
        return
    valid = [p["name"] for p in list_providers()]
    if name not in valid:
        error(f"未知: {name}，可选: auto, {', '.join(valid)}")
        print()
        return
    for p in list_providers():
        if p["name"] == name and not p["configured"]:
            from agent_core.ui import warn
            warn(f"{name} 未配置 API Key")
            print()
            return
    agent.set_mode(name)
    agent.reset()
    success(f"已切换: {name}")
    print()
