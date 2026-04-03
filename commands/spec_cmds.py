"""Spec 相关命令: /spec"""
from __future__ import annotations
import os
from agent_core import Agent, Spec


def handle_spec(agent: Agent, cmd: str):
    parts = cmd.split(maxsplit=2)
    if len(parts) < 2:
        print("  用法: /spec <需求描述> 或 /spec load <文件路径>\n")
        return

    spec = Spec(agent)

    if parts[1] == "load" and len(parts) > 2:
        path = parts[2].strip()
        if not os.path.exists(path):
            print(f"  ❌ 文件不存在: {path}\n")
            return
        spec.load(path)
    else:
        requirement = cmd[5:].strip()
        spec.create(requirement)

    confirm = input("  开始执行? [y/N]: ").strip().lower()
    if confirm in ("y", "yes"):
        spec.run()
        save = input("  保存 Spec? [y/N]: ").strip().lower()
        if save in ("y", "yes"):
            spec.save("specs/latest.md")
    print()
