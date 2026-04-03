"""MemoryIndex 测试 — 验证可检索的历史记忆"""
import pytest
from agent_core.memory_index import MemoryIndex


class TestMemoryIndex:
    def _make_index(self):
        """创建不持久化的测试索引"""
        return MemoryIndex(persist=False)

    def test_archive_and_retrieve(self):
        """存入后应能检索到"""
        idx = self._make_index()
        messages = [
            {"role": "user", "content": "帮我用 Python 写一个排序算法"},
            {"role": "assistant", "content": "好的，这是一个快速排序的实现..."},
        ]
        idx.archive(messages)
        results = idx.retrieve("Python 排序")
        assert len(results) > 0
        assert any("Python" in r["content"] or "排序" in r["content"] for r in results)

    def test_needs_retrieval(self):
        """回溯信号应被检测到"""
        idx = self._make_index()
        assert idx.needs_retrieval("之前那个方案是什么")
        assert idx.needs_retrieval("上次我们讨论过的问题")
        assert idx.needs_retrieval("remind me what we discussed")
        assert not idx.needs_retrieval("帮我写一个函数")
        assert not idx.needs_retrieval("今天天气怎么样")

    def test_retrieve_empty(self):
        """空索引应返回空结果"""
        idx = self._make_index()
        results = idx.retrieve("任何查询")
        assert results == []

    def test_retrieve_as_prompt(self):
        """检索结果应格式化为可注入的文本"""
        idx = self._make_index()
        idx.archive([
            {"role": "user", "content": "选择方案 A 来实现用户登录"},
            {"role": "assistant", "content": "好的，方案 A 使用 JWT 认证"},
        ])
        prompt = idx.retrieve_as_prompt("之前选的方案")
        assert prompt is not None
        assert "历史参考" in prompt
        assert "方案" in prompt

    def test_keyword_extraction(self):
        """关键词提取应覆盖中英文和技术术语"""
        keywords = MemoryIndex._extract_keywords(
            "使用 Python 的 flask 框架，文件在 src/app.py"
        )
        assert "python" in keywords
        assert "flask" in keywords
        assert "src/app.py" in keywords

    def test_eviction(self):
        """超出上限时应淘汰旧条目"""
        idx = self._make_index()
        idx._MAX_ENTRIES = 5  # 临时降低上限
        for i in range(10):
            idx.archive([{"role": "user", "content": f"消息 {i} 关于话题 {i}"}])
        assert len(idx._entries) <= 5

    def test_importance_affects_eviction(self):
        """高重要性的条目应在淘汰时被保留"""
        idx = self._make_index()
        idx._MAX_ENTRIES = 3

        # 先存入低重要性的
        idx.archive(
            [{"role": "user", "content": "不重要的闲聊内容"}],
            importance_scores=[0.1],
        )
        idx.archive(
            [{"role": "user", "content": "不重要的闲聊内容二"}],
            importance_scores=[0.1],
        )
        idx.archive(
            [{"role": "user", "content": "不重要的闲聊内容三"}],
            importance_scores=[0.1],
        )
        # 再存入高重要性的
        idx.archive(
            [{"role": "user", "content": "关键决策：选择 PostgreSQL 数据库"}],
            importance_scores=[0.9],
        )

        # 高重要性的应该被保留
        results = idx.retrieve("PostgreSQL 数据库")
        assert len(results) > 0

    def test_system_messages_skipped(self):
        """system 消息不应被索引"""
        idx = self._make_index()
        idx.archive([
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "你好"},
        ])
        # 只有 user 消息被索引
        assert len(idx._entries) == 1
        assert idx._entries[0].role == "user"

    def test_stats(self):
        """统计信息应正确"""
        idx = self._make_index()
        idx.archive([
            {"role": "user", "content": "问题一"},
            {"role": "assistant", "content": "回答一"},
        ])
        stats = idx.stats()
        assert stats["total_entries"] == 2
        assert stats["turns_archived"] == 1

    def test_clear(self):
        """清空应重置所有状态"""
        idx = self._make_index()
        idx.archive([{"role": "user", "content": "测试内容"}])
        assert len(idx._entries) > 0
        idx.clear()
        assert len(idx._entries) == 0
        assert idx._turn_counter == 0


class TestMemoryIndexIntegration:
    """集成测试 — 验证 MemoryIndex 与 Agent 的协作"""

    def test_compact_archives_to_index(self):
        """自动压缩时应将旧消息存入索引"""
        from agent_core.agent import Agent
        import agent_core.mixins.session_mixin as sm

        old_threshold = sm._AUTO_COMPACT_THRESHOLD
        sm._AUTO_COMPACT_THRESHOLD = 10
        try:
            agent = Agent(stream=False)
            # 填充消息
            for i in range(15):
                agent.memory.append({"role": "user", "content": f"讨论话题 {i} 关于 Python"})
                agent.memory.append({"role": "assistant", "content": f"关于话题 {i} 的回答"})

            agent._auto_persist()

            # 索引中应该有被压缩的消息
            assert len(agent._memory_index._entries) > 0
            # 应该能检索到
            results = agent._memory_index.retrieve("Python 话题")
            assert len(results) > 0
        finally:
            sm._AUTO_COMPACT_THRESHOLD = old_threshold

    def test_recall_retrieves_from_index(self):
        """用户引用历史时应能从索引中检索"""
        from agent_core.memory_index import MemoryIndex

        idx = MemoryIndex(persist=False)
        idx.archive([
            {"role": "user", "content": "我们决定使用 React 框架"},
            {"role": "assistant", "content": "好的，React 是个好选择"},
        ])

        # 模拟用户回溯
        assert idx.needs_retrieval("之前我们选的什么框架")
        prompt = idx.retrieve_as_prompt("之前选的框架 React")
        assert prompt is not None
        assert "React" in prompt


class TestMemoryIndexSessionIsolation:
    """会话隔离测试"""

    def test_switch_session(self):
        """切换会话后应加载不同的索引"""
        idx = MemoryIndex(persist=False)
        idx.archive([{"role": "user", "content": "会话 A 的内容关于 Python"}])

        idx.switch_session("session_b")
        assert len(idx._entries) == 0  # 新会话应该是空的

        idx.archive([{"role": "user", "content": "会话 B 的内容关于 Java"}])

        # 切回会话 A（非持久化模式下数据已丢失，这是预期行为）
        idx.switch_session("_default")
        # 非持久化模式下切换会清空，这里验证的是隔离机制
        assert len(idx._entries) == 0

    def test_session_id_in_path(self):
        """不同会话应使用不同的文件路径"""
        idx = MemoryIndex(persist=False, session_id="test_session_1")
        assert "test_session_1" in idx._persist_path

        idx.switch_session("test_session_2")
        assert "test_session_2" in idx._persist_path


class TestSessionStoreCleanup:
    """SessionStore 自动清理测试"""

    def test_auto_cleanup_by_count(self, tmp_path):
        """超过上限时应删除最旧的文件"""
        import agent_core.session_store as ss
        old_max = ss._MAX_SESSIONS
        ss._MAX_SESSIONS = 3
        try:
            store = ss.SessionStore(base_dir=str(tmp_path))
            # 创建 5 个会话
            for i in range(5):
                store.save(f"session_{i}", [{"role": "user", "content": f"msg {i}"}])
                import time
                time.sleep(0.05)  # 确保 mtime 不同

            # 应该只保留 3 个
            import glob
            files = glob.glob(str(tmp_path / "*.jsonl"))
            assert len(files) <= 3
        finally:
            ss._MAX_SESSIONS = old_max

    def test_auto_cleanup_by_age(self, tmp_path):
        """超过保留天数的文件应被删除"""
        import time as _time
        import agent_core.session_store as ss
        old_age = ss._SESSION_MAX_AGE_DAYS
        # 先用正常天数创建文件
        ss._SESSION_MAX_AGE_DAYS = 30
        try:
            store = ss.SessionStore(base_dir=str(tmp_path))
            store.save("old_session", [{"role": "user", "content": "old"}])

            # 手动把文件时间改到过去
            import os
            path = str(tmp_path / "old_session.jsonl")
            assert os.path.exists(path)
            old_time = _time.time() - 86400 * 2  # 2 天前
            os.utime(path, (old_time, old_time))

            # 现在设置为 1 天过期，再保存一个新的触发清理
            ss._SESSION_MAX_AGE_DAYS = 1
            store.save("new_session", [{"role": "user", "content": "new"}])

            assert not os.path.exists(path)  # 旧文件应被删除
        finally:
            ss._SESSION_MAX_AGE_DAYS = old_age
