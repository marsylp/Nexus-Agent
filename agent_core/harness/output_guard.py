"""OutputGuard — Agent 输出质量守卫

Harness 驾驭层的第四个检查维度：不只管"写代码"，还管"说话"。

检查 Agent 回复中的质量问题:
1. 幻觉检测: 编造不存在的 URL、API、库名
2. 跑题检测: 回复与用户问题的相关性
3. 过度输出: 简单问题给出过长回复（弱模型常见）
4. 代码幻觉: 在非代码问题中编造代码示例
5. 格式混乱: 输出结构不清晰

与 Layer 1-3 的关系:
- Layer 1 (约束执行): 管写操作的代码质量
- Layer 2 (认知感知): 管项目结构理解
- Layer 3 (度量闭环): 管历史趋势
- OutputGuard: 管 Agent 回复的对话质量 ← 新增
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class OutputIssue:
    """输出质量问题"""
    type: str       # hallucination / off_topic / verbose / code_hallucination / format
    severity: str   # info / warn / error
    message: str
    suggestion: str = ""


# ── 检测规则 ────────────────────────────────────────────────

# 常见幻觉 URL 模式（不存在的域名）
_HALLUCINATED_URLS = re.compile(
    r'https?://(?:weather-app\.com|news-app\.com|example-api\.com|'
    r'my-api\.com|fake-api\.com|test-server\.com|api-server\.com)',
    re.IGNORECASE,
)

# 编造的 Python 包名模式
_FAKE_PACKAGES = re.compile(
    r'(?:import|from)\s+(?:weather_api|news_api|my_module|fake_module|test_lib)\b'
)

# 代码块计数
_CODE_BLOCK = re.compile(r'```[\s\S]*?```')

# 简单问候/身份问题的模式
_SIMPLE_QUESTION = re.compile(
    r'^(?:你是谁|你好|hi|hello|hey|谢谢|你能做什么|介绍一下你自己|what are you|who are you)\s*[？?！!。.]*$',
    re.IGNORECASE,
)

# 简单事实问题
_FACTUAL_QUESTION = re.compile(
    r'^(?:什么是|.*是什么|.*怎么用|.*的区别|how to|what is)\s',
    re.IGNORECASE,
)


class OutputGuard:
    """Agent 输出质量守卫

    在 agentStop 事件时检查 Agent 回复质量，
    发现问题时记录到度量系统并可选地触发修正。
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._max_simple_reply_chars = cfg.get("max_simple_reply_chars", 500)
        self._max_code_blocks_for_chat = cfg.get("max_code_blocks_for_chat", 1)
        self._enabled = cfg.get("enabled", True)

    def check(self, user_input: str, agent_reply: str) -> list[OutputIssue]:
        """检查 Agent 回复质量，返回问题列表

        检查顺序：URL 幻觉 → 包名幻觉 → 过度输出 → 代码幻觉
        每种检查独立运行，互不影响。
        """
        if not self._enabled or not agent_reply:
            return []

        issues: list[OutputIssue] = []

        issues.extend(self._check_hallucinated_urls(agent_reply))
        issues.extend(self._check_fake_packages(agent_reply))
        issues.extend(self._check_verbose_reply(user_input, agent_reply))
        issues.extend(self._check_code_hallucination(user_input, agent_reply))

        return issues

    def build_correction_prompt(self, user_input: str, issues: list[OutputIssue]) -> str | None:
        """基于检测到的问题生成修正提示，注入到 Agent memory 的下一轮对话

        只对 error 和 warn 级别的问题生成修正提示。
        info 级别的问题只记录不修正，避免过度干预。
        返回 None 表示无需修正。
        """
        errors = [i for i in issues if i.severity == "error"]
        warns = [i for i in issues if i.severity == "warn"]

        if not errors and not warns:
            return None

        parts = ["[输出质量检查发现以下问题，请在后续回复中注意]"]
        for issue in errors + warns:
            parts.append("- {}: {}".format(issue.type, issue.message))
            if issue.suggestion:
                parts.append("  建议: {}".format(issue.suggestion))

        return "\n".join(parts)

    # ── 检测方法 ─────────────────────────────────────────────

    def _check_hallucinated_urls(self, reply: str) -> list[OutputIssue]:
        """检测编造的 URL"""
        matches = _HALLUCINATED_URLS.findall(reply)
        if matches:
            return [OutputIssue(
                type="hallucination",
                severity="error",
                message="回复中包含编造的 URL: {}".format(", ".join(matches[:3])),
                suggestion="不要编造不存在的网址。如果需要示例 URL，使用 example.com",
            )]
        return []

    def _check_fake_packages(self, reply: str) -> list[OutputIssue]:
        """检测编造的包名"""
        matches = _FAKE_PACKAGES.findall(reply)
        if matches:
            return [OutputIssue(
                type="hallucination",
                severity="warn",
                message="回复中可能包含编造的包名",
                suggestion="确保引用的库名真实存在，不要编造 import 语句",
            )]
        return []

    def _check_verbose_reply(self, user_input: str, reply: str) -> list[OutputIssue]:
        """检测简单问题的过长回复"""
        if not _SIMPLE_QUESTION.match(user_input.strip()):
            return []

        if len(reply) > self._max_simple_reply_chars:
            return [OutputIssue(
                type="verbose",
                severity="warn",
                message="简单问题的回复过长 ({} 字符，建议 < {})".format(
                    len(reply), self._max_simple_reply_chars),
                suggestion="对简单问候/身份问题，用 2-3 句话简洁回答即可",
            )]
        return []

    def _check_code_hallucination(self, user_input: str, reply: str) -> list[OutputIssue]:
        """检测非代码问题中编造代码"""
        # 如果用户问的是代码相关问题，允许代码回复
        code_keywords = re.compile(
            r'代码|编程|函数|脚本|实现|debug|bug|写一个|code|implement|function|script',
            re.IGNORECASE,
        )
        if code_keywords.search(user_input):
            return []

        # 简单问题中出现多个代码块 = 代码幻觉
        if _SIMPLE_QUESTION.match(user_input.strip()) or _FACTUAL_QUESTION.match(user_input.strip()):
            code_blocks = _CODE_BLOCK.findall(reply)
            if len(code_blocks) > self._max_code_blocks_for_chat:
                return [OutputIssue(
                    type="code_hallucination",
                    severity="error",
                    message="非代码问题中编造了 {} 个代码块".format(len(code_blocks)),
                    suggestion="用户没有要求代码，不要编造代码示例。用自然语言回答即可",
                )]
        return []
