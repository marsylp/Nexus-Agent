"""Nexus Agent — 入口

职责：
1. 启动初始化（配置向导、Skills、MCP、Powers、Hooks）
2. REPL 交互循环（命令分发 + 普通对话）
3. 所有命令处理委托给 commands/ 子模块

架构说明：
- main.py 只做"胶水"工作，不包含业务逻辑
- 命令处理拆分到 commands/ 下 13 个独立模块
- 终端输出统一通过 agent_core/ui.py 格式化
"""
from __future__ import annotations
import os, traceback

from agent_core.setup_wizard import check_and_setup
check_and_setup()

from agent_core import Agent, __version__, provider_info, get_tool_specs
from agent_core.mcp_manager import load_mcp_servers, shutdown_mcp_servers
from agent_core.hooks import load_hooks
from agent_core.workspace import get_workspace_manager
from agent_core.powers import get_powers_manager
from agent_core.custom_agents import get_agent_manager
from agent_core.token_optimizer import cache_tools
from agent_core.ui import (
    banner, section, item, success, info, warn, hint, separator,
    bold, dim, cyan, green, yellow, gray, red, cmd_table, kv_table,
)
from skills import load_all_skills

from commands import (
    show_models, handle_model_switch,
    handle_spec, handle_task, handle_spawn,
    handle_mcp, handle_steer, handle_workspace,
    handle_power, auto_activate_powers,
    handle_agent, handle_load, handle_sessions,
    handle_keys, handle_harness, handle_agency,
)
from agent_core.agency_agents import get_agency_loader


def main():
    try:
        prov_info = provider_info()
    except Exception as e:
        warn(f"配置加载失败: {e}")
        prov_info = "未知"

    # ── 启动 Banner ──────────────────────────────────────
    banner("Nexus Agent", __version__)

    # ── 工作区 ───────────────────────────────────────────
    ws_mgr = get_workspace_manager()
    section("工作区")
    item(ws_mgr.current.name, dim(ws_mgr.current.path), icon="📂")

    # ── Skills ───────────────────────────────────────────
    section("Skills")
    skills = load_all_skills()
    if skills:
        success(f"{len(skills)} 个已加载")
    else:
        info("无 Skills")

    # ── MCP ──────────────────────────────────────────────
    section("MCP")
    mcp_tools = load_mcp_servers()
    if mcp_tools:
        success(f"{len(mcp_tools)} 个工具")
    else:
        info("无活跃 MCP")

    # ── Powers & Agents ──────────────────────────────────
    powers_mgr = get_powers_manager()
    powers_list = powers_mgr.list_powers()
    agent_mgr = get_agent_manager()
    agents_list = agent_mgr.list_agents()

    if powers_list or agents_list:
        section("扩展")
        if powers_list:
            item("Powers", ", ".join(p["name"] for p in powers_list), icon="⚡")
        if agents_list:
            item("Agents", ", ".join(a["name"] for a in agents_list), icon="🤖")

    # ── Agency Agents ────────────────────────────────────
    agency_loader = get_agency_loader()
    if agency_loader.available:
        section("Agency Agents")
        categories = {}
        for r in agency_loader.roles:
            cat = r.category.split("/")[0]
            categories[cat] = categories.get(cat, 0) + 1
        cat_summary = ", ".join(f"{cat}({n})" for cat, n in sorted(categories.items()))
        success(f"{len(agency_loader.roles)} 个专家角色已索引")
        info(f"分类: {cat_summary}")

    # ── Agent 初始化 ─────────────────────────────────────
    agent = Agent()

    hooks = load_hooks(agent)
    if hooks:
        section("Hooks")
        success(f"{len(hooks)} 个已加载")

    # ── 状态摘要 ─────────────────────────────────────────
    print()
    print(f"  {separator()}")
    kv_table([
        (bold("模式"), cyan(agent.current_mode.capitalize())),
        (bold("工具"), f"{len(get_tool_specs())} 个"),
        (bold("模型"), dim(prov_info)),
    ])
    print(f"  {separator()}")

    # ── 命令帮助 ─────────────────────────────────────────
    hint("输入自然语言对话，或使用以下命令:")
    print()
    cmd_table([
        ("/models", "查看/切换模型"),
        ("/tools", "查看已注册工具"),
        ("/tokens", "查看 Token 消耗"),
        ("/spec <需求>", "创建 Spec 并执行"),
        ("/task <需求>", "长任务控制"),
        ("/spawn <任务>", "派生子 Agent"),
        ("/harness", "驾驭层管理"),
        ("/agency", "专家角色管理（自动匹配）"),
        ("/save /load", "会话管理"),
        ("/quit", "退出"),
    ])
    print()

    # ── REPL 输入函数 ──────────────────────────────────────
    # 使用 prompt_toolkit 替代 input()，正确处理 CJK 字符宽度
    # macOS 自带的 libedit 对中文字符宽度计算有 bug，导致光标错位、删除失效
    try:
        from prompt_toolkit import prompt as pt_prompt
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.history import InMemoryHistory
        _pt_history = InMemoryHistory()

        def _read_input() -> str:
            return pt_prompt(
                HTML("  <style fg='ansibrightcyan'><b>❯</b></style> "),
                history=_pt_history,
            ).strip()
    except ImportError:
        # prompt_toolkit 未安装时降级到 input()
        def _read_input() -> str:
            return input("  ❯ ").strip()

    # ── REPL 循环 ────────────────────────────────────────
    # 所有 /xxx 命令委托给 commands/ 模块处理
    # 普通文本走 agent.run() 进入 Agent Run Loop
    while True:
        try:
            user_input = _read_input()
        except EOFError:
            print(f"\n  {gray('再见 👋')}")
            break
        except KeyboardInterrupt:
            # 在输入提示符处按 Ctrl+C：清空当前行，不退出
            print(f"  {gray('^C')}")
            continue

        if not user_input:
            continue
        if user_input == "/quit":
            print(f"  {gray('再见 👋')}")
            break
        if user_input == "/reset":
            agent.reset()
            success("已重置")
            print()
            continue
        if user_input == "/tools":
            section("已注册工具")
            for s in get_tool_specs():
                name = s["function"]["name"]
                desc = s["function"]["description"]
                print(f"    {cyan(name):30s} {gray(desc[:50])}")
            print()
            continue
        if user_input == "/tokens":
            section("Token 统计")
            kv_table([
                ("累计消耗", f"~{agent.estimated_tokens} tokens"),
                ("记忆状态", agent.memory_stats),
            ])
            print()
            continue

        if user_input == "/models":
            show_models(agent); continue
        if user_input.startswith("/model"):
            handle_model_switch(agent, user_input); continue
        if user_input.startswith("/spec"):
            handle_spec(agent, user_input); continue
        if user_input.startswith("/task"):
            handle_task(agent, user_input); continue
        if user_input.startswith("/spawn "):
            handle_spawn(agent, user_input); continue
        if user_input.startswith("/mcp"):
            handle_mcp(user_input); continue
        if user_input.startswith("/steer"):
            handle_steer(user_input); continue
        if user_input.startswith("/workspace") or user_input.startswith("/ws"):
            handle_workspace(user_input, agent); continue
        if user_input.startswith("/power"):
            handle_power(user_input, agent); continue
        if user_input.startswith("/agent"):
            handle_agent(user_input, agent); continue
        if user_input == "/save":
            sid = agent.save_session()
            success(f"已保存会话: {sid}")
            print()
            continue
        if user_input.startswith("/load"):
            handle_load(user_input, agent); continue
        if user_input == "/sessions":
            handle_sessions(agent); continue
        if user_input.startswith("/keys"):
            handle_keys(user_input); continue
        if user_input.startswith("/harness"):
            handle_harness(user_input, agent); continue
        if user_input.startswith("/agency"):
            handle_agency(user_input, agent); continue

        # 普通对话 — 在后台线程执行，主线程监听 Ctrl+C 中断
        try:
            auto_activate_powers(powers_mgr, agent, user_input)
            print()
            reply = _run_with_cancel(agent, user_input)
            if agent.stream:
                print()
            else:
                # 非流式模式：渲染 Markdown 后输出
                from agent_core.md_render import TerminalMarkdownRenderer
                md = TerminalMarkdownRenderer(indent=2)
                print(md.render(reply))
                print()
        except KeyboardInterrupt:
            # Ctrl+C 中断对话：立即清屏 + 停止 Agent
            _handle_cancel(agent)
        except Exception as e:
            print()
            from agent_core.ui import error as ui_error
            ui_error(str(e))
            if os.environ.get("DEBUG"):
                traceback.print_exc()
            print()

    shutdown_mcp_servers()


def _run_with_cancel(agent: Agent, user_input: str) -> str:
    """在后台线程执行 agent.run()，主线程可通过 Ctrl+C 中断

    原理：
    - agent.run() 在 daemon 线程中执行
    - 主线程通过 event.wait(0.2) 循环等待，可被 KeyboardInterrupt 打断
    - 中断时由调用方处理清理工作
    """
    import threading

    result_holder = [None]
    error_holder = [None]
    done = threading.Event()

    def worker():
        try:
            result_holder[0] = agent.run(user_input)
        except Exception as e:
            error_holder[0] = e
        finally:
            done.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    # 主线程等待完成，可被 Ctrl+C 打断
    while not done.is_set():
        done.wait(timeout=0.2)

    if error_holder[0]:
        raise error_holder[0]
    return result_holder[0] or ""


def _handle_cancel(agent: Agent):
    """处理 Ctrl+C 中断：立即清屏、停止 Spinner、清理状态

    关键：不等待后台线程结束（HTTP 请求可能还在阻塞），
    直接清理终端显示并恢复到可用状态。
    """
    import sys

    # 1. 设置中断标志（Agent Run Loop 会在下一个检查点退出）
    agent.interrupt()

    # 2. 立即清除终端上的 Spinner 行（\r 回到行首，\033[K 清除到行尾）
    sys.stderr.write("\r\033[K")
    sys.stderr.flush()
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()

    # 3. 显示取消提示
    print(f"    {gray('已取消 (Ctrl+C)')}\n")

    # 4. 清理 memory 中未完成的对话
    # 中断时 memory 末尾可能是没有对应 assistant 回复的 user 消息
    while agent.memory and agent.memory[-1].get("role") in ("user", "tool"):
        agent.memory.pop()


def _cleanup_interrupted(agent: Agent):
    """清理中断后的 Agent memory（兼容旧调用）"""
    while agent.memory and agent.memory[-1].get("role") in ("user", "tool"):
        agent.memory.pop()


if __name__ == "__main__":
    main()
