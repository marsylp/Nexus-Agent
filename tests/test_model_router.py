"""模型路由测试"""
import pytest
from agent_core.model_router import (
    ModelRouter, MODEL_TIERS, MODEL_REGISTRY,
    classify_task, ModelHistory,
)


class TestTaskClassification:
    def test_coding(self):
        needs = classify_task("写一个排序函数")
        assert "coding" in needs

    def test_reasoning(self):
        needs = classify_task("分析这个架构的优缺点")
        assert "reasoning" in needs

    def test_tool_use(self):
        needs = classify_task("先搜索然后总结")
        assert "tool_use" in needs

    def test_fast_short(self):
        needs = classify_task("你好")
        assert "fast" in needs

    def test_tool_continuation_stays_fast(self):
        needs = classify_task("简单问题", has_tool_calls=True, iteration=1)
        assert "fast" in needs
        assert "reasoning" not in needs


class TestModelRouter:
    def test_auto_simple_tier1(self):
        router = ModelRouter()
        router.mode = "auto"
        router.select("你好")
        assert router._last_tier == 1

    def test_auto_complex_tier2(self):
        router = ModelRouter()
        router.mode = "auto"
        router.select("请帮我设计一个微服务架构方案")
        assert router._last_tier == 2

    def test_fixed_mode_no_routing(self):
        router = ModelRouter()
        router.mode = "zhipu"
        provider, model = router.select("设计架构")
        assert provider is None and model is None

    def test_model_tiers_complete(self):
        for name in ["zhipu", "deepseek", "openai", "silicon", "ollama"]:
            assert name in MODEL_TIERS
            assert 1 in MODEL_TIERS[name] or 2 in MODEL_TIERS[name]


class TestModelHistory:
    def test_record_and_score(self):
        h = ModelHistory()
        h.record("zhipu", "glm-4-flash", {"fast"}, True)
        h.record("zhipu", "glm-4-flash", {"fast"}, True)
        h.record("zhipu", "glm-4-flash", {"fast"}, False)
        score = h.score("zhipu", "glm-4-flash", {"fast"})
        assert abs(score - 0.667) < 0.01

    def test_no_record_returns_default(self):
        h = ModelHistory()
        assert h.score("unknown", "model", {"fast"}) == 0.5

    def test_persistence(self, tmp_path, monkeypatch):
        save_path = str(tmp_path / "history.json")
        monkeypatch.setattr(ModelHistory, "_SAVE_PATH", save_path)
        h1 = ModelHistory()
        h1.record("test_prov", "test_model", {"coding"}, True)
        h1.record("test_prov", "test_model", {"coding"}, True)
        h1.record("test_prov", "test_model", {"coding"}, False)
        # 新实例应从磁盘加载
        h2 = ModelHistory()
        score = h2.score("test_prov", "test_model", {"coding"})
        assert abs(score - 0.667) < 0.01


class TestModelRegistry:
    def test_registry_complete(self):
        total = sum(len(models) for models in MODEL_REGISTRY.values())
        assert total >= 9
        for prov, models in MODEL_REGISTRY.items():
            for model, info in models.items():
                assert "tier" in info
                assert "caps" in info
                assert "cost" in info
