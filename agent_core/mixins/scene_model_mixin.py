"""SceneModelMixin — 场景级模型选择职责

从 Agent 类拆分出来的模型选择功能。
不同场景（summarize/subagent/image 等）自动选择最合适的模型，
避免简单任务浪费昂贵模型的 Token。

选择逻辑：
1. 环境变量覆盖: SCENE_MODEL_<SCENE>=provider/model
2. 内置配置: 按 tier + 能力匹配 + 成本排序
3. 跳过已熔断的 provider
"""
from __future__ import annotations
import os
from agent_core.llm import chat_completion, get_model, get_circuit_breaker, _provider_has_key
from agent_core.token_optimizer import count_messages_tokens

_SUMMARY_SYSTEM = "用一两句话概括以下对话的要点，只保留关键信息。"


class SceneModelMixin:
    """场景级模型选择 Mixin

    要求宿主类提供:
    - self._provider: str
    - self.model: str | None
    - self._total_tokens: int
    - self.router: ModelRouter
    """

    def _resolve_model(self, user_input: str, has_tool_calls: bool = False,
                       iteration: int = 0) -> tuple[str, str]:
        """返回 (provider, model)"""
        selected_provider, selected_model = self.router.select(user_input, has_tool_calls, iteration)
        if selected_provider:
            return selected_provider, selected_model or get_model(selected_provider)
        return self._provider, self.model or get_model(self._provider)

    def _llm_summarize(self, text: str) -> str:
        """使用廉价模型做摘要"""
        scene_provider, scene_model = self._get_scene_model("summarize")
        msgs = [{"role": "system", "content": _SUMMARY_SYSTEM}, {"role": "user", "content": text}]
        resp = chat_completion(msgs, tools=None, model=scene_model, provider=scene_provider)
        result = getattr(resp, "content", "") or ""
        self._total_tokens += count_messages_tokens(msgs) + len(result) // 3
        return result

    def _get_scene_model(self, scene: str) -> tuple[str, str]:
        """场景级模型选择 — 不同场景用不同模型

        场景: summarize / subagent / compaction / heartbeat / image / classification
        配置优先级: 环境变量 SCENE_MODEL_<SCENE> > 内置默认
        """
        env_key = "SCENE_MODEL_{}".format(scene.upper())
        env_val = os.environ.get(env_key, "")
        if env_val:
            from agent_core.model_router import parse_model_ref
            prov, model = parse_model_ref(env_val)
            if prov and model:
                return prov, model

        scene_config = {
            "summarize":      {"preferred_tier": 1, "caps": {"fast"}},
            "subagent":       {"preferred_tier": 1, "caps": {"fast", "tool_use"}},
            "compaction":     {"preferred_tier": 1, "caps": {"fast"}},
            "heartbeat":      {"preferred_tier": 1, "caps": {"fast"}},
            "classification": {"preferred_tier": 1, "caps": {"fast"}},
            "image":          {"preferred_tier": 2, "caps": {"reasoning"}},
        }
        cfg = scene_config.get(scene, {"preferred_tier": 1, "caps": {"fast"}})
        tier = cfg.get("preferred_tier", 1)
        required_caps = cfg.get("caps", {"fast"})

        from agent_core.model_router import MODEL_REGISTRY
        breaker = get_circuit_breaker()

        candidates = []
        for prov, models in MODEL_REGISTRY.items():
            if not _provider_has_key(prov) or breaker.is_open(prov):
                continue
            for model_name, info in models.items():
                if info["tier"] <= tier:
                    caps_match = len(required_caps & info["caps"]) / max(len(required_caps), 1)
                    cost = info["cost"]
                    candidates.append((prov, model_name, cost, caps_match))

        if candidates:
            candidates.sort(key=lambda x: (-x[3], x[2]))
            return candidates[0][0], candidates[0][1]
        return self._provider, self.model or get_model(self._provider)
