"""模型智能路由 — 标签 + 任务分类 + 历史学习

策略：
- 每个模型标注能力标签: fast, coding, reasoning, tool_use
- 任务自动分类为所需能力
- 根据能力匹配选择最佳模型
- 历史记录追踪模型成功率，动态调整
"""
from __future__ import annotations
import os, re, time
from collections import defaultdict

# ── 能力标签 ────────────────────────────────────────────────
# fast: 低延迟  coding: 代码生成  reasoning: 复杂推理  tool_use: 工具调用

MODEL_REGISTRY: dict[str, dict] = {
    "zhipu": {
        "glm-4-flash":  {"tier": 1, "caps": {"fast", "tool_use"}, "cost": 0},
        "glm-4-plus":   {"tier": 2, "caps": {"coding", "reasoning", "tool_use"}, "cost": 5},
    },
    "deepseek": {
        "deepseek-chat": {"tier": 2, "caps": {"fast", "coding", "reasoning", "tool_use"}, "cost": 1},
    },
    "openai": {
        "gpt-4o-mini":  {"tier": 1, "caps": {"fast", "coding", "tool_use"}, "cost": 3},
        "gpt-4o":       {"tier": 2, "caps": {"coding", "reasoning", "tool_use"}, "cost": 10},
    },
    "silicon": {
        "Qwen/Qwen2.5-7B-Instruct":  {"tier": 1, "caps": {"fast", "coding"}, "cost": 1},
        "Qwen/Qwen2.5-72B-Instruct": {"tier": 2, "caps": {"coding", "reasoning", "tool_use"}, "cost": 4},
    },
    "ollama": {
        "qwen2.5:7b":  {"tier": 1, "caps": {"fast", "coding"}, "cost": 0},
        "qwen2.5:14b": {"tier": 2, "caps": {"coding", "reasoning"}, "cost": 0},
    },
}

# 兼容旧接口
MODEL_TIERS: dict[str, dict] = {}
for _prov, _models in MODEL_REGISTRY.items():
    MODEL_TIERS[_prov] = {}
    for _m, _info in _models.items():
        MODEL_TIERS[_prov][_info["tier"]] = _m

# ── 任务分类规则（P2-7 升级版：加权关键词 + 上下文信号 + LLM 回退）──

# 加权关键词表：每个关键词带权重（0~1），支持更精细的分类
_KEYWORD_WEIGHTS: dict[str, list[tuple[str, float]]] = {
    "coding": [
        # 高权重：明确的编码意图
        (r"写代码|编程|写一个.*函数|写一个.*脚本|代码实现", 0.9),
        (r"implement|write.*code|write.*function|write.*script", 0.9),
        (r"debug|修复.*bug|fix.*bug|修复.*错误", 0.85),
        (r"refactor|重构|优化代码|代码优化", 0.8),
        # 中权重：可能涉及编码
        (r"算法|数据结构|正则|regex|sql|api", 0.7),
        (r"函数|类|接口|模块|组件|class|function|interface|module", 0.6),
        (r"python|java|javascript|typescript|rust|go|c\+\+|ruby|swift", 0.7),
        (r"import|def |return |async |await |const |let |var ", 0.8),
        # 移动端/框架术语
        (r"android|ios|kotlin|flutter|compose|swiftui|react native", 0.7),
        (r"handler|looper|activity|fragment|viewmodel|livedata|lifecycle", 0.6),
        (r"jetpack|hilt|dagger|room|retrofit|okhttp|gradle", 0.6),
        (r"vue|react|angular|svelte|webpack|vite|nextjs|nuxt", 0.6),
        (r"spring|django|fastapi|express|nestjs|graphql|grpc", 0.6),
        # 低权重：弱信号
        (r"开发|实现|implement|develop", 0.5),
        (r"测试|test|单元测试|unit test", 0.6),
    ],
    "reasoning": [
        # 高权重：明确的推理/分析意图
        (r"分析.*原因|为什么.*不|解释.*原理|深入分析", 0.9),
        (r"analyze.*why|explain.*how|root cause|深度分析", 0.9),
        (r"写一篇|写一个方案|技术方案|设计文档|架构设计", 0.85),
        (r"write.*essay|write.*proposal|design.*doc|architecture", 0.85),
        # 中权重：需要思考
        (r"分析|对比|比较|评估|权衡|优缺点|trade-?off", 0.6),
        (r"compare|evaluate|pros.*cons|advantages|disadvantages", 0.7),
        (r"总结|归纳|概括|summarize|conclude", 0.65),
        (r"设计|架构|design|architect|方案", 0.6),
        (r"analyze|analyse", 0.6),
        # 技术原理/机制类问题
        (r"机制|原理|底层|源码|实现原理|工作原理", 0.6),
        (r"mechanism|principle|internal|under the hood|how.*work", 0.6),
        (r"生命周期|lifecycle|流程|pipeline|调用链|调用栈", 0.5),
        # 低权重：弱信号
        (r"为什么|原因|why|how come|怎么回事", 0.5),
        (r"解释|explain|说明|详细", 0.4),
        (r"完整的|全面的|comprehensive|detailed|thorough", 0.5),
    ],
    "tool_use": [
        # 高权重：明确的工具/操作意图
        (r"搜索.*并|查找.*然后|先.*再.*最后", 0.9),
        (r"search.*and|find.*then|first.*then.*finally", 0.9),
        (r"运行|执行|run |execute|调用|invoke", 0.8),
        (r"下载|上传|download|upload|fetch|抓取|爬取", 0.8),
        # 中权重：可能需要工具
        (r"文件|目录|路径|file|directory|folder|path", 0.6),
        (r"搜索|查找|查询|search|find|query|look up", 0.6),
        (r"步骤|流程|依次|step|workflow|pipeline", 0.5),
        (r"安装|部署|install|deploy|配置|configure", 0.6),
    ],
    "fast": [
        # 高权重：明确的简单交互
        (r"^(你好|hi|hello|hey|嗨|谢谢|感谢|ok|好的|明白|嗯|对|是的)$", 1.0),
        (r"^(你好|hi|hello|hey|嗨|谢谢|感谢|ok|好的|明白)", 0.8),
        # 中权重：简单查询
        (r"几点|什么时间|今天|天气|日期|星期", 0.7),
        (r"what time|what day|weather|date today", 0.7),
        (r"^(是什么|什么是).{0,10}$", 0.6),
        (r"^what is .{0,20}$", 0.6),
    ],
}

# 预编译加权模式
_compiled_weighted: dict[str, list[tuple[re.Pattern, float]]] = {
    cap: [(re.compile(pat, re.IGNORECASE), w) for pat, w in pairs]
    for cap, pairs in _KEYWORD_WEIGHTS.items()
}

# 上下文信号检测器
_CONTEXT_SIGNALS: dict[str, list[tuple[re.Pattern, float]]] = {
    "coding": [
        (re.compile(r"```"), 0.4),                          # 代码块
        (re.compile(r"\.py|\.js|\.ts|\.java|\.go|\.rs"), 0.3),  # 文件扩展名
        (re.compile(r"error|traceback|exception|stack", re.I), 0.3),  # 错误信息
        (re.compile(r"import |from .+ import|require\(|#include", re.I), 0.5),  # 导入语句
    ],
    "reasoning": [
        (re.compile(r"\?"), 0.15),                          # 问号（弱信号）
        (re.compile(r"[\u4e00-\u9fff]{50,}"), 0.2),        # 长中文段落
        (re.compile(r"\b\d+\.\s"), 0.15),                   # 编号列表
        (re.compile(r"vs\.?|versus|对比|比较", re.I), 0.25),  # 对比信号
    ],
    "tool_use": [
        (re.compile(r"https?://"), 0.3),                    # URL
        (re.compile(r"[~/][\w/.-]+\.\w+"), 0.2),           # 文件路径
        (re.compile(r"\$\s*\w+|`[^`]+`"), 0.15),           # 命令/代码引用
    ],
}

# 向后兼容：旧代码可能引用 _compiled_patterns
_compiled_patterns = {
    cap: re.compile("|".join(pat for pat, _ in pairs), re.IGNORECASE)
    for cap, pairs in _KEYWORD_WEIGHTS.items()
}

# 分类置信度阈值
_CLASSIFY_THRESHOLD = 0.4
# 模糊区间（分数在此范围内视为不确定，可触发 LLM 回退）
_AMBIGUOUS_MARGIN = 0.15

# LLM 分类开关
_LLM_CLASSIFY_ENABLED = os.environ.get("LLM_CLASSIFY", "").lower() in ("1", "true", "yes")


def _score_capabilities(text: str) -> dict[str, float]:
    """对输入文本计算每个能力的置信度分数 (0~1)"""
    scores: dict[str, float] = {cap: 0.0 for cap in _compiled_weighted}

    for cap, patterns in _compiled_weighted.items():
        max_score = 0.0
        hit_count = 0
        for pattern, weight in patterns:
            if pattern.search(text):
                max_score = max(max_score, weight)
                hit_count += 1
        # 多关键词命中有加成（但不超过 1.0）
        bonus = min(hit_count * 0.05, 0.2) if hit_count > 1 else 0
        scores[cap] = min(max_score + bonus, 1.0)

    # 叠加上下文信号
    for cap, signals in _CONTEXT_SIGNALS.items():
        signal_boost = 0.0
        for pattern, weight in signals:
            if pattern.search(text):
                signal_boost += weight
        scores[cap] = min(scores.get(cap, 0) + signal_boost, 1.0)

    return scores


def _classify_with_llm(text: str) -> set[str] | None:
    """用便宜模型做任务分类（可选，仅在 LLM_CLASSIFY=1 时启用）

    返回 None 表示 LLM 分类不可用或失败，调用方应回退到规则分类。
    """
    if not _LLM_CLASSIFY_ENABLED:
        return None
    try:
        from agent_core.llm import chat_completion
        prompt = (
            "将以下用户输入分类为一个或多个能力标签，只返回标签名，用逗号分隔。\n"
            "可选标签: fast, coding, reasoning, tool_use\n"
            "- fast: 简单问候、闲聊、简短查询\n"
            "- coding: 编写/修改/调试代码\n"
            "- reasoning: 分析、对比、设计、长文写作\n"
            "- tool_use: 需要搜索、文件操作、命令执行\n\n"
            "用户输入: {}\n\n标签:".format(text[:200])
        )
        # 使用场景级模型（classification 场景，最便宜的模型）
        resp = chat_completion(
            [{"role": "user", "content": prompt}],
            model=os.environ.get("SCENE_MODEL_CLASSIFICATION", "glm-4-flash"),
        )
        content = resp.content if hasattr(resp, "content") else str(resp)
        valid_caps = {"fast", "coding", "reasoning", "tool_use"}
        result = set()
        for token in re.split(r"[,，\s]+", content.strip().lower()):
            token = token.strip()
            if token in valid_caps:
                result.add(token)
        return result if result else None
    except Exception:
        return None


def classify_task(text: str, has_tool_calls: bool = False, iteration: int = 0) -> set[str]:
    """分析用户输入，返回所需能力集合

    P2-7 升级版分类策略（三层）:
    1. 加权关键词评分 — 每个关键词带权重，多命中有加成
    2. 上下文信号叠加 — 代码块、URL、文件路径等结构化信号
    3. LLM 回退（可选）— 评分模糊时用便宜模型做最终判定

    置信度 >= 0.4 的能力被选中。
    """
    # 工具调用后的续轮（呈现结果）— 保持 fast，不升级模型
    if iteration > 0 and has_tool_calls:
        return {"fast", "tool_use"}

    # 第一层 + 第二层：加权关键词 + 上下文信号
    scores = _score_capabilities(text)

    # 选出超过阈值的能力
    needs = {cap for cap, score in scores.items() if score >= _CLASSIFY_THRESHOLD}

    # 检测是否存在模糊区间（最高分和次高分接近）
    sorted_scores = sorted(scores.values(), reverse=True)
    is_ambiguous = (
        len(sorted_scores) >= 2
        and sorted_scores[0] >= _CLASSIFY_THRESHOLD
        and sorted_scores[0] - sorted_scores[1] < _AMBIGUOUS_MARGIN
        and sorted_scores[1] >= _CLASSIFY_THRESHOLD * 0.7
    )

    # 第三层：LLM 回退（仅在模糊时触发，避免浪费调用）
    if is_ambiguous and _LLM_CLASSIFY_ENABLED:
        llm_result = _classify_with_llm(text)
        if llm_result:
            return llm_result

    # 长度启发式（仅在无关键词命中时生效）
    if not needs:
        if len(text) < 20:
            needs.add("fast")
        elif len(text) >= 50:
            needs.add("reasoning")
        else:
            needs.add("fast")

    return needs


# ── 历史学习 ────────────────────────────────────────────────

class ModelHistory:
    """追踪模型在不同任务类型上的成功率（带持久化）"""

    _SAVE_PATH = os.path.expanduser("~/.nexus-agent/model_history.json")

    def __init__(self):
        # key: (provider, model, task_type) → {"success": int, "fail": int}
        self._records: dict[tuple, dict] = defaultdict(lambda: {"success": 0, "fail": 0})
        self._load()

    def _load(self):
        """从磁盘加载历史记录"""
        try:
            if os.path.exists(self._SAVE_PATH):
                import json
                with open(self._SAVE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    key = tuple(item["key"])
                    self._records[key] = {"success": item["s"], "fail": item["f"]}
        except Exception:
            pass

    def _save(self):
        """持久化到磁盘"""
        try:
            import json
            os.makedirs(os.path.dirname(self._SAVE_PATH), exist_ok=True)
            data = [{"key": list(k), "s": v["success"], "f": v["fail"]}
                    for k, v in self._records.items()
                    if v["success"] + v["fail"] > 0]
            with open(self._SAVE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def record(self, provider: str, model: str, task_caps: set[str], success: bool):
        for cap in task_caps:
            key = (provider, model, cap)
            if success:
                self._records[key]["success"] += 1
            else:
                self._records[key]["fail"] += 1
        self._save()

    def score(self, provider: str, model: str, task_caps: set[str]) -> float:
        """计算模型对该任务的历史得分 (0~1)，无记录返回 0.5"""
        if not task_caps:
            return 0.5
        scores = []
        for cap in task_caps:
            key = (provider, model, cap)
            rec = self._records.get(key)
            if rec and (rec["success"] + rec["fail"]) > 0:
                scores.append(rec["success"] / (rec["success"] + rec["fail"]))
            else:
                scores.append(0.5)
        return sum(scores) / len(scores)


# ── 路由器 ──────────────────────────────────────────────────

class ModelRouter:
    """模型路由器 — 能力匹配 + 成本优化 + 历史学习"""

    def __init__(self):
        self.mode: str = os.environ.get("MODEL_MODE", "auto").lower()
        self._last_tier: int = 0
        self._last_reason: str = ""
        self._last_caps: set[str] = set()
        self.history = ModelHistory()
        # 动态注册 OLLAMA_MODEL 环境变量指定的自定义模型
        # 放在 __init__ 而非模块级，确保 dotenv 已加载完毕
        self._register_custom_ollama()

    def select(self, user_input: str, has_tool_calls: bool = False,
               iteration: int = 0) -> tuple[str | None, str | None]:
        """根据输入选择提供商和模型"""
        if self.mode != "auto":
            self._last_tier = 0
            self._last_reason = f"固定模式: {self.mode}"
            return None, None

        needs = classify_task(user_input, has_tool_calls, iteration)
        self._last_caps = needs

        # 找最佳匹配
        best = self._find_best(needs)
        if not best:
            self._last_reason = "无可用提供商，使用默认"
            return None, None

        provider, model, score, reason = best
        self._last_tier = MODEL_REGISTRY.get(provider, {}).get(model, {}).get("tier", 1)
        self._last_reason = reason
        # 对 ollama provider，如果路由器选的是 tier 1 默认模型，
        # 用 OLLAMA_MODEL 环境变量的值替换（因为用户实际安装的可能不同）
        # tier 2 模型不替换（用户可能同时安装了多个模型）
        if provider == "ollama":
            selected_tier = MODEL_REGISTRY.get("ollama", {}).get(model, {}).get("tier", 1)
            if selected_tier == 1:
                from agent_core.llm import get_model as _get_model
                actual_model = _get_model("ollama")
                if actual_model != model:
                    self._last_reason = reason.replace(model, actual_model)
                    model = actual_model
        return provider, model

    def _find_best(self, needs: set[str]) -> tuple[str, str, float, str] | None:
        """在所有可用模型中找最佳匹配（感知熔断状态）"""
        from agent_core.llm import get_circuit_breaker
        breaker = get_circuit_breaker()
        candidates = []

        for provider, models in MODEL_REGISTRY.items():
            if not self._has_key(provider):
                continue
            # 跳过已熔断的 provider
            if breaker.is_open(provider):
                continue
            for model_name, info in models.items():
                caps = info["caps"]
                cost = info["cost"]
                tier = info["tier"]

                # 能力覆盖度 (0~1)
                if needs:
                    coverage = len(needs & caps) / len(needs)
                else:
                    coverage = 1.0

                # 历史得分
                hist_score = self.history.score(provider, model_name, needs)

                # 综合评分: 能力覆盖 * 0.5 + 历史 * 0.3 + 成本优势 * 0.2
                cost_score = 1.0 - (cost / 10.0)  # cost 0~10 → score 1~0
                total = coverage * 0.5 + hist_score * 0.3 + cost_score * 0.2

                candidates.append((provider, model_name, total, coverage, tier))

        if not candidates:
            return None

        # 如果只需要 fast，优先选 tier 1
        only_fast = needs == {"fast"}

        # 排序: 总分降序，同分时 tier 低的优先（省钱）
        candidates.sort(key=lambda x: (-x[2], x[4] if not only_fast else -x[4]))

        best = candidates[0]
        provider, model, score, coverage, tier = best
        caps_str = ",".join(sorted(needs))
        reason = f"需求[{caps_str}] → {provider}/{model} (匹配{coverage:.0%}, 评分{score:.2f})"
        return provider, model, score, reason

    def record_result(self, success: bool):
        """记录本次调用结果，用于历史学习"""
        current_provider = os.environ.get("LLM_PROVIDER", "").lower()
        from agent_core.llm import get_model
        current_model = get_model()
        self.history.record(current_provider, current_model, self._last_caps, success)

    @staticmethod
    def _has_key(provider: str) -> bool:
        from agent_core.llm import PROVIDERS
        cfg = PROVIDERS.get(provider, {})
        key_env = cfg.get("key_env")
        if not key_env:
            # 无需 key 的提供商（如 ollama）检查服务是否可达
            if provider == "ollama":
                return ModelRouter._is_ollama_running()
            return True
        key = os.environ.get(key_env, "")
        return bool(key) and key not in ("sk-xxx", "xxx", "")

    @staticmethod
    def _is_ollama_running() -> bool:
        """检查 Ollama 服务是否在运行

        可通过 DISABLE_OLLAMA=1 跳过检测（未安装 Ollama 时避免无效探测）。
        """
        if os.environ.get("DISABLE_OLLAMA", "").strip() in ("1", "true", "yes"):
            return False
        try:
            import urllib.request
            req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
            urllib.request.urlopen(req, timeout=1)
            return True
        except Exception:
            return False

    @staticmethod
    def _register_custom_ollama():
        """动态注册 OLLAMA_MODEL 环境变量指定的自定义模型

        用户可能安装了不在默认列表中的模型（如 qwen2.5:3b-instruct、gemma:2b 等），
        需要在运行时注册到 MODEL_REGISTRY 和 MODEL_TIERS 中。
        """
        custom = os.environ.get("OLLAMA_MODEL", "")
        if not custom:
            return
        ollama_models = MODEL_REGISTRY.get("ollama", {})
        if custom not in ollama_models:
            ollama_models[custom] = {"tier": 1, "caps": {"fast", "coding"}, "cost": 0}
            # 同步更新 MODEL_TIERS
            if "ollama" not in MODEL_TIERS:
                MODEL_TIERS["ollama"] = {}
            if 1 not in MODEL_TIERS["ollama"]:
                MODEL_TIERS["ollama"][1] = custom

    @property
    def last_selection(self) -> str:
        return self._last_reason


# ── P3: 统一模型引用格式 ────────────────────────────────────

def parse_model_ref(ref: str) -> tuple[str, str]:
    """解析 provider/model 格式的模型引用

    Examples:
        "zhipu/glm-4-flash" → ("zhipu", "glm-4-flash")
        "deepseek-chat" → ("", "deepseek-chat")
        "openai/gpt-4o" → ("openai", "gpt-4o")
    """
    if "/" in ref:
        parts = ref.split("/", 1)
        return parts[0].strip().lower(), parts[1].strip()
    return "", ref.strip()


def format_model_ref(provider: str, model: str) -> str:
    """格式化为 provider/model 引用"""
    return "{}/{}".format(provider, model)


def resolve_model_ref(ref: str) -> tuple[str, str]:
    """解析模型引用并验证，返回 (provider, model)

    支持:
    - "zhipu/glm-4-flash" → 直接解析
    - "glm-4-flash" → 在 MODEL_REGISTRY 中查找
    - "zhipu" → 使用该 provider 的默认模型
    """
    provider, model = parse_model_ref(ref)

    if provider and model:
        # 完整引用
        return provider, model

    if not provider and model:
        # 只有模型名，查找 provider
        for prov, models in MODEL_REGISTRY.items():
            if model in models:
                return prov, model
        # 可能是 provider 名
        if model in MODEL_REGISTRY:
            from agent_core.llm import get_model
            return model, get_model(model)

    return provider or "", model or ""
