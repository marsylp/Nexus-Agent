"""MemoryIndex — 可检索的历史记忆索引

核心原则: 上下文不是用来"思考"的，是用来"检索"和"总结"的。

被压缩掉的历史消息不是丢掉，而是存入 MemoryIndex。
当用户提到"之前""上次""那个方案"等回溯信号时，
自动从索引中检索相关内容，按需注入上下文。

架构:
- 存储层: 每条消息按 (turn_id, role, content, keywords) 存入索引
- 检索层: 基于关键词匹配 + 时间衰减的轻量检索
- 注入层: 检索结果作为 [历史参考] 注入 build_context

不依赖向量数据库，纯关键词检索，零外部依赖。
"""
from __future__ import annotations
import re, time, os, json
from dataclasses import dataclass, field


@dataclass
class MemoryEntry:
    """索引中的一条记忆"""
    turn_id: int            # 第几轮对话
    role: str               # user / assistant / tool
    content: str            # 原始内容（完整保留）
    keywords: set[str]      # 提取的关键词
    timestamp: float = 0.0  # 存入时间
    importance: float = 0.5 # 重要性评分


class MemoryIndex:
    """可检索的历史记忆索引

    生命周期:
    1. 自动压缩时 → archive() 将被压缩的消息存入索引
    2. 用户输入时 → retrieve() 检索相关历史
    3. build_context 时 → 检索结果作为 [历史参考] 注入

    会话隔离: 每个会话有独立的索引文件，切换会话时自动切换。
    存储上限: 每个会话最多保留 200 条记忆，超出时淘汰最旧且最不重要的。
    """

    _MAX_ENTRIES = int(os.environ.get("MEMORY_INDEX_MAX", "200"))
    _BASE_DIR = os.path.expanduser("~/.nexus-agent/memory_index")
    # 兼容旧版单文件路径
    _LEGACY_PATH = os.path.expanduser("~/.nexus-agent/memory_index.json")
    # 索引目录最多保留的会话索引文件数
    _MAX_INDEX_FILES = int(os.environ.get("MAX_INDEX_FILES", "30"))

    # 回溯信号 — 用户想引用历史信息的模式
    _RECALL_PATTERNS = [
        re.compile(r"之前|上次|刚才|前面|earlier|before|previous|last time", re.IGNORECASE),
        re.compile(r"那个方案|那个问题|那个错误|那个文件|那个命令", re.IGNORECASE),
        re.compile(r"我们讨论过|我说过|你说过|提到过|mentioned|discussed", re.IGNORECASE),
        re.compile(r"回顾|回忆|recall|remind me|还记得", re.IGNORECASE),
    ]

    def __init__(self, persist: bool = True, session_id: str | None = None):
        self._entries: list[MemoryEntry] = []
        self._persist = persist
        self._turn_counter = 0
        self._session_id = session_id or "_default"
        if persist:
            self._migrate_legacy()
            self._load()

    def switch_session(self, session_id: str):
        """切换到另一个会话的索引"""
        if session_id == self._session_id:
            return
        # 先保存当前会话
        if self._persist:
            self._save()
        # 切换并加载
        self._entries.clear()
        self._turn_counter = 0
        self._session_id = session_id
        if self._persist:
            self._load()

    @property
    def _persist_path(self) -> str:
        """当前会话的索引文件路径"""
        return os.path.join(self._BASE_DIR, "{}.json".format(self._session_id))

    # ── 存入 ─────────────────────────────────────────────

    def archive(self, messages: list[dict], importance_scores: list[float] | None = None):
        """将消息批量存入索引（通常在自动压缩时调用）

        参数:
        - messages: 被压缩掉的消息列表
        - importance_scores: 每条消息的重要性评分（来自 ContextHygiene）
        """
        self._turn_counter += 1
        now = time.time()

        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content or not isinstance(content, str):
                continue
            if role == "system":
                continue  # system prompt 不需要索引

            keywords = self._extract_keywords(content)
            if not keywords:
                continue  # 没有有意义的关键词，跳过

            score = importance_scores[i] if importance_scores and i < len(importance_scores) else 0.5

            self._entries.append(MemoryEntry(
                turn_id=self._turn_counter,
                role=role,
                content=content[:500],  # 限制单条长度
                keywords=keywords,
                timestamp=now,
                importance=score,
            ))

        # 超出上限时淘汰
        self._evict()
        if self._persist:
            self._save()

    def archive_single(self, role: str, content: str, importance: float = 0.5):
        """存入单条记忆"""
        if not content or not isinstance(content, str):
            return
        keywords = self._extract_keywords(content)
        if not keywords:
            return
        self._entries.append(MemoryEntry(
            turn_id=self._turn_counter,
            role=role,
            content=content[:500],
            keywords=keywords,
            timestamp=time.time(),
            importance=importance,
        ))
        self._evict()

    # ── 检索 ─────────────────────────────────────────────

    def needs_retrieval(self, user_input: str) -> bool:
        """检测用户输入是否包含回溯信号"""
        for pat in self._RECALL_PATTERNS:
            if pat.search(user_input):
                return True
        return False

    def retrieve(self, query: str, top_k: int = 3) -> list[dict]:
        """基于关键词匹配 + 时间衰减检索相关记忆

        返回格式:
        [{"role": "user", "content": "...", "relevance": 0.8, "turn_id": 5}, ...]
        """
        if not self._entries:
            return []

        query_keywords = self._extract_keywords(query)
        if not query_keywords:
            # 没有明确关键词时，返回最近的重要记忆
            sorted_entries = sorted(
                self._entries,
                key=lambda e: e.importance * 0.7 + (e.timestamp / time.time()) * 0.3,
                reverse=True,
            )
            return [self._entry_to_dict(e, 0.3) for e in sorted_entries[:top_k]]

        # 计算每条记忆的相关性分数
        scored = []
        now = time.time()
        for entry in self._entries:
            # 关键词重叠度 (0~1)
            overlap = len(query_keywords & entry.keywords)
            if overlap == 0:
                continue
            keyword_score = overlap / max(len(query_keywords), 1)

            # 时间衰减 (越新越高，半衰期 1 小时)
            age_hours = (now - entry.timestamp) / 3600
            time_score = 0.5 ** (age_hours / 1.0)  # 1 小时半衰期

            # 综合分数
            relevance = keyword_score * 0.6 + time_score * 0.2 + entry.importance * 0.2
            scored.append((entry, relevance))

        # 按相关性排序
        scored.sort(key=lambda x: x[1], reverse=True)
        return [self._entry_to_dict(e, r) for e, r in scored[:top_k]]

    def retrieve_as_prompt(self, query: str, top_k: int = 3) -> str | None:
        """检索并格式化为可注入上下文的文本

        返回 None 表示没有相关历史。
        """
        results = self.retrieve(query, top_k)
        if not results:
            return None

        lines = ["[历史参考 — 从之前的对话中检索]"]
        for r in results:
            role_label = "用户" if r["role"] == "user" else "助手" if r["role"] == "assistant" else "工具"
            # 截断过长内容
            content = r["content"]
            if len(content) > 200:
                content = content[:200] + "..."
            lines.append("- [{}] {}".format(role_label, content))

        return "\n".join(lines)

    # ── 关键词提取 ───────────────────────────────────────

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        """从文本中提取关键词（轻量级，无外部依赖）

        提取规则:
        - 中文: 2-4 字的词组（bigram/trigram/4-gram）
        - 英文: 完整单词（3 字母以上）
        - 技术术语: 文件路径、命令、包名等
        - 过滤停用词
        """
        keywords = set()

        # 英文词（3 字母以上）
        en_words = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{2,}', text.lower())
        # 过滤常见停用词
        _STOP_WORDS = {
            "the", "and", "for", "that", "this", "with", "from", "are", "was",
            "were", "been", "have", "has", "had", "not", "but", "can", "will",
            "would", "could", "should", "may", "might", "shall", "its", "you",
            "your", "they", "them", "their", "what", "which", "who", "how",
            "when", "where", "why", "all", "each", "every", "both", "few",
            "more", "most", "other", "some", "such", "than", "too", "very",
            "just", "about", "above", "after", "again", "also", "any",
            "content", "role", "user", "assistant", "system", "tool",
        }
        keywords.update(w for w in en_words if w not in _STOP_WORDS)

        # 中文字符
        cn_chars = re.findall(r'[\u4e00-\u9fff]', text)
        # bigram
        for i in range(len(cn_chars) - 1):
            bigram = cn_chars[i] + cn_chars[i + 1]
            keywords.add(bigram)

        # 文件路径
        paths = re.findall(r'[\w./\\-]+\.(?:py|js|ts|json|yaml|yml|toml|md|vue|css|html)', text)
        keywords.update(paths)

        # 技术命令
        commands = re.findall(r'(?:npm|pip|git|docker|kubectl|curl|wget)\s+\w+', text.lower())
        keywords.update(cmd.replace(" ", "_") for cmd in commands)

        return keywords

    # ── 内部方法 ─────────────────────────────────────────

    def _entry_to_dict(self, entry: MemoryEntry, relevance: float) -> dict:
        return {
            "role": entry.role,
            "content": entry.content,
            "relevance": round(relevance, 3),
            "turn_id": entry.turn_id,
        }

    def _evict(self):
        """淘汰策略: 超出上限时，删除最旧且最不重要的"""
        if len(self._entries) <= self._MAX_ENTRIES:
            return
        # 按 (importance, timestamp) 排序，淘汰最低的
        self._entries.sort(key=lambda e: (e.importance, e.timestamp))
        excess = len(self._entries) - self._MAX_ENTRIES
        self._entries = self._entries[excess:]

    def _save(self):
        """持久化到磁盘（按会话隔离）"""
        try:
            os.makedirs(self._BASE_DIR, exist_ok=True)
            data = [
                {
                    "turn_id": e.turn_id,
                    "role": e.role,
                    "content": e.content,
                    "keywords": list(e.keywords),
                    "timestamp": e.timestamp,
                    "importance": e.importance,
                }
                for e in self._entries
            ]
            with open(self._persist_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            # 清理旧索引文件
            self._cleanup_old_index_files()
        except Exception:
            pass

    def _load(self):
        """从磁盘加载当前会话的索引"""
        try:
            if os.path.exists(self._persist_path):
                with open(self._persist_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    self._entries.append(MemoryEntry(
                        turn_id=item.get("turn_id", 0),
                        role=item.get("role", ""),
                        content=item.get("content", ""),
                        keywords=set(item.get("keywords", [])),
                        timestamp=item.get("timestamp", 0),
                        importance=item.get("importance", 0.5),
                    ))
                if self._entries:
                    self._turn_counter = max(e.turn_id for e in self._entries)
        except Exception:
            pass

    def _migrate_legacy(self):
        """迁移旧版单文件索引到按会话隔离的目录"""
        if not os.path.exists(self._LEGACY_PATH):
            return
        try:
            os.makedirs(self._BASE_DIR, exist_ok=True)
            dest = os.path.join(self._BASE_DIR, "_default.json")
            if not os.path.exists(dest):
                os.rename(self._LEGACY_PATH, dest)
            else:
                os.remove(self._LEGACY_PATH)
        except Exception:
            pass

    def _cleanup_old_index_files(self):
        """清理旧的索引文件，保留最近 MAX_INDEX_FILES 个"""
        try:
            import glob
            files = glob.glob(os.path.join(self._BASE_DIR, "*.json"))
            if len(files) <= self._MAX_INDEX_FILES:
                return
            files_with_mtime = []
            for f in files:
                try:
                    files_with_mtime.append((f, os.path.getmtime(f)))
                except Exception:
                    pass
            files_with_mtime.sort(key=lambda x: x[1])
            excess = len(files_with_mtime) - self._MAX_INDEX_FILES
            for f, _ in files_with_mtime[:excess]:
                try:
                    os.remove(f)
                except Exception:
                    pass
        except Exception:
            pass

    def clear(self):
        """清空索引"""
        self._entries.clear()
        self._turn_counter = 0

    def stats(self) -> dict:
        """索引统计"""
        return {
            "total_entries": len(self._entries),
            "max_entries": self._MAX_ENTRIES,
            "turns_archived": self._turn_counter,
            "oldest": time.strftime(
                "%Y-%m-%d %H:%M",
                time.localtime(min(e.timestamp for e in self._entries))
            ) if self._entries else "N/A",
        }
