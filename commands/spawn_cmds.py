"""子 Agent 相关命令: /spawn"""
from __future__ import annotations
from agent_core import Agent


def handle_spawn(agent: Agent, cmd: str):
    parts = cmd.split(maxsplit=1)
    task = parts[1].strip() if len(parts) > 1 else ""
    if not task:
        print("  用法: /spawn <任务>  |  /spawn -p <任务1> | <任务2> | ...")
        print("        /spawn -a <任务>  (异步，后台执行)\n")
        return

    if task.startswith("-p "):
        tasks_str = task[3:].strip()
        task_list = [{"task": t.strip()} for t in tasks_str.split("|") if t.strip()]
        if len(task_list) < 2:
            print("  ⚠️  并行模式需要至少 2 个任务（用 | 分隔）\n")
            return
        print(f"  🔀 并行派生 {len(task_list)} 个子 Agent ...")
        results = agent.spawn_parallel(task_list)
        for i, r in enumerate(results):
            print(f"\n  🤖 子 Agent #{i+1}: {r[:200]}")
        print()
        return

    if task.startswith("-a "):
        actual_task = task[3:].strip()
        print(f"  🔀 异步派生子 Agent (后台执行) ...")
        future = agent.spawn_async(actual_task)
        if not hasattr(agent, '_pending_futures'):
            agent._pending_futures = []
        agent._pending_futures.append(future)
        print(f"  ✅ 已提交，使用 /spawn status 查看状态\n")
        return

    if task == "status":
        status = agent.get_sub_agent_status()
        print(f"  📊 子 Agent 并发: {status['active']}/{status['max_concurrent']}")
        futures = getattr(agent, '_pending_futures', [])
        if futures:
            for i, f in enumerate(futures):
                state = "✅ 完成" if f.done() else "⏳ 运行中"
                elapsed = f"{f.elapsed:.1f}s"
                print(f"  {i+1}. {state} ({elapsed})")
                if f.done():
                    print(f"     结果: {f.result()[:100]}")
        else:
            print("  ℹ️  无异步任务")
        print()
        return

    print(f"  🔀 派生子 Agent ...")
    result = agent.spawn(task)
    print(f"\n  🤖 子 Agent: {result}\n")
