"""会话持久化 — JSONL 格式存储 + 压缩归档

功能:
- 自动保存/加载对话历史
- JSONL 格式（每行一条消息，追加友好）
- 会话压缩（旧轮次摘要化）
- 多会话管理（按时间戳命名）
- 自动清理: 会话文件超过上限时淘汰最旧的
"""
from __future__ import annotations
import os, json, time, glob
from dataclasses import dataclass

# 最多保留的会话文件数
_MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "50"))
# 会话文件最大保留天数（超过的自动删除）
_SESSION_MAX_AGE_DAYS = int(os.environ.get("SESSION_MAX_AGE_DAYS", "30"))


@dataclass
class SessionMeta:
    """会话元数据"""
    session_id: str
    created_at: float
    updated_at: float
    turns: int
    provider: str
    summary: str

    def display(self) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.updated_at))
        return "{} | {}轮 | {} | {}".format(self.session_id[:12], self.turns, ts, self.summary[:40])


class SessionStore:
    """JSONL 会话持久化"""

    def __init__(self, base_dir: str | None = None):
        self._base_dir = base_dir or os.path.expanduser("~/.nexus-agent/sessions")
        os.makedirs(self._base_dir, exist_ok=True)

    def save(self, session_id: str, memory: list[dict], provider: str = "",
             summary: str = "") -> str:
        """保存会话到 JSONL 文件"""
        path = os.path.join(self._base_dir, "{}.jsonl".format(session_id))
        meta = {
            "_meta": True,
            "session_id": session_id,
            "created_at": time.time(),
            "updated_at": time.time(),
            "turns": sum(1 for m in memory if m.get("role") == "user"),
            "provider": provider,
            "summary": summary,
        }
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for msg in memory:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        # 自动清理旧会话
        self._auto_cleanup()
        return path

    def _auto_cleanup(self):
        """自动清理旧会话文件

        策略:
        1. 超过 SESSION_MAX_AGE_DAYS 天的文件直接删除
        2. 文件数超过 MAX_SESSIONS 时，按修改时间淘汰最旧的
        """
        try:
            files = glob.glob(os.path.join(self._base_dir, "*.jsonl"))
            if len(files) <= _MAX_SESSIONS:
                # 数量未超限，只检查过期
                cutoff = time.time() - _SESSION_MAX_AGE_DAYS * 86400
                for f in files:
                    try:
                        if os.path.getmtime(f) < cutoff:
                            os.remove(f)
                    except Exception:
                        pass
                return

            # 数量超限: 按修改时间排序，删除最旧的
            files_with_mtime = []
            for f in files:
                try:
                    files_with_mtime.append((f, os.path.getmtime(f)))
                except Exception:
                    pass
            files_with_mtime.sort(key=lambda x: x[1])

            # 删除超出部分
            excess = len(files_with_mtime) - _MAX_SESSIONS
            for f, _ in files_with_mtime[:excess]:
                try:
                    os.remove(f)
                except Exception:
                    pass
        except Exception:
            pass

    def load(self, session_id: str) -> tuple[list[dict], SessionMeta | None]:
        """加载会话，返回 (memory, meta)"""
        path = os.path.join(self._base_dir, "{}.jsonl".format(session_id))
        if not os.path.exists(path):
            return [], None
        memory = []
        meta_dict = None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("_meta"):
                    meta_dict = obj
                else:
                    memory.append(obj)
        meta = None
        if meta_dict:
            meta = SessionMeta(
                session_id=meta_dict.get("session_id", session_id),
                created_at=meta_dict.get("created_at", 0),
                updated_at=meta_dict.get("updated_at", 0),
                turns=meta_dict.get("turns", 0),
                provider=meta_dict.get("provider", ""),
                summary=meta_dict.get("summary", ""),
            )
        return memory, meta

    def list_sessions(self, limit: int = 20) -> list[SessionMeta]:
        """列出最近的会话"""
        sessions = []
        for path in sorted(glob.glob(os.path.join(self._base_dir, "*.jsonl")), reverse=True):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if not first_line:
                        continue
                    obj = json.loads(first_line)
                    if obj.get("_meta"):
                        sessions.append(SessionMeta(
                            session_id=obj.get("session_id", os.path.basename(path)[:-6]),
                            created_at=obj.get("created_at", 0),
                            updated_at=obj.get("updated_at", 0),
                            turns=obj.get("turns", 0),
                            provider=obj.get("provider", ""),
                            summary=obj.get("summary", ""),
                        ))
            except Exception:
                continue
            if len(sessions) >= limit:
                break
        return sessions

    def delete(self, session_id: str) -> bool:
        """删除会话"""
        path = os.path.join(self._base_dir, "{}.jsonl".format(session_id))
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def compact(self, session_id: str, summarize_fn=None) -> bool:
        """压缩会话 — 将旧轮次摘要化，保留最近 N 轮"""
        memory, meta = self.load(session_id)
        if not memory or len(memory) < 10:
            return False

        # 保留 system + 最近 4 轮用户消息
        system = memory[0] if memory and memory[0].get("role") == "system" else None
        rest = memory[1:] if system else memory[:]
        user_indices = [i for i, m in enumerate(rest) if m["role"] == "user"]
        if len(user_indices) <= 4:
            return False

        split_at = user_indices[-4]
        old_part = rest[:split_at]
        new_part = rest[split_at:]

        # 生成摘要
        if summarize_fn:
            lines = []
            for m in old_part:
                if m["role"] == "user":
                    lines.append("用户: {}".format((m.get("content") or "")[:80]))
                elif m["role"] == "assistant":
                    lines.append("助手: {}".format((m.get("content") or "")[:80]))
            summary_text = summarize_fn("\n".join(lines[-10:]))
        else:
            exchanges = []
            q = None
            for m in old_part:
                if m["role"] == "user":
                    q = (m.get("content") or "")[:50]
                elif m["role"] == "assistant" and q:
                    a = (m.get("content") or "").strip().split("\n")[0][:60]
                    exchanges.append("Q:{} → A:{}".format(q, a))
                    q = None
            summary_text = "\n".join(exchanges[-5:])

        compacted = []
        if system:
            compacted.append(system)
        if summary_text:
            compacted.append({"role": "system", "content": "[历史摘要]\n{}".format(summary_text)})
        compacted.extend(new_part)

        self.save(
            session_id, compacted,
            provider=meta.provider if meta else "",
            summary=meta.summary if meta else "",
        )
        return True

    @staticmethod
    def generate_id() -> str:
        """生成会话 ID"""
        return time.strftime("%Y%m%d_%H%M%S")
