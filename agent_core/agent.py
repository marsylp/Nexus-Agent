"""Agent 引擎 — 核心 Run Loop + Hook + 工具执行

v0.5 重构:
- 会话管理拆分到 SessionMixin
- 子 Agent 管理拆分到 SubAgentMixin
- 场景级模型选择拆分到 SceneModelMixin
- Agent 类聚焦核心职责: Run Loop、工具执行、Steering/Powers 注入
"""
from __future__ import annotations
import json, os, sys, time, hashlib, platform
from concurrent.futures import ThreadPoolExecutor, as_completed
from agent_core.llm import (
    chat_completion_resilient, get_model, PROVIDERS,
)
from agent_core.tools import get_tool_specs, call_tool
from agent_core.token_optimizer import (
    compress_tool_result, build_context, select_tools,
    count_messages_tokens, cache_tools,
)
from agent_core.model_router import ModelRouter
from agent_core.approval import request_approval, check_provider_tool_policy
from agent_core.steering import get_steering_manager
from agent_core.harness.layer import HarnessLayer
from agent_core.context_hygiene import ContextHygiene
from agent_core.memory_index import MemoryIndex
from agent_core.agency_agents import get_agency_matcher, get_agency_loader
from agent_core.mixins import SessionMixin, SubAgentMixin, SceneModelMixin

DEFAULT_SYSTEM_PROMPT = """你是一个功能强大的 AI Agent，拥有多种工具可以调用来完成任务。

核心原则:
- 先思考再行动，选择最合适的工具
- 需要实时信息（天气、新闻、价格等）时，使用 web_search 搜索互联网
- 需要执行命令时使用 shell，需要读写文件时使用 read_file/write_file
- 如果工具调用失败，尝试换一种方式解决

输出要求:
- 工具返回结果后，你必须将完整内容呈现给用户，不要省略、截断或概括
- 列表类结果（新闻、搜索结果等）必须完整输出每一条，包括序号、标题、热度等全部字段
- 不要说"以上是工具返回的结果"之类的废话，直接输出内容
- 保持工具返回的原始格式，不要重新编号或重新组织"""


# ── SubAgentFuture — 异步子 Agent 句柄 ──────────────────────

class SubAgentFuture:
    """异步子 Agent 的 Future 对象"""

    def __init__(self, parent, task, system_prompt, tools_filter, timeout, callback):
        import threading
        self._parent = parent
        self._task = task
        self._system_prompt = system_prompt
        self._tools_filter = tools_filter
        self._timeout = timeout
        self._callback = callback
        self._result: str | None = None
        self._error: Exception | None = None
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_time: float = 0
        self._end_time: float = 0

    def start(self):
        import threading
        self._start_time = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self._result = self._parent.spawn(
                self._task, self._system_prompt,
                self._tools_filter, self._timeout,
            )
        except Exception as e:
            self._error = e
            self._result = "[子 Agent 错误] {}".format(e)
        finally:
            self._end_time = time.time()
            self._done.set()
            if self._callback:
                try:
                    self._callback(self._result)
                except Exception:
                    pass

    def done(self) -> bool:
        return self._done.is_set()

    def result(self, timeout: float | None = None) -> str:
        self._done.wait(timeout=timeout)
        if not self._done.is_set():
            return "[子 Agent] 等待超时"
        return self._result or ""

    @property
    def elapsed(self) -> float:
        if self._end_time:
            return self._end_time - self._start_time
        return time.time() - self._start_time if self._start_time else 0

    @property
    def error(self) -> Exception | None:
        return self._error


class Agent(SessionMixin, SubAgentMixin, SceneModelMixin):
    """Agent 引擎 — 核心 Run Loop + Hook + 工具执行

    通过 Mixin 模式拆分职责（详见 docs/ARCHITECTURE.md §3.1）：
    - SessionMixin: 会话持久化（save/load/list/compact）
    - SubAgentMixin: 子 Agent 管理（spawn/spawn_async/spawn_parallel）
    - SceneModelMixin: 场景级模型选择（_get_scene_model/_llm_summarize/_resolve_model）
    - Agent 本体: Run Loop、工具执行、Steering/Powers 注入、Hook 事件

    运行时只有一个 Agent 实例，Mixin 只是代码组织方式，
    通过 Python 多继承合并到同一个对象上。
    """

    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    max_iterations: int = 10

    def __init__(self, model: str | None = None, system_prompt: str | None = None,
                 mode: str | None = None, stream: bool = True,
                 tools_filter: list[str] | None = None,
                 timeout: int | None = None, parent: "Agent | None" = None,
                 provider: str | None = None):
        self._provider = provider or os.environ.get("LLM_PROVIDER", "openai").lower()
        self.model = model
        self.stream = stream
        self.timeout = timeout
        self._parent = parent
        self._tools_filter = tools_filter
        self._interrupted = False
        self.router = ModelRouter()
        if mode:
            self.router.mode = mode.lower()

        if system_prompt:
            self.system_prompt = system_prompt

        # 上下文感知
        self._context_info = self._gather_context()

        # Steering
        self._steering_mgr = get_steering_manager()
        self._steering_content = self._steering_mgr.get_active_content()

        # 构建完整 system prompt
        full_prompt = self.system_prompt
        if self._steering_content:
            full_prompt += "\n\n" + self._steering_content
        if self._context_info:
            full_prompt += "\n\n[环境上下文]\n" + self._context_info

        self.memory: list[dict] = [{"role": "system", "content": full_prompt}]
        self._all_tools = self._get_filtered_tools()
        self._total_tokens = 0
        self._hooks: dict[str, list] = {}
        self._hook_manager = None

        # Harness 驾驭层
        self._harness_layer: HarnessLayer | None = None
        try:
            self._harness_layer = HarnessLayer(agent=self, cwd=os.getcwd())
        except Exception:
            self._harness_layer = None

        self._powers_content = ""

        # 上下文卫生系统
        self._hygiene = ContextHygiene()

        # 记忆索引（可检索的历史记忆）
        self._memory_index = MemoryIndex(persist=parent is None)

        # Agency Agents 自动匹配
        self._agency_matcher = get_agency_matcher()
        self._agency_role_content = ""  # 当前注入的角色内容

        # 初始化 Mixin
        self._init_session()

        cache_tools(self._all_tools)

    # ── provider 属性 ────────────────────────────────────────

    @property
    def provider(self) -> str:
        return self._provider

    @provider.setter
    def provider(self, value: str):
        self._provider = value.lower()

    def _get_filtered_tools(self) -> list[dict]:
        all_tools = get_tool_specs()
        if self._tools_filter is not None:
            return [t for t in all_tools if t["function"]["name"] in self._tools_filter]
        return all_tools

    # ── 上下文感知 ───────────────────────────────────────────

    @staticmethod
    def _gather_context() -> str:
        import subprocess as _sp
        info = []
        info.append("OS: {} {} ({})".format(platform.system(), platform.release(), platform.machine()))
        info.append("CWD: {}".format(os.getcwd()))
        info.append("Python: {}".format(platform.python_version()))
        try:
            node_ver = _sp.run(["node", "--version"], capture_output=True, text=True, timeout=3).stdout.strip()
            if node_ver:
                info.append("Node: {}".format(node_ver))
        except Exception:
            pass
        info.append("Shell: {}".format(os.environ.get("SHELL", "unknown")))
        try:
            branch = _sp.run(["git", "branch", "--show-current"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
            if branch:
                info.append("Git分支: {}".format(branch))
                status = _sp.run(["git", "status", "--short"],
                                 capture_output=True, text=True, timeout=3).stdout.strip()
                if status:
                    lines = status.split("\n")
                    info.append("Git变更: {} 个文件".format(len(lines)))
                    if len(lines) <= 5:
                        for line in lines:
                            info.append("  {}".format(line))
                log = _sp.run(["git", "log", "--oneline", "-1"],
                              capture_output=True, text=True, timeout=3).stdout.strip()
                if log:
                    info.append("最近提交: {}".format(log[:80]))
        except Exception:
            pass
        try:
            entries = sorted(os.listdir("."))[:20]
            info.append("项目文件: {}".format(", ".join(entries)))
        except Exception:
            pass
        machine_id = hashlib.md5(platform.node().encode()).hexdigest()
        info.append("Machine: {}".format(machine_id[:16]))
        return "\n".join(info)

    # ── Hook 事件系统 ────────────────────────────────────────

    def on(self, event: str, callback):
        self._hooks.setdefault(event, []).append(callback)

    def _emit(self, event: str, **kwargs) -> bool:
        if self._hook_manager:
            result = self._hook_manager.emit(event, **kwargs)
            return result["allowed"]
        for cb in self._hooks.get(event, []):
            try:
                result = cb(**kwargs)
                if result is False:
                    return False
            except Exception as e:
                print("  ⚠️  Hook [{}] 错误: {}".format(event, e))
        return True

    def _register_harness_hooks(self):
        if self._harness_layer and self._hook_manager:
            self._harness_layer.register_hooks(self._hook_manager)

    # ── 并行工具执行 ─────────────────────────────────────────
    # 多个 tool_calls 通过 ThreadPoolExecutor 并行执行（最多 4 并发）
    # 每个工具调用经过完整的 9 层安全管线检查（详见 docs/ARCHITECTURE.md §3.6）

    def _execute_tools_parallel(self, tool_calls_data: list[dict]) -> list[dict]:
        results: list[dict | None] = [None] * len(tool_calls_data)

        def exec_one(idx, tc):
            name = tc["name"]
            args = tc["args"]
            tc_id = tc["id"]
            allowed, reason = check_provider_tool_policy(name, provider=self._provider)
            if not allowed:
                return idx, tc_id, "[策略拦截] {}".format(reason), False
            if not self._emit("preToolUse", tool_name=name, tool_args=args):
                return idx, tc_id, "[Hook 阻止了此操作]", False
            approved, reason = request_approval(name, args)
            if not approved:
                return idx, tc_id, "[操作已取消] {}".format(reason), False
            result = call_tool(name, args)
            result = compress_tool_result(result, tool_name=name)
            self._emit("postToolUse", tool_name=name, tool_args=args, tool_result=result)
            # 痛点三: 记录工具使用频率
            self._hygiene.record_tool_usage(name)
            return idx, tc_id, result, True

        if len(tool_calls_data) == 1:
            idx, tc_id, result, ok = exec_one(0, tool_calls_data[0])
            return [{"tc_id": tc_id, "result": result, "ok": ok}]

        with ThreadPoolExecutor(max_workers=min(len(tool_calls_data), 4)) as pool:
            futures = {pool.submit(exec_one, i, tc): i for i, tc in enumerate(tool_calls_data)}
            for future in as_completed(futures):
                idx, tc_id, result, ok = future.result()
                results[idx] = {"tc_id": tc_id, "result": result, "ok": ok}

        return results  # type: ignore[return-value]

    # ── 流式中断 ─────────────────────────────────────────────

    def interrupt(self):
        """中断当前执行

        设置 _interrupted 标志，Run Loop 在下一个检查点退出。
        同时停止活跃的 Spinner 动画（如果有的话）。
        """
        self._interrupted = True
        # 停止活跃的 Spinner（可能在后台线程中运行）
        spinner = getattr(self, '_active_spinner', None)
        if spinner:
            try:
                spinner.stop()
            except Exception:
                pass
            self._active_spinner = None

    # ── 核心 Run 循环 ────────────────────────────────────────
    # 完整数据流详见 docs/ARCHITECTURE.md §3.2
    # 每轮迭代: 模型路由 → 上下文构建 → LLM 调用 → 工具执行 → 结果呈现

    def run(self, user_input: str) -> str:
        from agent_core.ui import Spinner, gray, cyan, dim, green, red, yellow

        self._interrupted = False
        self._truncated = False
        start_time = time.time()
        accumulated_content = ""

        self._emit("promptSubmit", user_input=user_input)
        self.memory.append({"role": "user", "content": user_input})

        context_files = self._extract_context_files(user_input)
        if context_files:
            extra_steering = self._steering_mgr.get_active_content(context_files=context_files)
            if extra_steering and extra_steering != self._steering_content:
                self._steering_content = extra_steering
                self._rebuild_system_prompt()

        # Agency Agents 自动匹配：根据用户输入匹配最合适的专家角色
        if self._agency_matcher and self._agency_matcher._loader.available:
            matched_role = self._agency_matcher.match(user_input)
            if matched_role:
                # 截取角色正文的前 2000 字符作为角色注入（避免 token 爆炸）
                role_body = matched_role.body
                if len(role_body) > 2000:
                    role_body = role_body[:2000] + "\n\n[角色指南已截断，聚焦核心规则]"
                new_role_content = "[专家角色: {} {}]\n{}".format(
                    matched_role.emoji, matched_role.name, role_body)
                if new_role_content != self._agency_role_content:
                    self._agency_role_content = new_role_content
                    self._rebuild_system_prompt()
                    sys.stderr.write("    {} 匹配专家: {} {}\n".format(
                        "\033[90m", matched_role.emoji, matched_role.name + "\033[0m"))
                    sys.stderr.flush()

        tools = select_tools(user_input, self._all_tools)

        # 痛点三: 智能工具筛选（含 MCP 工具预算控制）
        context_budget = count_messages_tokens(self.memory)
        from agent_core.token_optimizer import TOOL_KEYWORDS, max_context_tokens
        budget_tools = self._hygiene.tool_budget.select_tools(
            user_input, self._all_tools, max_context_tokens(), TOOL_KEYWORDS)
        if len(budget_tools) < len(tools):
            tools = budget_tools

        for i in range(self.max_iterations):
            if self.timeout and (time.time() - start_time) > self.timeout:
                return accumulated_content or "[Agent] 执行超时 ({}s)".format(self.timeout)
            if self._interrupted:
                return accumulated_content or "[Agent] 已中断"

            if i == 0:
                prov, model = self._resolve_model(user_input, has_tool_calls=False, iteration=0)
                if self.router.mode == "auto":
                    sys.stderr.write("    {}\n".format(
                        dim("{} → {}".format(self.router.last_selection.split("→")[0].strip(),
                                             cyan("{}/{}".format(prov, model))))))
                    sys.stderr.flush()
            else:
                prov, model = self._provider, self.model or get_model(self._provider)

            # 痛点一/二/四: 上下文卫生系统集成
            context = build_context(
                self.memory,
                llm_summarize_fn=self._llm_summarize,
                hygiene=self._hygiene,
                user_input=user_input,
            )

            # 按需检索: 用户引用历史时，从索引中检索相关记忆注入上下文
            if i == 0 and self._memory_index.needs_retrieval(user_input):
                recall = self._memory_index.retrieve_as_prompt(user_input)
                if recall:
                    # 插入到 system 消息之后、用户消息之前
                    insert_pos = 1 if context and context[0]["role"] == "system" else 0
                    context.insert(insert_pos, {"role": "system", "content": recall})

            self._truncated = False

            try:
                if self.stream:
                    result = self._run_stream(context, tools, model, prov)
                else:
                    spinner = Spinner("思考中")
                    self._active_spinner = spinner
                    spinner.start()
                    try:
                        result = self._run_sync(context, tools, model, prov)
                    finally:
                        spinner.stop()
                        self._active_spinner = None
            except Exception as e:
                self.router.record_result(success=False)
                return accumulated_content or "[LLM 调用失败] {}".format(e)

            self._total_tokens += count_messages_tokens(context, tools)

            if result is not None:
                accumulated_content += result
                if self._truncated:
                    self.memory.append({"role": "assistant", "content": result})
                    self.memory.append({"role": "user", "content": "继续"})
                    continue
                self.memory.append({"role": "assistant", "content": accumulated_content})
                self._emit("agentStop", user_input=user_input, reply=accumulated_content)
                self.router.record_result(success=True)
                self._auto_persist()
                return accumulated_content

            tools = self._all_tools

        return accumulated_content or "[Agent] 达到最大迭代次数。"

    @staticmethod
    def _extract_context_files(user_input: str) -> list[str]:
        import re
        patterns = [
            r'[\w./\\-]+\.(?:py|js|ts|tsx|jsx|java|go|rs|c|cpp|h|md|json|yaml|yml|toml|css|html|sql)',
        ]
        files = []
        for pat in patterns:
            files.extend(re.findall(pat, user_input))
        return files

    def _run_stream(self, context, tools, model, provider):
        from agent_core.ui import Spinner, gray, cyan, dim, green, red
        from agent_core.md_render import TerminalMarkdownRenderer

        # 输出保护配置
        max_output_chars = int(os.environ.get("MAX_STREAM_OUTPUT_CHARS", "8000"))
        stream_timeout = int(os.environ.get("STREAM_TIMEOUT", "120"))

        # Markdown 渲染器
        md = TerminalMarkdownRenderer(indent=2)

        # 启动 spinner（收到第一个 token 后自动停止）
        spinner = Spinner("思考中")
        self._active_spinner = spinner  # 注册到 Agent，供 interrupt() 停止
        spinner.start()
        spinner_active = True

        def _stop_spinner(msg=""):
            nonlocal spinner_active
            if spinner_active:
                spinner.stop(msg)
                spinner_active = False
                self._active_spinner = None

        gen = chat_completion_resilient(context, tools=tools or None, model=model,
                                        stream=True, provider=provider)
        full_content = ""
        tool_calls_data = []
        started_text = False
        in_thinking = False
        stream_start = time.time()
        last_token_time = time.time()

        for event_type, data in gen:
            now = time.time()

            # 超时保护：整体超时或长时间无新 token
            if now - stream_start > stream_timeout:
                if spinner_active:
                    spinner.stop()
                if started_text:
                    sys.stdout.write(gray("\n    [输出超时，已截断]"))
                    sys.stdout.flush()
                    print()
                return full_content or "[流式输出超时]"

            if self._interrupted:
                if spinner_active:
                    spinner.stop()
                if started_text:
                    print("\n    " + gray("⚡ 已中断"))
                return full_content or "[已中断]"

            if event_type == "error":
                if spinner_active:
                    spinner.stop(red("✗") + " 调用失败")
                    spinner_active = False
                if started_text:
                    print()
                self.router.record_result(success=False)
                return full_content or "[LLM 调用失败] {}".format(data)

            if event_type == "thinking":
                if spinner_active:
                    spinner.stop()
                    spinner_active = False
                if not in_thinking:
                    sys.stdout.write("    {} ".format(dim("思考:")))
                    in_thinking = True
                sys.stdout.write(dim(data))
                sys.stdout.flush()
                last_token_time = now

            elif event_type == "content":
                if spinner_active:
                    spinner.stop()
                    spinner_active = False
                if in_thinking:
                    print()
                    in_thinking = False
                if not started_text:
                    sys.stdout.write("\n")
                    started_text = True

                # 输出长度保护
                if len(full_content) + len(data) > max_output_chars:
                    remaining = max_output_chars - len(full_content)
                    if remaining > 0:
                        rendered = md.feed(data[:remaining])
                        if rendered:
                            sys.stdout.write(rendered)
                        full_content += data[:remaining]
                    # 刷新渲染器缓冲
                    flushed = md.flush()
                    if flushed:
                        sys.stdout.write(flushed)
                    sys.stdout.write(gray("\n    [输出过长，已截断]"))
                    sys.stdout.flush()
                    print()
                    # 消费剩余 token 但不输出
                    for _ in gen:
                        pass
                    return full_content

                # 流式 Markdown 渲染
                rendered = md.feed(data)
                if rendered:
                    sys.stdout.write(rendered)
                    sys.stdout.flush()
                full_content += data
                last_token_time = now

            elif event_type == "tool_call_start":
                if spinner_active:
                    spinner.stop()
                    spinner_active = False
                if in_thinking:
                    print()
                    in_thinking = False
                sys.stdout.write("\n    {} {} ".format(cyan("▸"), data))
                sys.stdout.flush()

            elif event_type == "tool_call_end":
                tc = data
                try:
                    args = json.loads(tc["arguments"])
                except json.JSONDecodeError:
                    args = {}
                tool_calls_data.append({"id": tc["id"], "name": tc["name"], "args": args})
                args_preview = json.dumps(args, ensure_ascii=False)[:60]
                print(dim(args_preview))

            elif event_type == "done":
                if spinner_active:
                    spinner.stop()
                    spinner_active = False
                if in_thinking:
                    print()
                if started_text:
                    # 刷新 Markdown 渲染器缓冲区
                    flushed = md.flush()
                    if flushed:
                        sys.stdout.write(flushed)
                    print()
                if isinstance(data, dict) and data.get("finish_reason") == "length":
                    self._truncated = True
                break

        if not tool_calls_data:
            return full_content

        self.memory.append({
            "role": "assistant",
            "content": full_content,
            "tool_calls": [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": json.dumps(tc["args"], ensure_ascii=False)}}
                for tc in tool_calls_data
            ],
        })

        # 执行工具（带 spinner）
        tool_spinner = Spinner("执行工具")
        self._active_spinner = tool_spinner
        tool_spinner.start()
        results = self._execute_tools_parallel(tool_calls_data)
        tool_spinner.stop()
        self._active_spinner = None

        for r in results:
            if r["ok"]:
                preview = r["result"].split("\n")[0][:70]
                print("    {} {}".format(green("✓"), preview))
            else:
                preview = r["result"][:70]
                print("    {} {}".format(red("✗"), preview))
            self.memory.append({"role": "tool", "tool_call_id": r["tc_id"], "content": r["result"]})

        return None

    def _run_sync(self, context, tools, model, provider):
        from agent_core.ui import cyan, dim, green, red

        msg = chat_completion_resilient(context, tools=tools or None, model=model,
                                        stream=False, provider=provider)
        finish_reason = getattr(msg, "finish_reason", None)
        if finish_reason == "length":
            self._truncated = True

        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            return getattr(msg, "content", "") or ""

        self.memory.append({
            "role": "assistant",
            "content": getattr(msg, "content", "") or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })

        tool_calls_data = []
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            tool_calls_data.append({"id": tc.id, "name": tc.function.name, "args": args})
            args_preview = json.dumps(args, ensure_ascii=False)[:60]
            print("    {} {} {}".format(cyan("▸"), tc.function.name, dim(args_preview)))

        results = self._execute_tools_parallel(tool_calls_data)
        for r in results:
            if r["ok"]:
                preview = r["result"].split("\n")[0][:70]
                print("    {} {}".format(green("✓"), preview))
            else:
                preview = r["result"][:70]
                print("    {} {}".format(red("✗"), preview))
            self.memory.append({"role": "tool", "tool_call_id": r["tc_id"], "content": r["result"]})

        return None

    # ── 状态管理 ─────────────────────────────────────────────

    def set_mode(self, mode: str):
        self.router.mode = mode.lower()
        if mode.lower() != "auto":
            self._provider = mode.lower()

    def _rebuild_system_prompt(self):
        full_prompt = self.system_prompt
        if self._steering_content:
            full_prompt += "\n\n" + self._steering_content
        if self._powers_content:
            full_prompt += "\n\n[Powers]\n" + self._powers_content
        # Agency Agents 角色注入
        if self._agency_role_content:
            full_prompt += "\n\n" + self._agency_role_content
        if self._harness_layer:
            harness_steering = self._harness_layer.get_steering_enhancement()
            if harness_steering:
                full_prompt += "\n\n" + harness_steering
        harness_specs_dir = os.path.join(os.getcwd(), ".harness", "specs")
        if os.path.isdir(harness_specs_dir):
            for f in sorted(os.listdir(harness_specs_dir)):
                if f.endswith(".md"):
                    try:
                        with open(os.path.join(harness_specs_dir, f), "r", encoding="utf-8") as fh:
                            spec_content = fh.read().strip()
                            if spec_content:
                                full_prompt += f"\n\n[Harness Spec: {f}]\n{spec_content}"
                    except Exception:
                        pass
        if self._context_info:
            full_prompt += "\n\n[环境上下文]\n" + self._context_info
        self.memory[0] = {"role": "system", "content": full_prompt}

    def inject_powers_content(self, doc_content: str, steering_content: str = ""):
        parts = []
        if doc_content:
            parts.append(doc_content)
        if steering_content:
            parts.append(steering_content)
        self._powers_content = "\n\n".join(parts) if parts else ""
        self._rebuild_system_prompt()

    def reload_config(self):
        self._steering_mgr.reload()
        self._steering_content = self._steering_mgr.get_active_content()
        self._context_info = self._gather_context()
        self._all_tools = self._get_filtered_tools()
        cache_tools(self._all_tools)
        if self._harness_layer:
            try:
                self._harness_layer = HarnessLayer(agent=self, cwd=os.getcwd())
                self._register_harness_hooks()
            except Exception:
                pass
        self._rebuild_system_prompt()

    def reset(self):
        self._steering_mgr.reload()
        self._steering_content = self._steering_mgr.get_active_content()
        self._context_info = self._gather_context()
        self._powers_content = ""
        self._agency_role_content = ""
        if self._agency_matcher:
            self._agency_matcher.reset()
        self.memory = [{"role": "system", "content": ""}]
        self._rebuild_system_prompt()
        self._total_tokens = 0
        self._all_tools = self._get_filtered_tools()
        self._session_id = None
        self._hygiene.reset()
        self._memory_index.clear()
        cache_tools(self._all_tools)

    # ── 属性 ─────────────────────────────────────────────────

    @property
    def current_mode(self) -> str:
        return self.router.mode

    @property
    def estimated_tokens(self) -> int:
        return self._total_tokens

    @property
    def memory_stats(self) -> str:
        n_user = sum(1 for m in self.memory if m["role"] == "user")
        tokens = count_messages_tokens(self.memory)
        return "{}轮, {}条消息, ~{} tokens".format(n_user, len(self.memory), tokens)
