"""SessionMixin — 会话持久化职责

从 Agent 类拆分出来的会话管理功能。
通过 Mixin 模式组合到 Agent 类中（详见 docs/ARCHITECTURE.md §3.1）。

管理会话的保存、加载、列表、压缩和自动持久化。
三层记忆的 L3（磁盘层）在 _auto_persist() 中实现。
自动压缩: memory 超过阈值时自动触发 in-place 压缩。
"""
from __future__ import annotations
import os
from agent_core.session_store import SessionStore
from agent_core.token_optimizer import count_messages_tokens

# memory 消息数超过此值时触发自动压缩
_AUTO_COMPACT_THRESHOLD = int(os.environ.get("AUTO_COMPACT_THRESHOLD", "40"))
# 压缩后保留的最近用户轮数
_COMPACT_KEEP_TURNS = int(os.environ.get("COMPACT_KEEP_TURNS", "6"))


class SessionMixin:
    """会话持久化 Mixin

    要求宿主类提供:
    - self.memory: list[dict]
    - self._provider: str
    - self._llm_summarize(text: str) -> str
    """

    def _init_session(self):
        """在宿主 __init__ 中调用"""
        self._session_store = SessionStore()
        self._session_id: str | None = None

    def save_session(self, session_id: str | None = None) -> str:
        """保存当前会话"""
        sid = session_id or self._session_id or SessionStore.generate_id()
        self._session_id = sid
        summary = ""
        for m in self.memory:
            if m.get("role") == "user" and m.get("content"):
                summary = m["content"][:60]
                break
        self._session_store.save(sid, self.memory, provider=self._provider, summary=summary)
        return sid

    def load_session(self, session_id: str) -> bool:
        """加载会话"""
        memory, meta = self._session_store.load(session_id)
        if not memory:
            return False
        self.memory = memory
        self._session_id = session_id
        if meta and meta.provider:
            self._provider = meta.provider
        # 同步切换记忆索引
        memory_index = getattr(self, '_memory_index', None)
        if memory_index:
            memory_index.switch_session(session_id)
        return True

    def list_sessions(self, limit: int = 20) -> list:
        """列出最近的会话"""
        return self._session_store.list_sessions(limit)

    def compact_session(self) -> bool:
        """压缩当前会话"""
        if not self._session_id:
            return False
        return self._session_store.compact(self._session_id, summarize_fn=self._llm_summarize)

    def _auto_persist(self):
        """三层记忆 L3: 自动持久化到磁盘

        增强: memory 超过阈值时自动触发 in-place 压缩，
        防止长时间使用后 memory 无限膨胀。
        """
        # 确保 session_id 存在（压缩时需要用它来隔离记忆索引）
        if not self._session_id:
            self._session_id = SessionStore.generate_id()
            memory_index = getattr(self, '_memory_index', None)
            if memory_index:
                memory_index.switch_session(self._session_id)

        # 自动压缩: memory 过长时先瘦身再持久化
        if len(self.memory) > _AUTO_COMPACT_THRESHOLD:
            self._auto_compact_memory()

        try:
            summary = ""
            for m in self.memory:
                if m.get("role") == "user" and m.get("content"):
                    summary = m["content"][:60]
                    break
            self._session_store.save(
                self._session_id, self.memory,
                provider=self._provider, summary=summary,
            )
        except Exception:
            pass

    def _auto_compact_memory(self):
        """自动压缩 memory — 将旧消息摘要化，原地替换

        触发条件: memory 消息数 > AUTO_COMPACT_THRESHOLD (默认 40)
        保留策略:
        1. system prompt 始终保留
        2. 最近 COMPACT_KEEP_TURNS 轮用户对话完整保留
        3. 更早的消息 → 提取重要信息 + 生成摘要 → 替换为一条 system 消息
        4. 上下文卫生系统的毒性标记同步重置（索引已变）
        """
        import sys

        system = self.memory[0] if self.memory and self.memory[0].get("role") == "system" else None
        rest = self.memory[1:] if system else self.memory[:]

        user_indices = [i for i, m in enumerate(rest) if m["role"] == "user"]
        if len(user_indices) <= _COMPACT_KEEP_TURNS:
            return  # 不够压缩

        split_at = user_indices[-_COMPACT_KEEP_TURNS]
        old_part = rest[:split_at]
        new_part = rest[split_at:]

        # 提取重要信息（偏好、决策、文件路径）
        from agent_core.token_optimizer import _extract_important_info
        important = _extract_important_info(old_part)

        # 生成摘要
        try:
            summary_text = self._llm_summarize(
                "\n".join(
                    "{}: {}".format(m.get("role", ""), (m.get("content") or "")[:100])
                    for m in old_part if m.get("content")
                )[-2000:]  # 限制输入长度
            )
        except Exception:
            # LLM 摘要失败时用本地摘要
            exchanges = []
            q = None
            for m in old_part:
                if m["role"] == "user":
                    q = (m.get("content") or "")[:50]
                elif m["role"] == "assistant" and q:
                    a = (m.get("content") or "").strip().split("\n")[0][:60]
                    exchanges.append("Q:{} → A:{}".format(q, a))
                    q = None
            summary_text = "\n".join(exchanges[-8:])

        # 组装压缩后的摘要消息
        compact_content = "[历史摘要 — 已压缩 {} 条消息]\n{}".format(len(old_part), summary_text)
        if important:
            compact_content += "\n\n[重要信息]\n" + important

        # 将被压缩的消息存入记忆索引（可检索，不丢失）
        memory_index = getattr(self, '_memory_index', None)
        if memory_index:
            # 获取重要性评分（如果有卫生系统）
            hygiene = getattr(self, '_hygiene', None)
            scores = None
            if hygiene:
                # old_part 的索引偏移: system(1) + old_part 起始位置
                offset = 1 if system else 0
                all_scores = hygiene.score_messages(self.memory)
                scores = all_scores[offset:offset + len(old_part)]
            memory_index.archive(old_part, importance_scores=scores)

        # 原地替换 memory
        compacted = []
        if system:
            compacted.append(system)
        compacted.append({"role": "system", "content": compact_content})
        compacted.extend(new_part)

        old_len = len(self.memory)
        self.memory[:] = compacted

        # 重置上下文卫生系统的索引（压缩后索引全变了）
        hygiene = getattr(self, '_hygiene', None)
        if hygiene:
            hygiene.detox.clear()

        try:
            sys.stderr.write("    ↻ 自动压缩: {} → {} 条消息 (已归档到记忆索引)\n".format(
                old_len, len(self.memory)))
            sys.stderr.flush()
        except Exception:
            pass
