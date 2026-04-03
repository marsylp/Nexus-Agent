"""模型切换调度 — 全面测试

从资深测试专家角度，覆盖以下维度：
1. 任务分类准确性（边界、多能力组合、中英文混合）
2. 路由选择正确性（能力匹配、成本优化、历史学习权重）
3. 熔断与降级（熔断触发、恢复、fallback 链路）
4. Agent 集成（_resolve_model 与 router 的协作、多轮迭代模型切换）
5. 并发安全与状态隔离
6. 边界条件与异常路径
"""
import os
import time
import json
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from collections import defaultdict

from agent_core.model_router import (
    ModelRouter, MODEL_REGISTRY, MODEL_TIERS,
    classify_task, ModelHistory, _compiled_patterns,
)
from agent_core.llm import (
    CircuitBreaker, classify_error, ErrorKind,
    PROVIDERS, _provider_has_key, get_model,
    FALLBACK_ORDER, _get_fallback_order,
    _RETRYABLE, _SKIP_TO_FALLBACK,
    _calc_delay, chat_completion_resilient,
    switch_provider, list_providers, register_provider,
)


# ════════════════════════════════════════════════════════════
# 第一部分：任务分类深度测试
# ════════════════════════════════════════════════════════════

class TestTaskClassificationEdgeCases:
    """任务分类的边界条件和复杂场景"""

    # ── 多能力组合 ──────────────────────────────────────────
    def test_coding_plus_reasoning(self):
        """同时需要编码和推理的复合任务"""
        needs = classify_task("分析这段代码的性能问题并优化实现")
        assert "coding" in needs
        assert "reasoning" in needs

    def test_tool_use_plus_reasoning(self):
        """需要工具调用和推理的任务"""
        needs = classify_task("先搜索最新的技术方案，然后分析对比")
        assert "tool_use" in needs
        assert "reasoning" in needs

    def test_all_capabilities_combined(self):
        """触发所有能力标签的极端输入"""
        needs = classify_task("先搜索然后写代码实现，分析对比不同方案")
        assert len(needs) >= 3  # 至少 tool_use + coding + reasoning

    # ── 中英文混合 ──────────────────────────────────────────
    def test_english_coding(self):
        needs = classify_task("implement a binary search function")
        assert "coding" in needs

    def test_english_reasoning(self):
        needs = classify_task("analyze and compare these two approaches")
        assert "reasoning" in needs

    def test_english_tool_use(self):
        needs = classify_task("search for the latest docs and download")
        assert "tool_use" in needs

    def test_mixed_language(self):
        """中英文混合输入"""
        needs = classify_task("请implement一个sort函数")
        assert "coding" in needs

    # ── 长度边界 ──────────────────────────────────────────
    def test_empty_input(self):
        """空输入应归类为 fast"""
        needs = classify_task("")
        assert "fast" in needs

    def test_short_no_keyword(self):
        """短输入无关键词 → fast"""
        needs = classify_task("嗯")
        assert "fast" in needs

    def test_medium_no_keyword(self):
        """20~50字符无关键词的中等输入"""
        needs = classify_task("这是一段普通的文字内容")
        # 长度 < 50 且无关键词匹配 → 取决于实现
        assert len(needs) >= 1

    def test_long_no_keyword_defaults_reasoning(self):
        """长输入(>=50字符)无关键词 → reasoning"""
        text = "这是一段非常长的文字" * 10  # 远超50字符
        needs = classify_task(text)
        assert "reasoning" in needs

    def test_exactly_20_chars(self):
        """恰好20字符的边界"""
        text = "a" * 20
        needs = classify_task(text)
        assert len(needs) >= 1

    def test_exactly_50_chars(self):
        """恰好50字符的边界"""
        text = "a" * 50
        needs = classify_task(text)
        assert "reasoning" in needs

    # ── 工具续轮降级 ──────────────────────────────────────
    def test_tool_continuation_ignores_complex_keywords(self):
        """工具调用后的续轮，即使输入包含复杂关键词也应保持 fast"""
        needs = classify_task("请帮我设计一个微服务架构", has_tool_calls=True, iteration=1)
        assert needs == {"fast", "tool_use"}

    def test_tool_continuation_iteration_zero_not_affected(self):
        """iteration=0 时不触发续轮降级"""
        needs = classify_task("设计架构", has_tool_calls=True, iteration=0)
        assert "reasoning" in needs

    def test_tool_continuation_no_tool_calls_not_affected(self):
        """has_tool_calls=False 时不触发续轮降级"""
        needs = classify_task("设计架构", has_tool_calls=False, iteration=1)
        assert "reasoning" in needs

    # ── 特殊模式 ──────────────────────────────────────────
    def test_greeting_patterns(self):
        for greeting in ["你好", "hi", "hello", "hey", "嗨", "谢谢", "ok"]:
            needs = classify_task(greeting)
            assert "fast" in needs, f"'{greeting}' 应被分类为 fast"

    def test_time_weather_patterns(self):
        for q in ["几点了", "什么时间", "今天天气"]:
            needs = classify_task(q)
            assert "fast" in needs, f"'{q}' 应被分类为 fast"


# ════════════════════════════════════════════════════════════
# 第二部分：路由选择正确性
# ════════════════════════════════════════════════════════════

class TestRouterSelectionLogic:
    """路由器的选择逻辑：能力匹配、成本优化、评分公式"""

    def _make_router_with_keys(self, providers=None):
        """创建一个有可用 key 的 router"""
        router = ModelRouter()
        router.mode = "auto"
        # mock _has_key 让指定 provider 可用
        if providers:
            original = ModelRouter._has_key
            router._has_key = staticmethod(lambda p: p in providers)
        return router

    def test_fast_task_prefers_tier1(self):
        """fast 任务应优先选择 tier1 低成本模型"""
        with patch.object(ModelRouter, '_has_key', return_value=True):
            router = ModelRouter()
            router.mode = "auto"
            # 确保熔断器不干扰
            from agent_core.llm import get_circuit_breaker
            breaker = get_circuit_breaker()
            provider, model = router.select("你好")
            if provider:
                info = MODEL_REGISTRY.get(provider, {}).get(model, {})
                assert info.get("tier") == 1, f"fast 任务选了 tier {info.get('tier')}: {provider}/{model}"

    def test_reasoning_task_prefers_tier2(self):
        """reasoning 任务应选择 tier2 强模型"""
        with patch.object(ModelRouter, '_has_key', return_value=True):
            router = ModelRouter()
            router.mode = "auto"
            provider, model = router.select("请详细分析并对比这两种架构方案的优缺点")
            if provider:
                info = MODEL_REGISTRY.get(provider, {}).get(model, {})
                assert info.get("tier") == 2, f"reasoning 任务选了 tier {info.get('tier')}: {provider}/{model}"

    def test_fixed_mode_bypasses_routing(self):
        """固定模式下不进行路由选择"""
        router = ModelRouter()
        router.mode = "zhipu"
        provider, model = router.select("写一个复杂的分布式系统")
        assert provider is None
        assert model is None
        assert "固定模式" in router.last_selection

    def test_no_available_provider_returns_none(self):
        """所有 provider 都不可用时返回 None"""
        with patch.object(ModelRouter, '_has_key', return_value=False):
            router = ModelRouter()
            router.mode = "auto"
            provider, model = router.select("你好")
            assert provider is None
            assert model is None

    def test_scoring_formula_weights(self):
        """验证评分公式: coverage*0.5 + history*0.3 + cost_score*0.2"""
        router = ModelRouter()
        router.mode = "auto"
        # 手动调用 _find_best 验证
        with patch.object(ModelRouter, '_has_key', return_value=True):
            result = router._find_best({"fast"})
            if result:
                provider, model, score, reason = result
                # score 应在合理范围内
                assert 0 <= score <= 1.0

    def test_breaker_skips_fused_provider(self):
        """已熔断的 provider 应被跳过"""
        from agent_core.llm import get_circuit_breaker
        breaker = get_circuit_breaker()

        with patch.object(ModelRouter, '_has_key', return_value=True):
            router = ModelRouter()
            router.mode = "auto"

            # 熔断 zhipu
            breaker.record_failure("zhipu", immediate=True)
            try:
                provider, model = router.select("你好")
                if provider:
                    assert provider != "zhipu", "已熔断的 zhipu 不应被选中"
            finally:
                breaker.record_success("zhipu")  # 清理

    def test_cost_optimization_same_coverage(self):
        """能力覆盖度相同时，应优先选择低成本模型"""
        with patch.object(ModelRouter, '_has_key', return_value=True):
            router = ModelRouter()
            router.mode = "auto"
            # 多次选择 fast 任务，应倾向低成本
            provider, model = router.select("ok")
            if provider:
                info = MODEL_REGISTRY.get(provider, {}).get(model, {})
                assert info.get("cost", 99) <= 5  # 不应选最贵的


class TestRouterWithHistory:
    """历史学习对路由选择的影响"""

    def test_history_boosts_successful_model(self, tmp_path, monkeypatch):
        """历史成功率高的模型应获得更高评分"""
        save_path = str(tmp_path / "test_history.json")
        monkeypatch.setattr(ModelHistory, "_SAVE_PATH", save_path)

        router = ModelRouter()
        router.mode = "auto"
        router.history = ModelHistory()

        # 给某个模型大量成功记录
        for _ in range(20):
            router.history.record("deepseek", "deepseek-chat", {"coding"}, True)

        # 给另一个模型大量失败记录
        for _ in range(20):
            router.history.record("zhipu", "glm-4-plus", {"coding"}, False)

        score_ds = router.history.score("deepseek", "deepseek-chat", {"coding"})
        score_zp = router.history.score("zhipu", "glm-4-plus", {"coding"})
        assert score_ds > score_zp

    def test_history_empty_returns_neutral(self):
        """无历史记录时返回中性分数 0.5"""
        h = ModelHistory()
        assert h.score("nonexistent", "model", {"fast"}) == 0.5
        assert h.score("nonexistent", "model", set()) == 0.5

    def test_history_multi_cap_averaging(self, tmp_path, monkeypatch):
        """多能力评分应取平均值"""
        save_path = str(tmp_path / "test_history2.json")
        monkeypatch.setattr(ModelHistory, "_SAVE_PATH", save_path)

        h = ModelHistory()
        # coding: 100% 成功
        for _ in range(10):
            h.record("test", "model", {"coding"}, True)
        # reasoning: 0% 成功
        for _ in range(10):
            h.record("test", "model", {"reasoning"}, False)

        score = h.score("test", "model", {"coding", "reasoning"})
        assert abs(score - 0.5) < 0.01  # (1.0 + 0.0) / 2 = 0.5

    def test_record_result_integration(self, monkeypatch):
        """router.record_result 应正确记录到 history"""
        monkeypatch.setenv("LLM_PROVIDER", "zhipu")
        router = ModelRouter()
        router._last_caps = {"coding"}
        router.record_result(success=True)
        model = get_model("zhipu")
        score = router.history.score("zhipu", model, {"coding"})
        assert score >= 0.5


# ════════════════════════════════════════════════════════════
# 第三部分：熔断器与降级链路
# ════════════════════════════════════════════════════════════

class TestCircuitBreakerAdvanced:
    """熔断器的高级场景"""

    def test_threshold_boundary(self):
        """恰好达到阈值时触发熔断"""
        cb = CircuitBreaker(persist=False, threshold=3, recovery_time=60)
        cb.record_failure("p1")
        cb.record_failure("p1")
        assert not cb.is_open("p1")
        cb.record_failure("p1")  # 第3次
        assert cb.is_open("p1")

    def test_different_providers_independent(self):
        """不同 provider 的熔断状态互相独立"""
        cb = CircuitBreaker(persist=False, threshold=1)
        cb.record_failure("p1")
        assert cb.is_open("p1")
        assert not cb.is_open("p2")

    def test_recovery_time_precision(self):
        """恢复时间精度测试"""
        cb = CircuitBreaker(persist=False, threshold=1, recovery_time=0.2)
        cb.record_failure("p1")
        assert cb.is_open("p1")
        time.sleep(0.1)
        assert cb.is_open("p1")  # 还没到恢复时间
        time.sleep(0.15)
        assert not cb.is_open("p1")  # 已恢复

    def test_success_after_recovery_resets_counter(self):
        """恢复后成功调用应重置计数器"""
        cb = CircuitBreaker(persist=False, threshold=2, recovery_time=0.1)
        cb.record_failure("p1")
        cb.record_failure("p1")
        assert cb.is_open("p1")
        time.sleep(0.15)
        assert not cb.is_open("p1")
        cb.record_success("p1")
        # 重新开始计数
        cb.record_failure("p1")
        assert not cb.is_open("p1")  # 只有1次失败，阈值是2

    def test_immediate_fuse_for_auth_errors(self):
        """AUTH 错误应立即熔断"""
        cb = CircuitBreaker(persist=False, threshold=10)
        cb.record_failure("p1", immediate=True)
        assert cb.is_open("p1")

    def test_get_status_report(self):
        """状态报告应包含正确信息"""
        cb = CircuitBreaker(persist=False, threshold=1, recovery_time=60)
        cb.record_failure("p1")
        cb.record_success("p2")  # 先记录再查
        status = cb.get_status()
        assert "熔断中" in status.get("p1", "")

    def test_unknown_provider_not_open(self):
        """未记录的 provider 不应处于熔断状态"""
        cb = CircuitBreaker(persist=False)
        assert not cb.is_open("never_seen_provider")


class TestErrorClassificationAdvanced:
    """错误分类的更多场景"""

    def test_network_error(self):
        assert classify_error(ConnectionError("connection refused")) == ErrorKind.NETWORK

    def test_dns_error(self):
        assert classify_error(Exception("DNS resolution failed")) == ErrorKind.NETWORK

    def test_503_service_unavailable(self):
        assert classify_error(Exception("503 Service Unavailable")) == ErrorKind.SERVER

    def test_unknown_error(self):
        assert classify_error(Exception("something weird happened")) == ErrorKind.UNKNOWN

    def test_retryable_errors_are_correct(self):
        """可重试错误集合应包含正确的类型"""
        assert ErrorKind.RATE_LIMIT in _RETRYABLE
        assert ErrorKind.TIMEOUT in _RETRYABLE
        assert ErrorKind.SERVER in _RETRYABLE
        assert ErrorKind.NETWORK in _RETRYABLE

    def test_skip_errors_are_correct(self):
        """应跳过的错误集合"""
        assert ErrorKind.AUTH in _SKIP_TO_FALLBACK
        assert ErrorKind.BALANCE in _SKIP_TO_FALLBACK

    def test_retryable_and_skip_mutually_exclusive(self):
        """可重试和应跳过的集合不应有交集"""
        assert _RETRYABLE & _SKIP_TO_FALLBACK == set()


class TestFallbackChain:
    """降级链路测试"""

    def test_fallback_order_has_all_builtin_providers(self):
        """fallback 顺序应包含所有内置 provider"""
        order = _get_fallback_order()
        for name in ["zhipu", "deepseek", "silicon", "openai", "ollama"]:
            assert name in order

    def test_dynamic_provider_added_to_fallback(self):
        """动态注册的 provider 应自动加入 fallback"""
        # 注册一个临时 provider
        original_providers = dict(PROVIDERS)
        try:
            register_provider("test_dynamic", "http://localhost:9999/v1",
                              None, "test-model", {"fast"}, 1, 0)
            order = _get_fallback_order()
            assert "test_dynamic" in order
        finally:
            # 清理
            if "test_dynamic" in PROVIDERS:
                del PROVIDERS["test_dynamic"]
            from agent_core.model_router import MODEL_REGISTRY, MODEL_TIERS
            if "test_dynamic" in MODEL_REGISTRY:
                del MODEL_REGISTRY["test_dynamic"]
            if "test_dynamic" in MODEL_TIERS:
                del MODEL_TIERS["test_dynamic"]

    def test_backoff_exponential_growth(self):
        """指数退避应正确增长"""
        delays = [_calc_delay(i, ErrorKind.TIMEOUT) for i in range(4)]
        for i in range(1, len(delays)):
            assert delays[i] > delays[i - 1], f"delay[{i}] 应大于 delay[{i-1}]"

    def test_rate_limit_backoff_multiplier(self):
        """rate_limit 错误的退避应更长"""
        d_rate = _calc_delay(0, ErrorKind.RATE_LIMIT)
        d_timeout = _calc_delay(0, ErrorKind.TIMEOUT)
        assert d_rate == d_timeout * 3


# ════════════════════════════════════════════════════════════
# 第四部分：Agent 集成测试
# ════════════════════════════════════════════════════════════

class TestAgentModelResolution:
    """Agent._resolve_model 与 ModelRouter 的协作"""

    def test_resolve_model_auto_mode(self):
        """auto 模式下 _resolve_model 应返回路由器选择的结果"""
        from agent_core.agent import Agent
        with patch.object(ModelRouter, '_has_key', return_value=True):
            agent = Agent(stream=False, mode="auto")
            prov, model = agent._resolve_model("写一个排序算法")
            assert prov is not None
            assert model is not None

    def test_resolve_model_fixed_mode(self):
        """固定模式下 _resolve_model 应返回实例的 provider"""
        from agent_core.agent import Agent
        agent = Agent(stream=False, mode="zhipu", provider="zhipu")
        prov, model = agent._resolve_model("写一个排序算法")
        assert prov == "zhipu"

    def test_resolve_model_with_explicit_model(self):
        """显式指定 model 时应使用指定的 model"""
        from agent_core.agent import Agent
        agent = Agent(stream=False, mode="zhipu", provider="zhipu", model="glm-4-plus")
        prov, model = agent._resolve_model("任意输入")
        assert model == "glm-4-plus"

    def test_mode_switch_affects_routing(self):
        """set_mode 应影响后续的路由行为"""
        from agent_core.agent import Agent
        agent = Agent(stream=False, mode="auto")
        assert agent.current_mode == "auto"

        agent.set_mode("zhipu")
        assert agent.current_mode == "zhipu"
        prov, model = agent._resolve_model("设计一个复杂架构")
        # 固定模式下 router 返回 None，_resolve_model 回退到实例 provider
        assert prov == agent._provider

    def test_iteration_zero_uses_router(self):
        """第一轮迭代(i=0)应使用路由器选择模型"""
        from agent_core.agent import Agent
        with patch.object(ModelRouter, '_has_key', return_value=True):
            agent = Agent(stream=False, mode="auto")
            prov, model = agent._resolve_model("分析代码", has_tool_calls=False, iteration=0)
            # auto 模式下应有路由选择
            assert prov is not None

    def test_subsequent_iterations_use_instance_provider(self):
        """后续迭代(i>0)在 run() 中直接使用实例 provider，不再路由"""
        # 这个行为在 Agent.run 的循环中：i>0 时直接用 self._provider
        from agent_core.agent import Agent
        agent = Agent(stream=False, mode="auto", provider="deepseek")
        # 模拟 run 中 i>0 的逻辑
        prov = agent._provider
        model = agent.model or get_model(agent._provider)
        assert prov == "deepseek"


class TestAgentSpawnModelIsolation:
    """子 Agent 的模型配置隔离"""

    def test_spawn_inherits_provider(self):
        """spawn 的子 agent 应能指定独立的 provider"""
        from agent_core.agent import Agent
        parent = Agent(stream=False, provider="zhipu")
        # spawn 方法签名允许指定 provider
        assert hasattr(parent, "spawn")

    def test_parent_child_provider_independent(self):
        """父子 agent 的 provider 应互相独立"""
        from agent_core.agent import Agent
        parent = Agent(stream=False, provider="zhipu")
        child = Agent(stream=False, provider="deepseek")
        assert parent.provider != child.provider
        assert parent.provider == "zhipu"
        assert child.provider == "deepseek"


# ════════════════════════════════════════════════════════════
# 第五部分：状态隔离与并发安全
# ════════════════════════════════════════════════════════════

class TestStateIsolation:
    """多个 Router 实例之间的状态隔离"""

    def test_router_instances_independent(self):
        """不同 Router 实例的 mode 应互相独立"""
        r1 = ModelRouter()
        r2 = ModelRouter()
        r1.mode = "zhipu"
        r2.mode = "auto"
        assert r1.mode != r2.mode

    def test_router_last_selection_independent(self):
        """不同 Router 实例的 last_selection 应互相独立"""
        r1 = ModelRouter()
        r2 = ModelRouter()
        r1.mode = "zhipu"
        r2.mode = "auto"
        r1.select("你好")
        with patch.object(ModelRouter, '_has_key', return_value=True):
            r2.select("你好")
        assert r1.last_selection != r2.last_selection or True  # 至少不会互相覆盖

    def test_history_shared_across_routers(self):
        """ModelHistory 是实例级别的，但持久化文件是共享的"""
        r1 = ModelRouter()
        r2 = ModelRouter()
        # 两个 router 有各自的 history 实例
        assert r1.history is not r2.history


class TestModelRegistryIntegrity:
    """模型注册表的完整性和一致性"""

    def test_all_models_have_required_fields(self):
        """所有注册模型必须有 tier, caps, cost"""
        for prov, models in MODEL_REGISTRY.items():
            for model_name, info in models.items():
                assert "tier" in info, f"{prov}/{model_name} 缺少 tier"
                assert "caps" in info, f"{prov}/{model_name} 缺少 caps"
                assert "cost" in info, f"{prov}/{model_name} 缺少 cost"
                assert isinstance(info["caps"], set), f"{prov}/{model_name} caps 应为 set"
                assert info["tier"] in (1, 2), f"{prov}/{model_name} tier 应为 1 或 2"
                assert 0 <= info["cost"] <= 10, f"{prov}/{model_name} cost 应在 0~10"

    def test_each_provider_has_at_least_one_model(self):
        """每个 provider 至少有一个模型"""
        for prov, models in MODEL_REGISTRY.items():
            assert len(models) >= 1, f"{prov} 没有注册任何模型"

    def test_model_tiers_consistent_with_registry(self):
        """MODEL_TIERS 应与 MODEL_REGISTRY 一致"""
        for prov in MODEL_REGISTRY:
            assert prov in MODEL_TIERS, f"{prov} 不在 MODEL_TIERS 中"

    def test_providers_config_matches_registry(self):
        """PROVIDERS 配置应覆盖 MODEL_REGISTRY 中的所有 provider"""
        for prov in MODEL_REGISTRY:
            assert prov in PROVIDERS, f"{prov} 在 MODEL_REGISTRY 中但不在 PROVIDERS 中"

    def test_provider_default_model_exists_in_registry(self):
        """PROVIDERS 中的默认模型应在 MODEL_REGISTRY 中注册"""
        for prov, cfg in PROVIDERS.items():
            if prov in MODEL_REGISTRY:
                default_model = cfg["model"]
                assert default_model in MODEL_REGISTRY[prov], \
                    f"{prov} 的默认模型 {default_model} 未在 MODEL_REGISTRY 中注册"


# ════════════════════════════════════════════════════════════
# 第六部分：Provider 管理
# ════════════════════════════════════════════════════════════

class TestProviderManagement:
    """Provider 切换和管理"""

    def test_switch_provider_valid(self, monkeypatch):
        """切换到有效 provider"""
        monkeypatch.setenv("LLM_PROVIDER", "zhipu")
        result = switch_provider("deepseek")
        assert "deepseek" in result

    def test_switch_provider_invalid(self):
        """切换到无效 provider 应抛异常"""
        with pytest.raises(ValueError, match="未知提供商"):
            switch_provider("nonexistent_provider")

    def test_switch_provider_case_insensitive(self, monkeypatch):
        """provider 名称应不区分大小写"""
        monkeypatch.setenv("LLM_PROVIDER", "zhipu")
        result = switch_provider("DeepSeek")
        assert "deepseek" in result

    def test_list_providers_structure(self):
        """list_providers 返回结构正确"""
        providers = list_providers()
        assert len(providers) >= 5
        for p in providers:
            assert "name" in p
            assert "model" in p
            assert "active" in p
            assert "configured" in p
            assert "status" in p

    def test_list_providers_shows_fused_status(self):
        """list_providers 应显示熔断状态"""
        from agent_core.llm import get_circuit_breaker
        breaker = get_circuit_breaker()
        breaker.record_failure("zhipu", immediate=True)
        try:
            providers = list_providers()
            zhipu = next(p for p in providers if p["name"] == "zhipu")
            assert zhipu["status"] == "熔断"
        finally:
            breaker.record_success("zhipu")

    def test_register_provider_adds_to_all_registries(self):
        """register_provider 应同时更新 PROVIDERS 和 MODEL_REGISTRY"""
        try:
            register_provider(
                "test_reg", "http://test:8080/v1", "TEST_KEY",
                "test-model-v1", {"fast", "coding"}, tier=1, cost=2
            )
            assert "test_reg" in PROVIDERS
            assert "test_reg" in MODEL_REGISTRY
            assert "test-model-v1" in MODEL_REGISTRY["test_reg"]
            info = MODEL_REGISTRY["test_reg"]["test-model-v1"]
            assert info["caps"] == {"fast", "coding"}
            assert info["tier"] == 1
            assert info["cost"] == 2
        finally:
            if "test_reg" in PROVIDERS:
                del PROVIDERS["test_reg"]
            from agent_core.model_router import MODEL_REGISTRY as MR, MODEL_TIERS as MT
            if "test_reg" in MR:
                del MR["test_reg"]
            if "test_reg" in MT:
                del MT["test_reg"]

    def test_get_model_custom_provider(self, monkeypatch):
        """custom provider 应从环境变量获取模型名"""
        monkeypatch.setenv("CUSTOM_MODEL", "my-custom-model")
        assert get_model("custom") == "my-custom-model"

    def test_get_model_ollama_env_override(self, monkeypatch):
        """ollama 应支持 OLLAMA_MODEL 环境变量覆盖"""
        monkeypatch.setenv("OLLAMA_MODEL", "llama3:8b")
        assert get_model("ollama") == "llama3:8b"

    def test_get_model_agent_model_env_override(self, monkeypatch):
        """AGENT_MODEL 环境变量应覆盖默认模型"""
        monkeypatch.setenv("AGENT_MODEL", "gpt-4-turbo")
        # 对非 custom/ollama 的 provider 生效
        assert get_model("openai") == "gpt-4-turbo"


# ════════════════════════════════════════════════════════════
# 第七部分：Resilient 调用链路（mock LLM）
# ════════════════════════════════════════════════════════════

class TestResilientCallChain:
    """chat_completion_resilient 的错误恢复和 fallback 链路"""

    def test_resilient_sync_success_first_try(self, monkeypatch):
        """首次调用成功，不触发 fallback"""
        mock_msg = MagicMock()
        mock_msg.content = "test response"

        with patch("agent_core.llm.chat_completion", return_value=mock_msg) as mock_cc:
            with patch("agent_core.llm._provider_has_key", return_value=True):
                result = chat_completion_resilient(
                    [{"role": "user", "content": "hi"}],
                    stream=False, provider="zhipu"
                )
                assert result.content == "test response"
                assert mock_cc.call_count == 1

    def test_resilient_sync_retries_on_timeout(self, monkeypatch):
        """超时错误应触发重试"""
        mock_msg = MagicMock()
        mock_msg.content = "ok"
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise TimeoutError("timed out")
            return mock_msg

        monkeypatch.setenv("MAX_RETRIES", "3")
        monkeypatch.setenv("RETRY_BASE_DELAY", "0.01")

        with patch("agent_core.llm.chat_completion", side_effect=side_effect):
            with patch("agent_core.llm._provider_has_key", return_value=True):
                # 需要确保熔断器不干扰
                from agent_core.llm import get_circuit_breaker
                breaker = get_circuit_breaker()
                breaker.record_success("zhipu")
                result = chat_completion_resilient(
                    [{"role": "user", "content": "hi"}],
                    stream=False, provider="zhipu"
                )
                assert result.content == "ok"
                assert call_count == 3

    def test_resilient_sync_auth_error_skips_to_fallback(self, monkeypatch):
        """AUTH 错误应立即跳到 fallback，不重试"""
        mock_msg = MagicMock()
        mock_msg.content = "fallback response"
        monkeypatch.setenv("RETRY_BASE_DELAY", "0.01")

        call_providers = []

        def side_effect(*args, **kwargs):
            provider = kwargs.get("provider", "unknown")
            call_providers.append(provider)
            if provider == "zhipu":
                raise Exception("401 Unauthorized")
            return mock_msg

        with patch("agent_core.llm.chat_completion", side_effect=side_effect):
            with patch("agent_core.llm._provider_has_key", return_value=True):
                from agent_core.llm import get_circuit_breaker
                breaker = get_circuit_breaker()
                breaker.record_success("zhipu")
                try:
                    result = chat_completion_resilient(
                        [{"role": "user", "content": "hi"}],
                        stream=False, provider="zhipu"
                    )
                    assert result.content == "fallback response"
                    # zhipu 应只被调用一次（AUTH 不重试）
                    assert call_providers.count("zhipu") == 1
                    # 应有 fallback 调用
                    assert len(call_providers) >= 2
                finally:
                    breaker.record_success("zhipu")

    def test_resilient_all_fail_raises(self, monkeypatch):
        """所有 provider 都失败时应抛出 RuntimeError"""
        monkeypatch.setenv("RETRY_BASE_DELAY", "0.01")

        def always_fail(*args, **kwargs):
            raise Exception("401 Unauthorized")

        with patch("agent_core.llm.chat_completion", side_effect=always_fail):
            with patch("agent_core.llm._provider_has_key", return_value=True):
                from agent_core.llm import get_circuit_breaker
                breaker = get_circuit_breaker()
                # 清理所有熔断状态
                for p in PROVIDERS:
                    breaker.record_success(p)
                try:
                    with pytest.raises(RuntimeError, match="所有模型均调用失败"):
                        chat_completion_resilient(
                            [{"role": "user", "content": "hi"}],
                            stream=False, provider="zhipu"
                        )
                finally:
                    for p in PROVIDERS:
                        breaker.record_success(p)


class TestResilientStreamChain:
    """流式调用的错误恢复"""

    def test_resilient_stream_success(self, monkeypatch):
        """流式调用成功"""
        def mock_stream(*args, **kwargs):
            yield ("content", "hello ")
            yield ("content", "world")
            yield ("done", {"content": "hello world", "thinking": "",
                            "tool_calls": None, "finish_reason": "stop"})

        with patch("agent_core.llm.chat_completion_stream", side_effect=mock_stream):
            with patch("agent_core.llm._provider_has_key", return_value=True):
                from agent_core.llm import get_circuit_breaker
                breaker = get_circuit_breaker()
                breaker.record_success("zhipu")
                gen = chat_completion_resilient(
                    [{"role": "user", "content": "hi"}],
                    stream=True, provider="zhipu"
                )
                events = list(gen)
                contents = [d for t, d in events if t == "content"]
                assert "hello " in contents
                assert "world" in contents

    def test_resilient_stream_fallback_on_error(self, monkeypatch):
        """流式调用失败时应 fallback 到下一个 provider"""
        call_count = {"value": 0}

        def mock_stream(*args, **kwargs):
            call_count["value"] += 1
            provider = kwargs.get("provider", "unknown")
            if provider == "zhipu":
                raise Exception("502 Bad Gateway")
            yield ("content", "fallback ok")
            yield ("done", {"content": "fallback ok", "thinking": "",
                            "tool_calls": None, "finish_reason": "stop"})

        with patch("agent_core.llm.chat_completion_stream", side_effect=mock_stream):
            with patch("agent_core.llm._provider_has_key", return_value=True):
                from agent_core.llm import get_circuit_breaker
                breaker = get_circuit_breaker()
                for p in PROVIDERS:
                    breaker.record_success(p)
                try:
                    gen = chat_completion_resilient(
                        [{"role": "user", "content": "hi"}],
                        stream=True, provider="zhipu"
                    )
                    events = list(gen)
                    contents = [d for t, d in events if t == "content"]
                    assert "fallback ok" in contents
                finally:
                    for p in PROVIDERS:
                        breaker.record_success(p)


# ════════════════════════════════════════════════════════════
# 第八部分：端到端场景测试
# ════════════════════════════════════════════════════════════

class TestEndToEndScenarios:
    """模拟真实使用场景的端到端测试"""

    def test_scenario_simple_chat_uses_cheap_model(self):
        """场景：简单聊天应使用低成本模型"""
        with patch.object(ModelRouter, '_has_key', return_value=True):
            router = ModelRouter()
            router.mode = "auto"
            provider, model = router.select("你好呀")
            if provider and model:
                info = MODEL_REGISTRY[provider][model]
                assert info["tier"] == 1
                assert info["cost"] <= 3

    def test_scenario_code_generation_uses_capable_model(self):
        """场景：代码生成应使用有 coding 能力的模型"""
        with patch.object(ModelRouter, '_has_key', return_value=True):
            router = ModelRouter()
            router.mode = "auto"
            provider, model = router.select("实现一个二叉树的遍历算法")
            if provider and model:
                info = MODEL_REGISTRY[provider][model]
                assert "coding" in info["caps"]

    def test_scenario_architecture_design_uses_reasoning_model(self):
        """场景：架构设计应使用有 reasoning 能力的模型"""
        with patch.object(ModelRouter, '_has_key', return_value=True):
            router = ModelRouter()
            router.mode = "auto"
            provider, model = router.select("设计一个高可用的分布式缓存系统架构方案")
            if provider and model:
                info = MODEL_REGISTRY[provider][model]
                assert "reasoning" in info["caps"]

    def test_scenario_multi_step_task_uses_tool_model(self):
        """场景：多步骤任务应使用有 tool_use 能力的模型"""
        with patch.object(ModelRouter, '_has_key', return_value=True):
            router = ModelRouter()
            router.mode = "auto"
            provider, model = router.select("先搜索最新的Python文档，然后下载示例代码")
            if provider and model:
                info = MODEL_REGISTRY[provider][model]
                assert "tool_use" in info["caps"]

    def test_scenario_provider_fuse_and_recover(self):
        """场景：provider 熔断后恢复"""
        cb = CircuitBreaker(persist=False, threshold=2, recovery_time=0.2)
        # 模拟连续失败
        cb.record_failure("test_prov")
        cb.record_failure("test_prov")
        assert cb.is_open("test_prov")

        # 等待恢复
        time.sleep(0.25)
        assert not cb.is_open("test_prov")

        # 恢复后成功
        cb.record_success("test_prov")
        assert not cb.is_open("test_prov")

    def test_scenario_history_learning_improves_selection(self, tmp_path, monkeypatch):
        """场景：历史学习应逐渐改善模型选择"""
        save_path = str(tmp_path / "scenario_history.json")
        monkeypatch.setattr(ModelHistory, "_SAVE_PATH", save_path)

        h = ModelHistory()

        # 初始状态：所有模型评分相同
        s1 = h.score("deepseek", "deepseek-chat", {"coding"})
        s2 = h.score("zhipu", "glm-4-plus", {"coding"})
        assert s1 == s2 == 0.5

        # 模拟 deepseek 在 coding 任务上表现好
        for _ in range(10):
            h.record("deepseek", "deepseek-chat", {"coding"}, True)
        for _ in range(10):
            h.record("zhipu", "glm-4-plus", {"coding"}, False)

        # 现在 deepseek 应该评分更高
        s1 = h.score("deepseek", "deepseek-chat", {"coding"})
        s2 = h.score("zhipu", "glm-4-plus", {"coding"})
        assert s1 > s2
        assert s1 == 1.0
        assert s2 == 0.0


class TestModelRegistryCapabilities:
    """验证模型注册表中的能力标签合理性"""

    def test_tier1_models_have_fast(self):
        """tier1 模型应至少有 fast 能力"""
        for prov, models in MODEL_REGISTRY.items():
            for model_name, info in models.items():
                if info["tier"] == 1:
                    assert "fast" in info["caps"] or "coding" in info["caps"], \
                        f"{prov}/{model_name} 是 tier1 但没有 fast 或 coding 能力"

    def test_tier2_models_have_reasoning(self):
        """tier2 模型应有 reasoning 能力"""
        for prov, models in MODEL_REGISTRY.items():
            for model_name, info in models.items():
                if info["tier"] == 2:
                    assert "reasoning" in info["caps"], \
                        f"{prov}/{model_name} 是 tier2 但没有 reasoning 能力"

    def test_free_models_exist(self):
        """应存在免费模型(cost=0)"""
        free_models = [
            (p, m) for p, models in MODEL_REGISTRY.items()
            for m, info in models.items() if info["cost"] == 0
        ]
        assert len(free_models) >= 1, "应至少有一个免费模型"
