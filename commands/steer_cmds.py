"""Steering 相关命令: /steer"""
from __future__ import annotations
from agent_core.steering import get_steering_manager


def handle_steer(cmd: str):
    parts = cmd.strip().split(maxsplit=1)
    mgr = get_steering_manager()

    if len(parts) < 2:
        files = mgr.list_files()
        if not files:
            print("  ℹ️  无 steering 文件 (放入 steering/ 目录)\n")
            return
        for f in files:
            mode = f["inclusion"]
            if mode == "always":
                icon = "✅"
                detail = "始终加载"
            elif mode == "filematch":
                icon = "📂"
                detail = f"匹配: {f['pattern']}"
            elif mode == "manual":
                active = f.get("active", False)
                icon = "🟢" if active else "⬜"
                detail = "手动" + (" (已激活)" if active else "")
            else:
                icon = "❓"
                detail = mode
            print(f"  {icon} {f['name']:25s} [{detail}]")
        print(f"\n  💡 /steer <文件名> 激活手动文件\n")
        return

    name = parts[1].strip()
    if name == "reload":
        mgr.reload()
        print(f"  🔄 已重新加载 steering 文件\n")
        return

    if mgr.activate_manual(name):
        print(f"  ✅ 已激活: {name}\n")
    else:
        print(f"  ❌ 未找到: {name}\n")
