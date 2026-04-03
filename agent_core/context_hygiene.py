"""上下文卫生系统 — 解决四大上下文痛点

痛点一: 上下文中毒 → MemoryDetox（记忆消毒）
痛点二: 上下文分心 → FocusManager（焦点管理）
痛点三: 上下文混乱 → ToolBudget（工具预算）
痛点四: 上下文冲突 → ConflictDetector（冲突检测）

设计原则: 做减法，保持上下文清爽。
"""
from __future__ import annotations
import re, time
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════
# 痛点一: 上下文中毒 — MemoryDetox
# ═══════════════════════════════════════════════════════════

@dataclass
class ToxicMarker:
    """毒性标记"""
    index: int          # memory 中的位置
    reason: str         # 中毒原因
    severity: str       # low / medium / high
    timestamp: float = 0.0


class MemoryDetox:
    """记忆消毒器 — 检测并标记 memory 中的有毒信息

    策略:
    1. 工具失败的结果标记为不可信
    2. 被用户纠正过的 assistant 回复降权
    3. 幻觉检测标记的内容打上毒性标签
    4. 在 build_context 时，有毒消息被摘要替代而非原文保留
    """

    # 用户纠正模式（中英文）
    _CORRECTION_PATTERNS = [
        re.compile(r"不对|错了|不是这样|你说错了|纠正|更正", re.IGNORECASE),
        re.compile(r"wrong|incorrect|no,?\s*(?:it|that|this)\s*(?:is|should)", re.IGNORECASE),
        re.compile(r"其实|实际上|应该是|正确的是", re.IGNORECASE),
    ]

    # 工具失败标记
    _TOOL_FAILURE_PATTERNS = [
        re.compile(r"\[(?:错误|工具执行错误|操作已取消|Hook 阻止|策略拦截)\]"),
        re.compile(r"\[Error\]|\[Failed\]|Traceback|Exception:", re.IGNORECASE),
    ]

    def __init__(self):
        self._toxic_indices: dict[int, ToxicMarker] = {}

    def scan(self, memory: list[dict]) -> list[ToxicMarker]:
        """扫描 memory，返回新发现的毒性标记"""
        markers = []
        for i, msg in enumerate(memory):
            if i in self._toxic_indices:
                continue
            marker = self._check_message(i, msg, memory)
            if marker:
                self._toxic_indices[i] = marker
                markers.append(marker)
        return markers

    def _check_message(self, idx: int, msg: dict, memory: list[dict]) -> ToxicMarker | None:
        content = msg.get("content", "")
        if not content or not isinstance(content, str):
            return None

        # 检查工具返回的失败结果
        if msg.get("role") == "tool":
            for pat in self._TOOL_FAILURE_PATTERNS:
                if pat.search(content):
                    return ToxicMarker(idx, "工具执行失败", "medium", time.time())

        # 检查 assistant 回复是否被用户纠正
        if msg.get("role") == "assistant" and idx + 1 < len(memory):
            next_msg = memory[idx + 1]
            if next_msg.get("role") == "user":
                next_content = next_msg.get("content", "")
                for pat in self._CORRECTION_PATTERNS:
                    if pat.search(next_content):
                        return ToxicMarker(idx, "被用户纠正", "high", time.time())

        return None

    def get_toxic_indices(self) -> set[int]:
        return set(self._toxic_indices.keys())

    def is_toxic(self, index: int) -> bool:
        return index in self._toxic_indices

    def get_marker(self, index: int) -> ToxicMarker | None:
        return self._toxic_indices.get(index)

    def clear(self):
        self._toxic_indices.clear()


# ═══════════════════════════════════════════════════════════
# 痛点二: 上下文分心 — FocusManager
# ═══════════════════════════════════════════════════════════

class FocusManager:
    """焦点管理器 — 让上下文聚焦当前任务

    策略:
    1. 动态调整 short_term_turns: 简单对话保留少，复杂任务保留多
    2. 话题切换检测: 用户换了话题时，主动压缩旧话题
    3. 重要性评分: 每条消息打分，裁剪时优先丢低分消息
    """

    # 话题切换信号
    _TOPIC_SWITCH_PATTERNS = [
        re.compile(r"换个话题|另外|对了|还有个问题|新的问题", re.IGNORECASE),
        re.compile(r"by the way|another question|new topic|let's talk about", re.IGNORECASE),
        re.compile(r"^(?:好的|ok|行|嗯)[\s,，]*(?:现在|接下来|然后)", re.IGNORECASE),
    ]

    def __init__(self):
        self._topic_boundaries: list[int] = []  # memory 中话题切换的位置

    def detect_topic_switch(self, user_input: str) -> bool:
        """检测用户是否切换了话题"""
        for pat in self._TOPIC_SWITCH_PATTERNS:
            if pat.search(user_input):
                return True
        return False

    def adaptive_short_term_turns(self, memory: list[dict], user_input: str) -> int:
        """动态计算应保留的短期记忆轮数

        规则:
        - 简单问答（无工具调用）: 2 轮
        - 普通对话: 3 轮
        - 复杂任务（有工具调用链）: 4-6 轮
        - 话题切换后: 重置为 2 轮
        """
        if self.detect_topic_switch(user_input):
            return 2

        # 统计最近消息中的工具调用数
        recent_tool_calls = 0
        for msg in memory[-10:]:
            if msg.get("role") == "tool":
                recent_tool_calls += 1
            if msg.get("tool_calls"):
                recent_tool_calls += len(msg["tool_calls"])

        if recent_tool_calls == 0:
            return 2
        elif recent_tool_calls <= 2:
            return 3
        elif recent_tool_calls <= 5:
            return 4
        else:
            return 6

    def score_message(self, msg: dict, position: int, total: int) -> float:
        """给消息打重要性分数 (0~1)

        评分因素:
        - 位置: 越新越重要
        - 角色: user > assistant > tool
        - 内容: 有决策/偏好的更重要
        - 工具调用: 有结果的比失败的重要
        """
        score = 0.0

        # 位置分 (0.0 ~ 0.4)
        if total > 0:
            score += 0.4 * (position / total)

        # 角色分
        role = msg.get("role", "")
        if role == "user":
            score += 0.3
        elif role == "assistant":
            score += 0.2
        elif role == "system":
            score += 0.5  # system 消息始终高优
        elif role == "tool":
            score += 0.1

        # 内容质量分
        content = msg.get("content", "")
        if isinstance(content, str):
            # 有决策/偏好关键词加分
            if re.search(r"选择|决定|采用|方案|偏好|记住|注意", content):
                score += 0.2
            # 有代码块加分（可能是重要的实现）
            if "```" in content:
                score += 0.1
            # 工具失败结果减分
            if re.search(r"\[错误\]|\[失败\]|Error|Failed", content):
                score -= 0.1

        return max(0.0, min(1.0, score))


# ═══════════════════════════════════════════════════════════
# 痛点三: 上下文混乱 — ToolBudget
# ═══════════════════════════════════════════════════════════

class ToolBudget:
    """工具预算管理器 — 控制工具定义占用的上下文空间

    策略:
    1. MCP 工具也参与筛选，不再无条件全保留
    2. 基于使用频率的工具排序: 常用工具优先保留
    3. 工具总 token 预算: 不超过上下文窗口的 15%
    4. 分级加载: 核心工具 → 匹配工具 → 其他工具
    """

    # 核心工具（始终保留）
    CORE_TOOLS = {"web_search", "shell", "read_file", "write_file"}

    def __init__(self, max_tool_token_ratio: float = 0.15):
        self._max_ratio = max_tool_token_ratio
        self._usage_count: dict[str, int] = {}  # 工具使用频率

    def record_usage(self, tool_name: str):
        """记录工具使用"""
        self._usage_count[tool_name] = self._usage_count.get(tool_name, 0) + 1

    def select_tools(self, user_input: str, all_tools: list[dict],
                     context_budget: int, tool_keywords: dict) -> list[dict]:
        """智能工具筛选 — 包括 MCP 工具

        三层筛选:
        1. 核心工具（始终保留）
        2. 关键词匹配的工具（含 MCP）
        3. 高频使用的工具

        最终受 token 预算约束。
        """
        if len(all_tools) <= 4:
            return all_tools

        tool_budget = int(context_budget * self._max_ratio)

        # 分类
        core = []
        matched = []
        frequent = []
        rest = []

        lower_input = user_input.lower()
        matched_names = set()

        # 关键词匹配（内置工具）
        for name, kws in tool_keywords.items():
            if any(kw in lower_input for kw in kws):
                matched_names.add(name)

        # MCP 工具也做关键词匹配
        for spec in all_tools:
            name = spec["function"]["name"]
            if name.startswith("mcp_"):
                desc = spec["function"].get("description", "").lower()
                # 用工具描述和名称做模糊匹配
                if any(kw in desc or kw in name.lower() for kw in lower_input.split()
                       if len(kw) >= 2):
                    matched_names.add(name)

        for spec in all_tools:
            name = spec["function"]["name"]
            if name in self.CORE_TOOLS:
                core.append(spec)
            elif name in matched_names:
                matched.append(spec)
            elif self._usage_count.get(name, 0) >= 3:
                frequent.append(spec)
            else:
                rest.append(spec)

        # 按优先级组装，受预算约束
        from agent_core.token_optimizer import count_tokens
        import json

        selected = []
        used_tokens = 0

        for group in [core, matched, frequent, rest]:
            # 频率排序（高频优先）
            group.sort(key=lambda s: self._usage_count.get(
                s["function"]["name"], 0), reverse=True)
            for spec in group:
                spec_tokens = count_tokens(json.dumps(spec, ensure_ascii=False))
                if used_tokens + spec_tokens <= tool_budget:
                    selected.append(spec)
                    used_tokens += spec_tokens
                elif group is core:
                    # 核心工具强制保留
                    selected.append(spec)
                    used_tokens += spec_tokens

        # 兜底: 至少保留核心工具
        if len(selected) < 2:
            return core + matched[:3] if matched else all_tools[:6]

        return selected


# ═══════════════════════════════════════════════════════════
# 痛点四: 上下文冲突 — ConflictDetector
# ═══════════════════════════════════════════════════════════

@dataclass
class Conflict:
    """上下文冲突"""
    type: str           # decision / preference / fact
    earlier: str        # 较早的信息
    later: str          # 较新的信息
    earlier_idx: int    # 较早消息的位置
    later_idx: int      # 较新消息的位置
    resolution: str     # 建议的解决方式


class ConflictDetector:
    """冲突检测器 — 发现 memory 中前后矛盾的信息

    检测类型:
    1. 决策冲突: "用方案 A" vs "用方案 B"
    2. 偏好冲突: "以后用中文" vs "switch to English"
    3. 事实冲突: 对同一事物的矛盾描述
    """

    # 决策模式
    _DECISION_PATTERN = re.compile(
        r"(?:选择|决定|采用|使用|切换到|改为|换成|用)\s*(.{2,30}?)(?:[。，,.!！?？]|$)"
    )

    # 偏好模式
    _PREFERENCE_PATTERN = re.compile(
        r"(?:以后|始终|总是|一直|都)\s*(?:用|使用|采用)\s*(.{2,30}?)(?:[。，,.!！?？]|$)"
    )

    # 否定模式（用于检测对之前决策的否定）
    _NEGATION_PATTERN = re.compile(
        r"(?:不要|别|不用|不再|取消|放弃)\s*(?:用|使用|采用)\s*(.{2,20}?)(?:[。，,.\s]|$)"
    )

    def detect(self, memory: list[dict]) -> list[Conflict]:
        """扫描 memory 中的冲突"""
        conflicts = []

        decisions = self._extract_decisions(memory)
        conflicts.extend(self._find_decision_conflicts(decisions))

        preferences = self._extract_preferences(memory)
        conflicts.extend(self._find_preference_conflicts(preferences))

        return conflicts

    def resolve_prompt(self, conflicts: list[Conflict]) -> str | None:
        """生成冲突解决提示，注入 system prompt

        原则: 以最新的用户指令为准（后来居上）
        """
        if not conflicts:
            return None

        parts = ["[上下文冲突检测 — 以最新指令为准]"]
        for c in conflicts[-3:]:  # 最多展示 3 个
            parts.append("- {}: 早期「{}」→ 最新「{}」，采用最新".format(
                c.type, c.earlier[:30], c.later[:30]))
        return "\n".join(parts)

    def _extract_decisions(self, memory: list[dict]) -> list[tuple[int, str]]:
        """提取所有决策"""
        results = []
        for i, msg in enumerate(memory):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            for m in self._DECISION_PATTERN.finditer(content):
                results.append((i, m.group(1).strip()))
            # 否定也是一种决策
            for m in self._NEGATION_PATTERN.finditer(content):
                results.append((i, "不用" + m.group(1).strip()))
        return results

    def _extract_preferences(self, memory: list[dict]) -> list[tuple[int, str]]:
        """提取所有偏好"""
        results = []
        for i, msg in enumerate(memory):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            for m in self._PREFERENCE_PATTERN.finditer(content):
                results.append((i, m.group(1).strip()))
        return results

    def _find_decision_conflicts(self, decisions: list[tuple[int, str]]) -> list[Conflict]:
        """检测决策冲突 — 同一主题的不同决策"""
        if len(decisions) < 2:
            return []

        conflicts = []
        for i in range(len(decisions)):
            for j in range(i + 1, len(decisions)):
                idx_a, dec_a = decisions[i]
                idx_b, dec_b = decisions[j]
                # 内容不同且主题有重叠 → 冲突
                if dec_a != dec_b and self._decisions_conflict(dec_a, dec_b):
                    conflicts.append(Conflict(
                        type="decision",
                        earlier=dec_a,
                        later=dec_b,
                        earlier_idx=idx_a,
                        later_idx=idx_b,
                        resolution="采用最新决策: {}".format(dec_b),
                    ))
        return conflicts

    def _find_preference_conflicts(self, preferences: list[tuple[int, str]]) -> list[Conflict]:
        """检测偏好冲突"""
        if len(preferences) < 2:
            return []

        conflicts = []
        for i in range(len(preferences)):
            for j in range(i + 1, len(preferences)):
                idx_a, pref_a = preferences[i]
                idx_b, pref_b = preferences[j]
                if pref_a != pref_b and self._decisions_conflict(pref_a, pref_b):
                    conflicts.append(Conflict(
                        type="preference",
                        earlier=pref_a,
                        later=pref_b,
                        earlier_idx=idx_a,
                        later_idx=idx_b,
                        resolution="采用最新偏好: {}".format(pref_b),
                    ))
        return conflicts

    @staticmethod
    def _decisions_conflict(a: str, b: str) -> bool:
        """判断两个决策/偏好是否冲突

        策略:
        1. 有共同关键词但整体不同 → 冲突（如 "方案 A" vs "方案 B"）
        2. 属于同一语义类别 → 冲突（如 "中文回答" vs "英文回答"）
        """
        # 英文词 + 中文双字词（bigram）
        en_words_a = set(re.findall(r'[a-zA-Z]+', a.lower()))
        en_words_b = set(re.findall(r'[a-zA-Z]+', b.lower()))
        cn_chars_a = re.findall(r'[\u4e00-\u9fff]', a)
        cn_chars_b = re.findall(r'[\u4e00-\u9fff]', b)

        # 中文 bigram
        cn_bigrams_a = {cn_chars_a[i] + cn_chars_a[i+1] for i in range(len(cn_chars_a) - 1)}
        cn_bigrams_b = {cn_chars_b[i] + cn_chars_b[i+1] for i in range(len(cn_chars_b) - 1)}

        words_a = en_words_a | cn_bigrams_a | set(cn_chars_a)
        words_b = en_words_b | cn_bigrams_b | set(cn_chars_b)

        if not words_a or not words_b:
            return False

        # 有共同词/bigram 且整体不同 → 冲突
        overlap = words_a & words_b
        if overlap and a != b:
            return True

        # 语义类别检测（同类但不同值）
        _SEMANTIC_GROUPS = [
            {"中文", "英文", "日文", "韩文", "法文", "德文", "chinese", "english", "japanese"},
            {"方案", "plan", "option", "scheme"},
            {"python", "java", "javascript", "typescript", "go", "rust", "c"},
        ]
        all_a = en_words_a | set(cn_chars_a) | cn_bigrams_a
        all_b = en_words_b | set(cn_chars_b) | cn_bigrams_b
        for group in _SEMANTIC_GROUPS:
            a_in = all_a & group
            b_in = all_b & group
            if a_in and b_in and a_in != b_in:
                return True

        return False


# ═══════════════════════════════════════════════════════════
# 统一入口 — ContextHygiene
# ═══════════════════════════════════════════════════════════

class ContextHygiene:
    """上下文卫生系统 — 统一管理四大痛点的解决方案

    在 Agent 的 run loop 中调用:
    1. 每轮开始前: scan_and_clean() 扫描并清理
    2. 构建上下文时: get_adaptive_turns() 获取动态轮数
    3. 选择工具时: select_tools() 智能筛选
    4. 构建摘要时: get_conflict_resolution() 获取冲突解决提示
    """

    def __init__(self):
        self.detox = MemoryDetox()
        self.focus = FocusManager()
        self.tool_budget = ToolBudget()
        self.conflict = ConflictDetector()

    def scan_and_clean(self, memory: list[dict]) -> dict:
        """扫描 memory，返回卫生报告

        返回:
        {
            "toxic_count": int,       # 有毒消息数
            "conflicts": list,        # 冲突列表
            "conflict_prompt": str,   # 冲突解决提示（可注入 system prompt）
        }
        """
        toxic_markers = self.detox.scan(memory)
        conflicts = self.conflict.detect(memory)
        conflict_prompt = self.conflict.resolve_prompt(conflicts)

        return {
            "toxic_count": len(toxic_markers),
            "toxic_markers": toxic_markers,
            "conflicts": conflicts,
            "conflict_prompt": conflict_prompt,
        }

    def get_adaptive_turns(self, memory: list[dict], user_input: str) -> int:
        """获取动态短期记忆轮数"""
        return self.focus.adaptive_short_term_turns(memory, user_input)

    def score_messages(self, memory: list[dict]) -> list[float]:
        """给所有消息打重要性分数"""
        total = len(memory)
        scores = []
        for i, msg in enumerate(memory):
            base_score = self.focus.score_message(msg, i, total)
            # 有毒消息降权
            if self.detox.is_toxic(i):
                marker = self.detox.get_marker(i)
                if marker and marker.severity == "high":
                    base_score *= 0.3
                else:
                    base_score *= 0.6
            scores.append(base_score)
        return scores

    def record_tool_usage(self, tool_name: str):
        """记录工具使用（供 ToolBudget 统计）"""
        self.tool_budget.record_usage(tool_name)

    def reset(self):
        """重置所有状态"""
        self.detox.clear()
        self.focus = FocusManager()
        self.tool_budget = ToolBudget()
