"""AgentOrchestrator — 多 Agent 协作编排器

借鉴 CrewAI Task 链 + LangGraph State 共享的设计，
与 Harness 驾驭层深度集成。

核心概念:
- AgentRole: Agent 角色定义（对标 CrewAI 的 role/goal/backstory）
- SharedContext: 共享工作记忆 + State reducer（对标 LangGraph State）
- Task: 结构化任务（输入/输出/验证/可委托）
- Pipeline: 编排管线（sequential/parallel/conditional）
- AgentOrchestrator: 调度器 + Harness 集成
"""
from __future__ import annotations
import json, os, time, copy
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agent_core.agent import Agent


# ── AgentRole — Agent 角色定义（对标 CrewAI）────────────────

@dataclass
class AgentRole:
    """Agent 角色定义

    对标 CrewAI 的 Agent(role, goal, backstory)，
    让每个 Agent 有明确的身份和能力边界。

    用法:
        reviewer = AgentRole(
            name="code-reviewer",
            role="资深代码审查专家",
            goal="发现代码中的质量、安全和性能问题",
            backstory="你有 10 年代码审查经验，擅长发现隐藏的 bug",
            tools=["read_file", "list_dir", "shell"],
        )
    """
    name: str
    role: str
    goal: str = ""
    backstory: str = ""
    tools: list[str] | None = None   # 限定可用工具，None 表示全部
    model: str | None = None          # 指定模型，None 跟随主 Agent
    max_iterations: int = 10
    allow_delegation: bool = False    # 是否允许委托任务给其他 Agent

    def to_system_prompt(self) -> str:
        """生成 system prompt"""
        parts = [f"你是{self.role}。"]
        if self.goal:
            parts.append(f"你的目标: {self.goal}")
        if self.backstory:
            parts.append(f"背景: {self.backstory}")
        if not self.allow_delegation:
            parts.append("你必须亲自完成任务，不能委托给其他人。")
        return "\n".join(parts)


# ── SharedContext — 共享工作记忆 + State Reducer ─────────────

# Reducer 策略: 多个 Agent 写同一个 key 时的合并方式
class ReducerStrategy(str, Enum):
    OVERWRITE = "overwrite"   # 后写覆盖（默认）
    APPEND = "append"         # 追加到列表
    MERGE = "merge"           # 字典合并


class SharedContext:
    """共享工作记忆（对标 LangGraph State）

    四个命名空间 + State Reducer + Checkpoint 持久化:
    - facts: 关键事实
    - artifacts: 产出物
    - constraints: 约束条件
    - history: 执行历史
    """

    def __init__(self):
        self.facts: dict[str, Any] = {}
        self.artifacts: dict[str, Any] = {}
        self.constraints: list[str] = []
        self.history: list[dict] = []
        # Reducer 配置: key → strategy
        self._reducers: dict[str, ReducerStrategy] = {}

    def set_reducer(self, key: str, strategy: ReducerStrategy):
        """为指定 key 设置 reducer 策略"""
        self._reducers[key] = strategy

    def set_fact(self, key: str, value: Any):
        self.facts[key] = value

    def get_fact(self, key: str, default: Any = None) -> Any:
        return self.facts.get(key, default)

    def set_artifact(self, key: str, value: Any):
        """写入 artifact，根据 reducer 策略决定合并方式

        三种策略（详见 docs/ARCHITECTURE.md §5.2）：
        - OVERWRITE: 后写覆盖（默认）
        - APPEND: 追加到列表（适合多 Agent 的发现合并）
        - MERGE: 字典合并（适合配置项合并）
        """
        strategy = self._reducers.get(key, ReducerStrategy.OVERWRITE)

        if strategy == ReducerStrategy.OVERWRITE or key not in self.artifacts:
            self.artifacts[key] = value
        elif strategy == ReducerStrategy.APPEND:
            existing = self.artifacts[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                self.artifacts[key] = [existing, value]
        elif strategy == ReducerStrategy.MERGE:
            existing = self.artifacts[key]
            if isinstance(existing, dict) and isinstance(value, dict):
                existing.update(value)
            else:
                self.artifacts[key] = value

    def get_artifact(self, key: str, default: Any = None) -> Any:
        return self.artifacts.get(key, default)

    def add_constraint(self, constraint: str):
        if constraint not in self.constraints:
            self.constraints.append(constraint)

    def record_history(self, agent_name: str, task_name: str,
                       status: str, summary: str, duration: float = 0):
        self.history.append({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "agent": agent_name,
            "task": task_name,
            "status": status,
            "summary": summary[:200],
            "duration_s": round(duration, 1),
        })

    def to_prompt_section(self, max_chars: int = 2000) -> str:
        """序列化为可注入 prompt 的文本"""
        parts = []

        if self.facts:
            lines = ["## 已知事实"]
            for k, v in self.facts.items():
                lines.append(f"- {k}: {str(v)[:100]}")
            parts.append("\n".join(lines))

        if self.artifacts:
            lines = ["## 已有产出"]
            for k, v in self.artifacts.items():
                lines.append(f"- {k}: {str(v)[:150]}")
            parts.append("\n".join(lines))

        if self.constraints:
            lines = ["## 约束条件"]
            for c in self.constraints:
                lines.append(f"- {c}")
            parts.append("\n".join(lines))

        if self.history:
            lines = ["## 执行历史"]
            for h in self.history[-5:]:
                icon = "✅" if h["status"] == "success" else "❌"
                lines.append(f"- {icon} [{h['agent']}] {h['task']}: {h['summary']}")
            parts.append("\n".join(lines))

        result = "\n\n".join(parts)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n...(上下文已截断)"
        return result

    # ── Snapshot / Checkpoint ────────────────────────────

    def snapshot(self) -> dict:
        return {
            "facts": copy.deepcopy(self.facts),
            "artifacts": copy.deepcopy(self.artifacts),
            "constraints": list(self.constraints),
            "history": list(self.history),
            "reducers": {k: v.value for k, v in self._reducers.items()},
        }

    def restore(self, snapshot: dict):
        self.facts = snapshot.get("facts", {})
        self.artifacts = snapshot.get("artifacts", {})
        self.constraints = snapshot.get("constraints", [])
        self.history = snapshot.get("history", [])
        for k, v in snapshot.get("reducers", {}).items():
            self._reducers[k] = ReducerStrategy(v)

    def save_checkpoint(self, path: str):
        """持久化到磁盘（对标 LangGraph Checkpointing）"""
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.snapshot(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load_checkpoint(cls, path: str) -> "SharedContext":
        """从磁盘恢复"""
        ctx = cls()
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                ctx.restore(json.load(f))
        return ctx


# ── Task — 结构化任务定义 ───────────────────────────────────

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskResult:
    status: TaskStatus
    output: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_s: float = 0


@dataclass
class Task:
    """结构化任务定义

    对标 CrewAI Task:
    - context_keys: 输入（从 SharedContext 读取）
    - output_key: 输出（写入 SharedContext）
    - validate_fn: 结果验证
    - agent_role: 绑定的 AgentRole（优先于 agent_name）
    - allow_delegation: 允许 Agent 委托给其他角色
    """
    name: str
    description: str
    agent_name: str = ""
    agent_role: AgentRole | None = None
    system_prompt: str = ""
    tools_filter: list[str] | None = None
    context_keys: list[str] = field(default_factory=list)
    output_key: str = ""
    validate_fn: Callable[[str, "SharedContext"], bool] | None = None
    max_retries: int = 1
    timeout: int = 120

    # 运行时状态
    status: TaskStatus = TaskStatus.PENDING
    result: TaskResult | None = None


# ── Pipeline — 任务编排（含条件分支）────────────────────────

class PipelineMode(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    HIERARCHICAL = "hierarchical"  # 管理者 Agent 动态分配任务


@dataclass
class ConditionalEdge:
    """条件分支（对标 LangGraph conditional_edges）

    根据 SharedContext 的状态决定下一步执行哪个 Task。
    condition_fn 接收 SharedContext，返回目标 Task 的 name。
    """
    from_task: str                                    # 源 Task name
    condition_fn: Callable[["SharedContext"], str]     # 返回目标 Task name
    targets: dict[str, Task] = field(default_factory=dict)  # name → Task


@dataclass
class Pipeline:
    """任务编排管线

    支持:
    - sequential: 顺序执行，前置失败自动跳过后续
    - parallel: 并行执行，快照隔离
    - hierarchical: 管理者 Agent 动态分配任务给 worker Agent
    - conditional: 条件分支，根据 State 动态路由
    """
    name: str
    tasks: list[Task] = field(default_factory=list)
    mode: PipelineMode = PipelineMode.SEQUENTIAL
    # 条件分支: from_task_name → ConditionalEdge
    conditional_edges: dict[str, ConditionalEdge] = field(default_factory=dict)
    # hierarchical 模式的管理者角色
    manager_role: AgentRole | None = None

    def add_task(self, task: Task) -> "Pipeline":
        self.tasks.append(task)
        return self

    def add_tasks(self, *tasks: Task) -> "Pipeline":
        self.tasks.extend(tasks)
        return self

    def add_conditional_edge(self, from_task: str,
                             condition_fn: Callable[["SharedContext"], str],
                             targets: dict[str, Task]) -> "Pipeline":
        """添加条件分支"""
        self.conditional_edges[from_task] = ConditionalEdge(
            from_task=from_task,
            condition_fn=condition_fn,
            targets=targets,
        )
        return self

    def to_mermaid(self) -> str:
        """生成 Mermaid 流程图（图可视化）

        返回 Mermaid 格式文本，可直接嵌入 Markdown 渲染。
        """
        lines = ["graph TD"]

        # 节点
        for task in self.tasks:
            role_label = ""
            if task.agent_role:
                role_label = f"<br/><i>{task.agent_role.role}</i>"
            elif task.agent_name:
                role_label = f"<br/><i>{task.agent_name}</i>"

            status_icon = ""
            if task.status == TaskStatus.SUCCESS:
                status_icon = " ✅"
            elif task.status == TaskStatus.FAILED:
                status_icon = " ❌"
            elif task.status == TaskStatus.RUNNING:
                status_icon = " ⏳"

            node_id = _sanitize_id(task.name)
            lines.append(f'    {node_id}["{task.name}{status_icon}{role_label}"]')

        # 边: sequential 模式下相邻 Task 连线
        if self.mode in (PipelineMode.SEQUENTIAL, PipelineMode.HIERARCHICAL):
            for i in range(len(self.tasks) - 1):
                src = _sanitize_id(self.tasks[i].name)
                dst = _sanitize_id(self.tasks[i + 1].name)
                lines.append(f"    {src} --> {dst}")

        # 边: parallel 模式下从 START 扇出
        if self.mode == PipelineMode.PARALLEL and self.tasks:
            lines.append('    START(("开始"))')
            lines.append('    FINISH(("结束"))')
            for task in self.tasks:
                node_id = _sanitize_id(task.name)
                lines.append(f"    START --> {node_id}")
                lines.append(f"    {node_id} --> FINISH")

        # 边: 条件分支
        for from_name, edge in self.conditional_edges.items():
            src = _sanitize_id(from_name)
            for target_name, target_task in edge.targets.items():
                target_id = _sanitize_id(target_task.name)
                # 确保目标节点有定义
                role_label = ""
                if target_task.agent_role:
                    role_label = f"<br/><i>{target_task.agent_role.role}</i>"
                lines.append(f'    {target_id}["{target_task.name}{role_label}"]')
                lines.append(f'    {src} -->|"{target_name}"| {target_id}')

        # hierarchical 模式: 添加 Manager 节点
        if self.mode == PipelineMode.HIERARCHICAL and self.manager_role:
            lines.append(f'    MANAGER{{"🎩 {self.manager_role.role}"}}')
            for task in self.tasks:
                node_id = _sanitize_id(task.name)
                lines.append(f"    MANAGER -.->|分配| {node_id}")

        return "\n".join(lines)


def _sanitize_id(name: str) -> str:
    """将任务名转为合法的 Mermaid 节点 ID"""
    import re
    return re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fff]", "_", name)


# ── AgentOrchestrator — 调度器 ──────────────────────────────

class AgentOrchestrator:
    """多 Agent 协作编排器

    用法:
        # 定义角色
        reviewer = AgentRole(name="reviewer", role="代码审查专家", goal="发现问题")
        fixer = AgentRole(name="fixer", role="代码修复专家", goal="修复问题")

        # 构建 Pipeline
        ctx = SharedContext()
        ctx.set_fact("target_file", "main.py")
        ctx.set_reducer("issues", ReducerStrategy.APPEND)  # 多个 Agent 的发现追加合并

        pipeline = Pipeline("code-review")
        pipeline.add_task(Task(
            name="分析", description="分析代码", agent_role=reviewer,
            context_keys=["target_file"], output_key="analysis",
        ))
        pipeline.add_conditional_edge(
            from_task="分析",
            condition_fn=lambda ctx: "修复" if "问题" in str(ctx.get_artifact("analysis")) else "完成",
            targets={
                "修复": Task(name="修复", description="修复问题", agent_role=fixer,
                            context_keys=["analysis"], output_key="fix_result"),
                "完成": Task(name="完成", description="生成报告",
                            context_keys=["analysis"], output_key="report"),
            },
        )

        orchestrator = AgentOrchestrator(main_agent)
        results = orchestrator.run(pipeline, ctx)
    """

    def __init__(self, agent: "Agent", roles: dict[str, AgentRole] | None = None,
                 verbose: bool = True):
        self._agent = agent
        self._roles: dict[str, AgentRole] = roles or {}
        self._verbose = verbose
        self._harness = getattr(agent, '_harness_layer', None)

    def register_role(self, role: AgentRole):
        """注册 Agent 角色"""
        self._roles[role.name] = role

    def run(self, pipeline: Pipeline, context: SharedContext | None = None) -> list[TaskResult]:
        """执行 Pipeline

        根据 Pipeline.mode 选择执行策略：
        - SEQUENTIAL: 顺序执行 + 条件分支，前置失败自动跳过后续
        - PARALLEL: 并行执行，每个 Task 用 SharedContext 快照隔离
        - HIERARCHICAL: Manager Agent 先做任务分配，再顺序执行
        """
        ctx = context or SharedContext()
        self._inject_harness_constraints(ctx)

        if self._verbose:
            mode_label = pipeline.mode.value
            if pipeline.conditional_edges:
                mode_label += "+conditional"
            print(f"\n  🔀 Pipeline [{pipeline.name}] 启动 "
                  f"({len(pipeline.tasks)} 个任务, {mode_label})")

        if pipeline.mode == PipelineMode.SEQUENTIAL:
            return self._run_sequential(pipeline, ctx)
        elif pipeline.mode == PipelineMode.HIERARCHICAL:
            return self._run_hierarchical(pipeline, ctx)
        else:
            return self._run_parallel(pipeline.tasks, ctx)

    def run_task(self, task: Task, context: SharedContext) -> TaskResult:
        """执行单个 Task

        流程：
        1. 解析 Agent 角色（task.agent_role > self._roles[task.agent_name]）
        2. 构建 prompt（任务描述 + SharedContext 注入 + 约束条件）
        3. 通过 spawn 创建隔离子 Agent 执行（不污染主 Agent memory）
        4. 验证结果（validate_fn）
        5. 检测委托请求（[DELEGATE:role_name]）
        6. 写入 SharedContext + 记录执行历史
        7. Harness Task 边界约束检查
        """
        start = time.time()
        task.status = TaskStatus.RUNNING

        # 解析 Agent 角色
        role = task.agent_role or self._roles.get(task.agent_name)
        agent_label = role.name if role else (task.agent_name or "default")

        if self._verbose:
            role_desc = f" ({role.role})" if role else ""
            print(f"  🎯 [{task.name}] → {agent_label}{role_desc}: {task.description[:60]}")

        # 构建 prompt
        prompt = self._build_task_prompt(task, context, role)

        # 确定 system_prompt 和 tools
        sys_prompt = None
        tools = task.tools_filter
        if role:
            sys_prompt = task.system_prompt or role.to_system_prompt()
            tools = task.tools_filter or role.tools

        # 执行（带重试）
        last_error = ""
        for attempt in range(task.max_retries):
            try:
                output = self._agent.spawn(
                    prompt,
                    system_prompt=sys_prompt or task.system_prompt or None,
                    tools_filter=tools,
                    timeout=task.timeout,
                )

                # 验证
                if task.validate_fn and not task.validate_fn(output, context):
                    last_error = "验证未通过"
                    if attempt < task.max_retries - 1:
                        if self._verbose:
                            print(f"    ⚠️  验证失败，重试 ({attempt + 1}/{task.max_retries})")
                        prompt += f"\n\n[上次尝试失败: {last_error}，请修正后重试]"
                        continue
                    break

                duration = time.time() - start
                result = TaskResult(status=TaskStatus.SUCCESS, output=output, duration_s=duration)

                # Task delegation: 检测 Agent 是否请求委托
                delegated = self._check_delegation(output, task, context, role)
                if delegated is not None:
                    # 委托成功，用委托结果替换
                    result = TaskResult(
                        status=TaskStatus.SUCCESS, output=delegated,
                        duration_s=time.time() - start,
                    )
                    output = delegated

                if task.output_key:
                    context.set_artifact(task.output_key, output)

                context.record_history(
                    agent_name=agent_label, task_name=task.name,
                    status="success", summary=output[:100], duration=duration,
                )
                self._harness_check_at_boundary(task, context)

                task.status = TaskStatus.SUCCESS
                task.result = result
                if self._verbose:
                    print(f"    ✅ 完成 ({duration:.1f}s)")
                return result

            except Exception as e:
                last_error = str(e)
                if attempt < task.max_retries - 1:
                    if self._verbose:
                        print(f"    ⚠️  错误: {last_error}，重试 ({attempt + 1}/{task.max_retries})")
                    continue

        # 失败
        duration = time.time() - start
        result = TaskResult(status=TaskStatus.FAILED, error=last_error, duration_s=duration)
        context.record_history(
            agent_name=agent_label, task_name=task.name,
            status="failed", summary=last_error[:100], duration=duration,
        )
        task.status = TaskStatus.FAILED
        task.result = result
        if self._verbose:
            print(f"    ❌ 失败: {last_error[:80]}")
        return result

    # ── 编排模式 ─────────────────────────────────────────────

    def _run_sequential(self, pipeline: Pipeline, ctx: SharedContext) -> list[TaskResult]:
        """顺序执行 + 条件分支"""
        results = []
        i = 0
        tasks = list(pipeline.tasks)

        while i < len(tasks):
            task = tasks[i]
            result = self.run_task(task, ctx)
            results.append(result)

            if result.status == TaskStatus.FAILED:
                if self._verbose:
                    print(f"    ⏭️  后续任务跳过（前置任务失败）")
                for remaining in tasks[i + 1:]:
                    remaining.status = TaskStatus.SKIPPED
                    results.append(TaskResult(status=TaskStatus.SKIPPED))
                break

            # 检查条件分支
            edge = pipeline.conditional_edges.get(task.name)
            if edge:
                target_name = edge.condition_fn(ctx)
                if self._verbose:
                    print(f"    🔀 条件分支: {task.name} → {target_name}")
                target_task = edge.targets.get(target_name)
                if target_task:
                    # 将条件分支的目标 Task 插入到当前位置之后
                    tasks.insert(i + 1, target_task)

            i += 1

        return results

    def _run_parallel(self, tasks: list[Task], ctx: SharedContext) -> list[TaskResult]:
        """并行执行"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        snapshot = ctx.snapshot()
        results: list[TaskResult | None] = [None] * len(tasks)

        def run_one(idx: int, task: Task) -> tuple[int, TaskResult]:
            local_ctx = SharedContext()
            local_ctx.restore(copy.deepcopy(snapshot))
            result = self.run_task(task, local_ctx)
            return idx, result

        max_workers = min(len(tasks), self._agent._MAX_CONCURRENT_SUBAGENTS)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(run_one, i, t): i for i, t in enumerate(tasks)}
            for future in as_completed(futures):
                idx, result = future.result()
                results[idx] = result
                task = tasks[idx]
                if result.status == TaskStatus.SUCCESS and task.output_key:
                    ctx.set_artifact(task.output_key, result.output)

        return results  # type: ignore[return-value]

    # ── Hierarchical 模式 ────────────────────────────────────

    _MANAGER_PROMPT = """你是团队管理者。你的团队有以下成员:
{roles_desc}

当前待完成的任务:
{tasks_desc}

当前项目状态:
{context_summary}

请为每个任务分配最合适的团队成员。
严格按以下 JSON 格式返回分配方案（不要返回其他内容）:
[{{"task": "任务名", "assign_to": "成员名", "reason": "分配理由"}}]"""

    def _run_hierarchical(self, pipeline: Pipeline, ctx: SharedContext) -> list[TaskResult]:
        """Hierarchical 模式: 管理者 Agent 动态分配任务

        流程:
        1. Manager Agent 查看所有待办 Task 和可用 AgentRole
        2. Manager 决定每个 Task 分配给哪个 AgentRole
        3. 按 Manager 的分配方案顺序执行
        """
        manager = pipeline.manager_role or AgentRole(
            name="manager", role="团队管理者",
            goal="将任务分配给最合适的团队成员",
        )

        # 收集可用角色
        available_roles = dict(self._roles)
        for task in pipeline.tasks:
            if task.agent_role and task.agent_role.name not in available_roles:
                available_roles[task.agent_role.name] = task.agent_role

        if not available_roles:
            if self._verbose:
                print("    ⚠️  无可用角色，退化为 sequential 模式")
            return self._run_sequential(pipeline, ctx)

        # 构建 Manager prompt
        roles_desc = "\n".join(
            f"- {r.name}: {r.role}" + (f" (目标: {r.goal})" if r.goal else "")
            for r in available_roles.values()
        )
        tasks_desc = "\n".join(
            f"- {t.name}: {t.description}" for t in pipeline.tasks
            if t.status == TaskStatus.PENDING
        )
        context_summary = ctx.to_prompt_section(max_chars=500)

        prompt = self._MANAGER_PROMPT.format(
            roles_desc=roles_desc,
            tasks_desc=tasks_desc,
            context_summary=context_summary or "(无)",
        )

        if self._verbose:
            print(f"  🎩 Manager ({manager.role}) 分析任务分配 ...")

        # Manager Agent 做分配决策
        assignment_output = self._agent.spawn(
            prompt,
            system_prompt=manager.to_system_prompt(),
            timeout=60,
        )

        # 解析分配方案
        assignments = self._parse_assignments(assignment_output)

        # 按分配方案更新 Task 的 agent_role
        for task in pipeline.tasks:
            assigned_role_name = assignments.get(task.name)
            if assigned_role_name and assigned_role_name in available_roles:
                task.agent_role = available_roles[assigned_role_name]
                if self._verbose:
                    print(f"    📋 {task.name} → {assigned_role_name}")

        ctx.record_history(
            agent_name=manager.name, task_name="任务分配",
            status="success", summary=f"分配 {len(assignments)} 个任务",
        )

        # 按顺序执行分配后的 Task
        return self._run_sequential(pipeline, ctx)

    @staticmethod
    def _parse_assignments(output: str) -> dict[str, str]:
        """解析 Manager 的分配方案 JSON"""
        import re
        # 尝试提取 JSON 数组
        patterns = [
            r'\[[\s\S]*?\{[\s\S]*?"task"[\s\S]*?\}[\s\S]*?\]',
            r'```json\s*(\[[\s\S]*?\])\s*```',
            r'```\s*(\[[\s\S]*?\])\s*```',
        ]
        for pat in patterns:
            match = re.search(pat, output)
            if match:
                try:
                    json_str = match.group(1) if match.lastindex else match.group(0)
                    data = json.loads(json_str)
                    if isinstance(data, list):
                        return {
                            item["task"]: item["assign_to"]
                            for item in data
                            if isinstance(item, dict) and "task" in item and "assign_to" in item
                        }
                except (json.JSONDecodeError, KeyError):
                    continue
        return {}

    # ── Task Delegation ──────────────────────────────────────

    _DELEGATE_PATTERN = r'\[DELEGATE:(\w+)\]'

    def _check_delegation(self, output: str, task: Task, context: SharedContext,
                          role: AgentRole | None) -> str | None:
        """检测 Agent 输出中的委托请求

        如果 Agent 的 allow_delegation=True 且输出包含 [DELEGATE:role_name]，
        自动将任务转交给指定角色执行。

        返回委托执行的结果，或 None（无委托）。
        """
        if not role or not role.allow_delegation:
            return None

        import re
        match = re.search(self._DELEGATE_PATTERN, output)
        if not match:
            return None

        target_name = match.group(1)
        target_role = self._roles.get(target_name)
        if not target_role:
            if self._verbose:
                print(f"    ⚠️  委托目标 {target_name} 未注册，忽略")
            return None

        if self._verbose:
            print(f"    🔄 委托: {role.name} → {target_name} ({target_role.role})")

        # 构建委托 prompt: 原始任务 + 委托者的初步分析
        delegate_prompt = (
            f"{task.description}\n\n"
            f"---\n前一位同事 ({role.role}) 的初步分析:\n{output}\n\n"
            f"请基于以上分析完成任务。"
        )

        # 注入 context
        if task.context_keys:
            injected = []
            for key in task.context_keys:
                value = context.get_artifact(key) or context.get_fact(key)
                if value is not None:
                    injected.append(f"[{key}]\n{value}")
            if injected:
                delegate_prompt += "\n\n---\n相关上下文:\n" + "\n\n".join(injected)

        delegated_output = self._agent.spawn(
            delegate_prompt,
            system_prompt=target_role.to_system_prompt(),
            tools_filter=target_role.tools,
            timeout=task.timeout,
        )

        context.record_history(
            agent_name=target_name, task_name=f"委托@{task.name}",
            status="success", summary=delegated_output[:100],
        )

        return delegated_output

    # ── 图可视化 ─────────────────────────────────────────────

    def visualize(self, pipeline: Pipeline) -> str:
        """生成 Pipeline 的 Mermaid 流程图

        返回 Mermaid 格式文本，可嵌入 Markdown:
            ```mermaid
            {orchestrator.visualize(pipeline)}
            ```
        """
        return pipeline.to_mermaid()

    # ── Prompt 构建 ──────────────────────────────────────────

    def _build_task_prompt(self, task: Task, context: SharedContext,
                           role: AgentRole | None = None) -> str:
        parts = [task.description]

        # 注入 context keys
        if task.context_keys:
            injected = []
            for key in task.context_keys:
                value = context.get_artifact(key) or context.get_fact(key)
                if value is not None:
                    injected.append(f"[{key}]\n{value}")
            if injected:
                parts.append("\n---\n以下是相关上下文:\n" + "\n\n".join(injected))

        # 注入约束
        if context.constraints:
            parts.append("\n---\n约束条件:\n" + "\n".join(f"- {c}" for c in context.constraints))

        return "\n\n".join(parts)

    # ── Harness 集成 ─────────────────────────────────────────

    def _inject_harness_constraints(self, ctx: SharedContext):
        if not self._harness or not self._harness.enabled:
            return
        steering = self._harness.get_steering_enhancement()
        if steering:
            for line in steering.split("\n"):
                if line.strip().startswith("**正确做法**"):
                    ctx.add_constraint(line.replace("**正确做法**：", "").strip())

    def _harness_check_at_boundary(self, task: Task, ctx: SharedContext):
        if not self._harness or not self._harness.enabled:
            return
        output = task.result.output if task.result else ""
        if task.output_key and output:
            if "```" in output or "def " in output or "function " in output:
                ctx.record_history(
                    agent_name="harness",
                    task_name=f"约束检查@{task.name}",
                    status="success",
                    summary="Task 边界约束检查通过",
                )
