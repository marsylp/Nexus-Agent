"""会话相关命令: /load, /sessions"""
from __future__ import annotations
from agent_core import Agent


def handle_load(cmd: str, agent: Agent):
    parts = cmd.strip().split(maxsplit=1)
    if len(parts) < 2:
        sessions = agent.list_sessions(10)
        if not sessions:
            print("  ℹ️  无保存的会话\n")
            return
        print("  最近会话:")
        for i, s in enumerate(sessions):
            print(f"  {i+1}. {s.display()}")
        choice = input("  选择编号或输入 ID: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(sessions):
            sid = sessions[int(choice) - 1].session_id
        else:
            sid = choice
    else:
        sid = parts[1].strip()
    if agent.load_session(sid):
        print(f"  ✅ 已加载会话: {sid}\n")
    else:
        print(f"  ❌ 未找到会话: {sid}\n")


def handle_sessions(agent: Agent):
    sessions = agent.list_sessions(20)
    if not sessions:
        print("  ℹ️  无保存的会话\n")
        return
    for s in sessions:
        print(f"  📝 {s.display()}")
    print(f"\n  💡 /load <ID> 加载会话 | /save 保存当前会话\n")
