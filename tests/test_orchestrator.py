"""AgentOrchestrator 单元测试

测试 SharedContext、Task、Pipeline、AgentOrchestrator 的核心功能。
"""
import pytest
from unittest.mock import MagicMock, patch
from agent_core.orchestrator import (
    SharedContext, Task, TaskResult, TaskStatus,
    Pipeline, PipelineMode, AgentOrchestrator,
)


# ══════════════════════════════════════════════════════════
# SharedContext
# ══════════════════════════════════════════════════════════


class TestSharedContext:

    def test_facts_crud(self):
        ctx = SharedContext()
        ctx.set_fact("project", "nexus-agent")
        assert ctx.get_fact("project") == "nexus-agent"
        assert ctx.get_fact("missing") is None
        assert ctx.get_fact("missing", "default") == "default"

    def test_artifacts_crud(self):
        ctx = SharedContext()
        ctx.set_artifact("analysis", "代码结构良好")
        assert ctx.get_artifact("analysis") == "代码结构良好"

    def test_constraints(self):
        ctx = SharedContext()
        ctx.add_constraint("禁止使用 eval()")
        ctx.add_constraint("禁止使用 eval()")  # 去重
        assert len(ctx.constraints) == 1
        ctx.add_constraint("使用 $t() 国际化")
        assert len(ctx.constraints) == 2

    def test_history(self):
        ctx = SharedContext()
        ctx.record_history("reviewer", "分析代码", "success", "发现 3 个问题")
        assert len(ctx.history) == 1
        assert ctx.history[0]["agent"] == "reviewer"
        assert ctx.history[0]["status"] == "success"

    def test_to_prompt_section(self):
        ctx = SharedContext()
        ctx.set_fact("lang", "Python")
        ctx.set_artifact("result", "分析完成")
        ctx.add_constraint("遵守 PEP8")
        ctx.record_history("agent", "task", "success", "done")
        prompt = ctx.to_prompt_section()
        assert "Python" in prompt
        assert "分析完成" in prompt
        assert "PEP8" in prompt
        assert "done" in prompt

    def test_to_prompt_section_empty(self):
        ctx = SharedContext()
        assert ctx.to_prompt_section() == ""

    def test_to_prompt_section_truncation(self):
        ctx = SharedContext()
        ctx.set_fact("big", "x" * 3000)
        prompt = ctx.to_prompt_section(max_chars=100)
        assert len(prompt) <= 120  # 100 + 截断提示
        assert "截断" in prompt

    def test_snapshot_and_restore(self):
        ctx = SharedContext()
        ctx.set_fact("a", 1)
        ctx.set_artifact("b", "hello")
        ctx.add_constraint("c1")
        snap = ctx.snapshot()

        # 修改原始
        ctx.set_fact("a", 999)
        ctx.set_artifact("b", "changed")
        assert ctx.get_fact("a") == 999

        # 恢复
        ctx.restore(snap)
        assert ctx.get_fact("a") == 1
        assert ctx.get_artifact("b") == "hello"

    def test_snapshot_is_deep_copy(self):
        ctx = SharedContext()
        ctx.set_fact("list", [1, 2, 3])
        snap = ctx.snapshot()
        ctx.facts["list"].append(4)
        assert snap["facts"]["list"] == [1, 2, 3]


# ══════════════════════════════════════════════════════════
# Task
# ══════════════════════════════════════════════════════════


class TestTask:

    def test_task_defaults(self):
        t = Task(name="test", description="测试任务")
        assert t.status == TaskStatus.PENDING
        assert t.result is None
        assert t.max_retries == 1
        assert t.timeout == 120

    def test_task_with_context_keys(self):
        t = Task(
            name="analyze",
            description="分析代码",
            context_keys=["project_dir", "target_file"],
            output_key="analysis",
        )
        assert len(t.context_keys) == 2
        assert t.output_key == "analysis"


# ══════════════════════════════════════════════════════════
# Pipeline
# ══════════════════════════════════════════════════════════


class TestPipeline:

    def test_pipeline_add_task(self):
        p = Pipeline("test")
        p.add_task(Task(name="t1", description="d1"))
        p.add_task(Task(name="t2", description="d2"))
        assert len(p.tasks) == 2

    def test_pipeline_add_tasks(self):
        p = Pipeline("test")
        p.add_tasks(
            Task(name="t1", description="d1"),
            Task(name="t2", description="d2"),
        )
        assert len(p.tasks) == 2

    def test_pipeline_chaining(self):
        p = Pipeline("test")
        result = p.add_task(Task(name="t1", description="d1"))
        assert result is p  # 链式调用


# ══════════════════════════════════════════════════════════
# AgentOrchestrator
# ══════════════════════════════════════════════════════════


class TestOrchestrator:

    def _make_mock_agent(self, spawn_return="mock result"):
        agent = MagicMock()
        agent.spawn.return_value = spawn_return
        agent._MAX_CONCURRENT_SUBAGENTS = 3
        agent._harness_layer = None
        return agent

    def test_sequential_single_task(self):
        agent = self._make_mock_agent("分析结果: 代码质量良好")
        orch = AgentOrchestrator(agent, verbose=False)

        ctx = SharedContext()
        pipeline = Pipeline("test")
        pipeline.add_task(Task(
            name="分析",
            description="分析代码",
            output_key="analysis",
        ))

        results = orch.run(pipeline, ctx)
        assert len(results) == 1
        assert results[0].status == TaskStatus.SUCCESS
        assert ctx.get_artifact("analysis") == "分析结果: 代码质量良好"

    def test_sequential_chain(self):
        """顺序执行: 前一个 Task 的输出自动注入后一个"""
        call_count = [0]

        def mock_spawn(task, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return "发现 3 个安全问题"
            return "报告: 基于分析，建议修复 3 个问题"

        agent = self._make_mock_agent()
        agent.spawn.side_effect = mock_spawn

        orch = AgentOrchestrator(agent, verbose=False)
        ctx = SharedContext()

        pipeline = Pipeline("review")
        pipeline.add_task(Task(
            name="分析",
            description="分析代码安全性",
            output_key="analysis",
        ))
        pipeline.add_task(Task(
            name="报告",
            description="生成审查报告",
            context_keys=["analysis"],
            output_key="report",
        ))

        results = orch.run(pipeline, ctx)
        assert len(results) == 2
        assert results[0].status == TaskStatus.SUCCESS
        assert results[1].status == TaskStatus.SUCCESS
        assert ctx.get_artifact("analysis") == "发现 3 个安全问题"
        assert "报告" in ctx.get_artifact("report")

        # 验证第二个 Task 的 prompt 中包含了第一个的输出
        second_call_args = agent.spawn.call_args_list[1]
        prompt = second_call_args[0][0]
        assert "发现 3 个安全问题" in prompt

    def test_sequential_fail_skips_rest(self):
        """顺序模式下前置任务失败，后续任务跳过"""
        agent = self._make_mock_agent()
        agent.spawn.side_effect = Exception("LLM 调用失败")

        orch = AgentOrchestrator(agent, verbose=False)
        ctx = SharedContext()

        pipeline = Pipeline("test")
        pipeline.add_task(Task(name="t1", description="d1"))
        pipeline.add_task(Task(name="t2", description="d2"))

        results = orch.run(pipeline, ctx)
        assert results[0].status == TaskStatus.FAILED
        assert results[1].status == TaskStatus.SKIPPED

    def test_parallel_execution(self):
        """并行执行: 多个 Task 同时运行"""
        agent = self._make_mock_agent("并行结果")
        orch = AgentOrchestrator(agent, verbose=False)
        ctx = SharedContext()

        pipeline = Pipeline("parallel-test", mode=PipelineMode.PARALLEL)
        pipeline.add_task(Task(name="t1", description="d1", output_key="r1"))
        pipeline.add_task(Task(name="t2", description="d2", output_key="r2"))

        results = orch.run(pipeline, ctx)
        assert len(results) == 2
        assert all(r.status == TaskStatus.SUCCESS for r in results)
        assert ctx.get_artifact("r1") == "并行结果"
        assert ctx.get_artifact("r2") == "并行结果"

    def test_task_with_validation(self):
        """Task 带验证函数"""
        agent = self._make_mock_agent("PASS: 所有检查通过")
        orch = AgentOrchestrator(agent, verbose=False)
        ctx = SharedContext()

        def validate(output, ctx):
            return "PASS" in output

        pipeline = Pipeline("test")
        pipeline.add_task(Task(
            name="验证",
            description="验证功能",
            validate_fn=validate,
            output_key="result",
        ))

        results = orch.run(pipeline, ctx)
        assert results[0].status == TaskStatus.SUCCESS

    def test_task_validation_failure(self):
        """验证失败时 Task 标记为失败"""
        agent = self._make_mock_agent("FAIL: 有问题")
        orch = AgentOrchestrator(agent, verbose=False)
        ctx = SharedContext()

        def validate(output, ctx):
            return "PASS" in output

        pipeline = Pipeline("test")
        pipeline.add_task(Task(
            name="验证",
            description="验证功能",
            validate_fn=validate,
            max_retries=1,
        ))

        results = orch.run(pipeline, ctx)
        assert results[0].status == TaskStatus.FAILED

    def test_task_retry_on_validation_failure(self):
        """验证失败时重试"""
        call_count = [0]

        def mock_spawn(task, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return "FAIL: 第一次"
            return "PASS: 第二次修正"

        agent = self._make_mock_agent()
        agent.spawn.side_effect = mock_spawn

        orch = AgentOrchestrator(agent, verbose=False)
        ctx = SharedContext()

        pipeline = Pipeline("test")
        pipeline.add_task(Task(
            name="验证",
            description="验证功能",
            validate_fn=lambda output, ctx: "PASS" in output,
            max_retries=2,
            output_key="result",
        ))

        results = orch.run(pipeline, ctx)
        assert results[0].status == TaskStatus.SUCCESS
        assert call_count[0] == 2

    def test_context_injection(self):
        """SharedContext 数据正确注入到 Task prompt"""
        agent = self._make_mock_agent("ok")
        orch = AgentOrchestrator(agent, verbose=False)

        ctx = SharedContext()
        ctx.set_fact("project_dir", "/home/user/project")
        ctx.set_artifact("prev_analysis", "发现 5 个问题")

        pipeline = Pipeline("test")
        pipeline.add_task(Task(
            name="修复",
            description="修复代码问题",
            context_keys=["project_dir", "prev_analysis"],
        ))

        orch.run(pipeline, ctx)

        # 验证 spawn 收到的 prompt 包含注入的上下文
        call_args = agent.spawn.call_args[0][0]
        assert "/home/user/project" in call_args
        assert "发现 5 个问题" in call_args

    def test_constraints_injection(self):
        """约束条件注入到 Task prompt"""
        agent = self._make_mock_agent("ok")
        orch = AgentOrchestrator(agent, verbose=False)

        ctx = SharedContext()
        ctx.add_constraint("禁止使用 eval()")
        ctx.add_constraint("使用 $t() 国际化")

        pipeline = Pipeline("test")
        pipeline.add_task(Task(name="编码", description="写代码"))

        orch.run(pipeline, ctx)

        call_args = agent.spawn.call_args[0][0]
        assert "eval()" in call_args
        assert "$t()" in call_args

    def test_history_tracking(self):
        """执行历史正确记录"""
        agent = self._make_mock_agent("done")
        orch = AgentOrchestrator(agent, verbose=False)
        ctx = SharedContext()

        pipeline = Pipeline("test")
        pipeline.add_task(Task(name="t1", description="d1", agent_name="reviewer"))
        pipeline.add_task(Task(name="t2", description="d2", agent_name="coder"))

        orch.run(pipeline, ctx)

        assert len(ctx.history) == 2
        assert ctx.history[0]["agent"] == "reviewer"
        assert ctx.history[1]["agent"] == "coder"
        assert all(h["status"] == "success" for h in ctx.history)

    def test_harness_constraints_injected(self):
        """Harness 层约束自动注入 SharedContext"""
        agent = self._make_mock_agent("ok")
        # 模拟 Harness 层
        harness = MagicMock()
        harness.enabled = True
        harness.get_steering_enhancement.return_value = (
            "### 高频违规\n**正确做法**：使用 ButtonComponent 替代原生 button"
        )
        agent._harness_layer = harness

        orch = AgentOrchestrator(agent, verbose=False)
        ctx = SharedContext()

        pipeline = Pipeline("test")
        pipeline.add_task(Task(name="t1", description="d1"))
        orch.run(pipeline, ctx)

        assert any("ButtonComponent" in c for c in ctx.constraints)


from agent_core.orchestrator import AgentRole, ReducerStrategy, ConditionalEdge


# ══════════════════════════════════════════════════════════
# AgentRole
# ══════════════════════════════════════════════════════════


class TestAgentRole:

    def test_to_system_prompt(self):
        role = AgentRole(
            name="reviewer",
            role="代码审查专家",
            goal="发现代码问题",
            backstory="10 年经验",
        )
        prompt = role.to_system_prompt()
        assert "代码审查专家" in prompt
        assert "发现代码问题" in prompt
        assert "10 年经验" in prompt

    def test_delegation_flag(self):
        role = AgentRole(name="r", role="r", allow_delegation=False)
        assert "不能委托" in role.to_system_prompt()

        role2 = AgentRole(name="r", role="r", allow_delegation=True)
        assert "不能委托" not in role2.to_system_prompt()


# ══════════════════════════════════════════════════════════
# Reducer 策略
# ══════════════════════════════════════════════════════════


class TestReducerStrategy:

    def test_overwrite_default(self):
        ctx = SharedContext()
        ctx.set_artifact("key", "v1")
        ctx.set_artifact("key", "v2")
        assert ctx.get_artifact("key") == "v2"

    def test_append_strategy(self):
        ctx = SharedContext()
        ctx.set_reducer("issues", ReducerStrategy.APPEND)
        ctx.set_artifact("issues", "问题1")
        ctx.set_artifact("issues", "问题2")
        assert ctx.get_artifact("issues") == ["问题1", "问题2"]

    def test_append_to_existing_list(self):
        ctx = SharedContext()
        ctx.set_reducer("items", ReducerStrategy.APPEND)
        ctx.artifacts["items"] = ["a"]
        ctx.set_artifact("items", "b")
        assert ctx.get_artifact("items") == ["a", "b"]

    def test_merge_strategy(self):
        ctx = SharedContext()
        ctx.set_reducer("config", ReducerStrategy.MERGE)
        ctx.set_artifact("config", {"a": 1})
        ctx.set_artifact("config", {"b": 2})
        assert ctx.get_artifact("config") == {"a": 1, "b": 2}

    def test_merge_overwrite_key(self):
        ctx = SharedContext()
        ctx.set_reducer("config", ReducerStrategy.MERGE)
        ctx.set_artifact("config", {"a": 1})
        ctx.set_artifact("config", {"a": 99})
        assert ctx.get_artifact("config") == {"a": 99}


# ══════════════════════════════════════════════════════════
# Checkpoint 持久化
# ══════════════════════════════════════════════════════════


class TestCheckpoint:

    def test_save_and_load(self, tmp_path):
        ctx = SharedContext()
        ctx.set_fact("lang", "Python")
        ctx.set_artifact("result", "ok")
        ctx.add_constraint("no eval")
        ctx.set_reducer("items", ReducerStrategy.APPEND)

        path = str(tmp_path / "checkpoint.json")
        ctx.save_checkpoint(path)

        loaded = SharedContext.load_checkpoint(path)
        assert loaded.get_fact("lang") == "Python"
        assert loaded.get_artifact("result") == "ok"
        assert "no eval" in loaded.constraints
        # reducer 也恢复了
        assert loaded._reducers.get("items") == ReducerStrategy.APPEND

    def test_load_nonexistent(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        ctx = SharedContext.load_checkpoint(path)
        assert ctx.facts == {}


# ══════════════════════════════════════════════════════════
# 条件分支
# ══════════════════════════════════════════════════════════


class TestConditionalEdge:

    def _make_mock_agent(self):
        agent = MagicMock()
        agent._MAX_CONCURRENT_SUBAGENTS = 3
        agent._harness_layer = None
        return agent

    def test_conditional_branch_taken(self):
        """条件分支: 根据分析结果走不同路径"""
        call_count = [0]

        def mock_spawn(task, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return "发现 3 个严重问题"
            return "已修复所有问题"

        agent = self._make_mock_agent()
        agent.spawn.side_effect = mock_spawn

        orch = AgentOrchestrator(agent, verbose=False)
        ctx = SharedContext()

        pipeline = Pipeline("review")
        pipeline.add_task(Task(name="分析", description="分析代码", output_key="analysis"))
        pipeline.add_conditional_edge(
            from_task="分析",
            condition_fn=lambda c: "修复" if "问题" in str(c.get_artifact("analysis")) else "完成",
            targets={
                "修复": Task(name="修复", description="修复问题",
                            context_keys=["analysis"], output_key="fix"),
                "完成": Task(name="完成", description="生成报告"),
            },
        )

        results = orch.run(pipeline, ctx)
        # 应该走了 分析 → 修复 路径
        assert len(results) == 2
        assert results[0].status == TaskStatus.SUCCESS
        assert results[1].status == TaskStatus.SUCCESS
        assert ctx.get_artifact("fix") == "已修复所有问题"

    def test_conditional_branch_alternative(self):
        """条件分支: 走另一条路径"""
        agent = self._make_mock_agent()
        agent.spawn.return_value = "代码质量良好，一切正常"

        orch = AgentOrchestrator(agent, verbose=False)
        ctx = SharedContext()

        pipeline = Pipeline("review")
        pipeline.add_task(Task(name="分析", description="分析代码", output_key="analysis"))
        pipeline.add_conditional_edge(
            from_task="分析",
            condition_fn=lambda c: "修复" if "严重bug" in str(c.get_artifact("analysis")) else "完成",
            targets={
                "修复": Task(name="修复", description="修复问题"),
                "完成": Task(name="完成", description="生成报告", output_key="report"),
            },
        )

        results = orch.run(pipeline, ctx)
        assert len(results) == 2
        assert ctx.get_artifact("report") is not None
        assert ctx.get_artifact("fix") is None  # 修复分支没走


# ══════════════════════════════════════════════════════════
# AgentRole 集成
# ══════════════════════════════════════════════════════════


class TestOrchestratorWithRoles:

    def test_role_system_prompt_injected(self):
        """AgentRole 的 system_prompt 正确传递给 spawn"""
        agent = MagicMock()
        agent.spawn.return_value = "审查完成"
        agent._MAX_CONCURRENT_SUBAGENTS = 3
        agent._harness_layer = None

        reviewer = AgentRole(
            name="reviewer",
            role="代码审查专家",
            goal="发现安全问题",
        )

        orch = AgentOrchestrator(agent, verbose=False)
        ctx = SharedContext()

        pipeline = Pipeline("test")
        pipeline.add_task(Task(
            name="审查",
            description="审查代码",
            agent_role=reviewer,
        ))

        orch.run(pipeline, ctx)

        # 验证 spawn 收到了 role 的 system_prompt
        call_kwargs = agent.spawn.call_args[1]
        assert "代码审查专家" in call_kwargs["system_prompt"]
        assert "发现安全问题" in call_kwargs["system_prompt"]

    def test_role_tools_filter(self):
        """AgentRole 的 tools 限制正确传递"""
        agent = MagicMock()
        agent.spawn.return_value = "ok"
        agent._MAX_CONCURRENT_SUBAGENTS = 3
        agent._harness_layer = None

        role = AgentRole(name="r", role="r", tools=["read_file", "list_dir"])

        orch = AgentOrchestrator(agent, verbose=False)
        pipeline = Pipeline("test")
        pipeline.add_task(Task(name="t", description="d", agent_role=role))

        orch.run(pipeline)

        call_kwargs = agent.spawn.call_args[1]
        assert call_kwargs["tools_filter"] == ["read_file", "list_dir"]

    def test_registered_role_lookup(self):
        """通过 agent_name 查找已注册的 AgentRole"""
        agent = MagicMock()
        agent.spawn.return_value = "ok"
        agent._MAX_CONCURRENT_SUBAGENTS = 3
        agent._harness_layer = None

        role = AgentRole(name="coder", role="编码专家")
        orch = AgentOrchestrator(agent, verbose=False)
        orch.register_role(role)

        pipeline = Pipeline("test")
        pipeline.add_task(Task(name="t", description="d", agent_name="coder"))

        orch.run(pipeline)

        call_kwargs = agent.spawn.call_args[1]
        assert "编码专家" in call_kwargs["system_prompt"]


# ══════════════════════════════════════════════════════════
# Hierarchical 模式
# ══════════════════════════════════════════════════════════


class TestHierarchicalMode:

    def _make_mock_agent(self):
        agent = MagicMock()
        agent._MAX_CONCURRENT_SUBAGENTS = 3
        agent._harness_layer = None
        return agent

    def test_hierarchical_assigns_roles(self):
        """Manager Agent 动态分配任务给 worker"""
        call_count = [0]

        def mock_spawn(task, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Manager 的分配方案
                return '[{"task": "审查代码", "assign_to": "reviewer", "reason": "审查专家"}]'
            return "审查完成，发现 2 个问题"

        agent = self._make_mock_agent()
        agent.spawn.side_effect = mock_spawn

        reviewer = AgentRole(name="reviewer", role="代码审查专家")
        orch = AgentOrchestrator(agent, verbose=False)
        orch.register_role(reviewer)

        pipeline = Pipeline("test", mode=PipelineMode.HIERARCHICAL)
        pipeline.add_task(Task(name="审查代码", description="审查 main.py"))

        ctx = SharedContext()
        results = orch.run(pipeline, ctx)

        # Manager 调用 + 实际任务调用
        assert call_count[0] == 2
        assert results[0].status == TaskStatus.SUCCESS

    def test_hierarchical_no_roles_fallback(self):
        """无可用角色时退化为 sequential"""
        agent = self._make_mock_agent()
        agent.spawn.return_value = "done"

        orch = AgentOrchestrator(agent, roles={}, verbose=False)

        pipeline = Pipeline("test", mode=PipelineMode.HIERARCHICAL)
        pipeline.add_task(Task(name="t1", description="d1"))

        results = orch.run(pipeline)
        assert results[0].status == TaskStatus.SUCCESS

    def test_parse_assignments(self):
        output = '[{"task": "分析", "assign_to": "reviewer", "reason": "适合"}]'
        result = AgentOrchestrator._parse_assignments(output)
        assert result == {"分析": "reviewer"}

    def test_parse_assignments_in_markdown(self):
        output = '```json\n[{"task": "编码", "assign_to": "coder", "reason": "ok"}]\n```'
        result = AgentOrchestrator._parse_assignments(output)
        assert result == {"编码": "coder"}

    def test_parse_assignments_invalid(self):
        result = AgentOrchestrator._parse_assignments("无法解析的内容")
        assert result == {}


# ══════════════════════════════════════════════════════════
# Task Delegation
# ══════════════════════════════════════════════════════════


class TestTaskDelegation:

    def _make_mock_agent(self):
        agent = MagicMock()
        agent._MAX_CONCURRENT_SUBAGENTS = 3
        agent._harness_layer = None
        return agent

    def test_delegation_triggered(self):
        """Agent 输出 [DELEGATE:xxx] 时自动委托"""
        call_count = [0]

        def mock_spawn(task, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return "这个任务需要安全专家处理 [DELEGATE:security]"
            return "安全审查完成，无漏洞"

        agent = self._make_mock_agent()
        agent.spawn.side_effect = mock_spawn

        analyst = AgentRole(name="analyst", role="分析师", allow_delegation=True)
        security = AgentRole(name="security", role="安全专家")

        orch = AgentOrchestrator(agent, verbose=False)
        orch.register_role(analyst)
        orch.register_role(security)

        pipeline = Pipeline("test")
        pipeline.add_task(Task(
            name="分析", description="分析代码",
            agent_role=analyst, output_key="result",
        ))

        ctx = SharedContext()
        results = orch.run(pipeline, ctx)

        assert results[0].status == TaskStatus.SUCCESS
        # 最终结果应该是委托后的输出
        assert "安全审查完成" in ctx.get_artifact("result")
        assert call_count[0] == 2

    def test_delegation_not_allowed(self):
        """allow_delegation=False 时忽略 DELEGATE 标记"""
        agent = self._make_mock_agent()
        agent.spawn.return_value = "结果 [DELEGATE:other]"

        role = AgentRole(name="worker", role="工人", allow_delegation=False)
        orch = AgentOrchestrator(agent, verbose=False)

        pipeline = Pipeline("test")
        pipeline.add_task(Task(name="t", description="d", agent_role=role, output_key="r"))

        ctx = SharedContext()
        orch.run(pipeline, ctx)

        # 不委托，原始输出保留
        assert "[DELEGATE:other]" in ctx.get_artifact("r")

    def test_delegation_unknown_target(self):
        """委托目标未注册时忽略"""
        agent = self._make_mock_agent()
        agent.spawn.return_value = "需要帮助 [DELEGATE:unknown_role]"

        role = AgentRole(name="worker", role="工人", allow_delegation=True)
        orch = AgentOrchestrator(agent, verbose=False)
        orch.register_role(role)

        pipeline = Pipeline("test")
        pipeline.add_task(Task(name="t", description="d", agent_role=role, output_key="r"))

        ctx = SharedContext()
        orch.run(pipeline, ctx)

        # 委托目标不存在，保留原始输出
        assert "[DELEGATE:unknown_role]" in ctx.get_artifact("r")


# ══════════════════════════════════════════════════════════
# 图可视化 (Mermaid)
# ══════════════════════════════════════════════════════════


class TestMermaidVisualization:

    def test_sequential_graph(self):
        pipeline = Pipeline("test")
        pipeline.add_task(Task(name="分析", description="d1"))
        pipeline.add_task(Task(name="编码", description="d2"))
        pipeline.add_task(Task(name="测试", description="d3"))

        mermaid = pipeline.to_mermaid()
        assert "graph TD" in mermaid
        assert "分析" in mermaid
        assert "编码" in mermaid
        assert "-->" in mermaid

    def test_parallel_graph(self):
        pipeline = Pipeline("test", mode=PipelineMode.PARALLEL)
        pipeline.add_task(Task(name="t1", description="d1"))
        pipeline.add_task(Task(name="t2", description="d2"))

        mermaid = pipeline.to_mermaid()
        assert "START" in mermaid
        assert "FINISH" in mermaid

    def test_conditional_graph(self):
        pipeline = Pipeline("test")
        pipeline.add_task(Task(name="分析", description="d"))
        pipeline.add_conditional_edge(
            from_task="分析",
            condition_fn=lambda c: "修复",
            targets={
                "修复": Task(name="修复", description="fix"),
                "完成": Task(name="完成", description="done"),
            },
        )

        mermaid = pipeline.to_mermaid()
        assert "修复" in mermaid
        assert "完成" in mermaid

    def test_hierarchical_graph(self):
        manager = AgentRole(name="mgr", role="项目经理")
        pipeline = Pipeline("test", mode=PipelineMode.HIERARCHICAL, manager_role=manager)
        pipeline.add_task(Task(name="t1", description="d1"))

        mermaid = pipeline.to_mermaid()
        assert "MANAGER" in mermaid
        assert "项目经理" in mermaid
        assert "分配" in mermaid

    def test_role_labels_in_graph(self):
        role = AgentRole(name="r", role="审查专家")
        pipeline = Pipeline("test")
        pipeline.add_task(Task(name="审查", description="d", agent_role=role))

        mermaid = pipeline.to_mermaid()
        assert "审查专家" in mermaid

    def test_status_icons(self):
        pipeline = Pipeline("test")
        t = Task(name="完成的任务", description="d")
        t.status = TaskStatus.SUCCESS
        pipeline.add_task(t)

        mermaid = pipeline.to_mermaid()
        assert "✅" in mermaid

    def test_visualize_method(self):
        agent = MagicMock()
        agent._harness_layer = None
        orch = AgentOrchestrator(agent, verbose=False)

        pipeline = Pipeline("test")
        pipeline.add_task(Task(name="t", description="d"))

        result = orch.visualize(pipeline)
        assert "graph TD" in result
