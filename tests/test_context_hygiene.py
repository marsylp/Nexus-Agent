"""上下文卫生系统测试 — 验证四大痛点的解决方案"""
import pytest
from agent_core.context_hygiene import (
    MemoryDetox, FocusManager, ToolBudget, ConflictDetector, ContextHygiene,
)


# ═══════════════════════════════════════════════════════════
# 痛点一: 上下文中毒 — MemoryDetox
# ═══════════════════════════════════════════════════════════

class TestMemoryDetox:
    def test_detect_tool_failure(self):
        """工具执行失败的结果应被标记为有毒"""
        detox = MemoryDetox()
        memory = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "查天气"},
            {"role": "tool", "content": "[错误] 网络超时"},
        ]
        markers = detox.scan(memory)
        assert len(markers) == 1
        assert markers[0].reason == "工具执行失败"
        assert markers[0].severity == "medium"

    def test_detect_user_correction(self):
        """被用户纠正的 assistant 回复应被标记"""
        detox = MemoryDetox()
        memory = [
            {"role": "system", "content": "你是助手"},
            {"role": "assistant", "content": "Python 是编译型语言"},
            {"role": "user", "content": "不对，Python 是解释型语言"},
        ]
        markers = detox.scan(memory)
        assert len(markers) == 1
        assert markers[0].reason == "被用户纠正"
        assert markers[0].severity == "high"

    def test_no_false_positive(self):
        """正常对话不应被标记"""
        detox = MemoryDetox()
        memory = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮你的？"},
        ]
        markers = detox.scan(memory)
        assert len(markers) == 0

    def test_traceback_detection(self):
        """包含 Traceback 的工具结果应被标记"""
        detox = MemoryDetox()
        memory = [
            {"role": "tool", "content": "Traceback (most recent call last):\n  File..."},
        ]
        markers = detox.scan(memory)
        assert len(markers) == 1

    def test_scan_idempotent(self):
        """重复扫描不应产生重复标记"""
        detox = MemoryDetox()
        memory = [
            {"role": "tool", "content": "[错误] 失败了"},
        ]
        detox.scan(memory)
        markers2 = detox.scan(memory)
        assert len(markers2) == 0  # 第二次不应有新标记


# ═══════════════════════════════════════════════════════════
# 痛点二: 上下文分心 — FocusManager
# ═══════════════════════════════════════════════════════════

class TestFocusManager:
    def test_topic_switch_detection(self):
        """话题切换应被检测到"""
        fm = FocusManager()
        assert fm.detect_topic_switch("换个话题，帮我查天气")
        assert fm.detect_topic_switch("by the way, what about...")
        assert not fm.detect_topic_switch("继续上面的问题")

    def test_adaptive_turns_simple(self):
        """简单对话应返回较少的保留轮数"""
        fm = FocusManager()
        memory = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        turns = fm.adaptive_short_term_turns(memory, "今天天气怎么样")
        assert turns == 2  # 无工具调用，简单对话

    def test_adaptive_turns_complex(self):
        """复杂任务（多工具调用）应返回更多保留轮数"""
        fm = FocusManager()
        memory = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "分析项目"},
            {"role": "assistant", "content": "好的", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "结果1"},
            {"role": "assistant", "content": "继续", "tool_calls": [{"id": "2"}, {"id": "3"}]},
            {"role": "tool", "content": "结果2"},
            {"role": "tool", "content": "结果3"},
            {"role": "assistant", "content": "继续", "tool_calls": [{"id": "4"}, {"id": "5"}, {"id": "6"}]},
            {"role": "tool", "content": "结果4"},
            {"role": "tool", "content": "结果5"},
            {"role": "tool", "content": "结果6"},
        ]
        turns = fm.adaptive_short_term_turns(memory, "继续分析")
        assert turns >= 4  # 多工具调用，保留更多轮

    def test_topic_switch_resets_turns(self):
        """话题切换后应重置为最少轮数"""
        fm = FocusManager()
        memory = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "分析代码"},
            {"role": "assistant", "content": "好的", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "结果"},
        ]
        turns = fm.adaptive_short_term_turns(memory, "换个话题，帮我查天气")
        assert turns == 2

    def test_message_scoring(self):
        """消息评分应反映重要性"""
        fm = FocusManager()
        user_msg = {"role": "user", "content": "选择方案 A"}
        tool_msg = {"role": "tool", "content": "执行结果"}
        error_msg = {"role": "tool", "content": "[错误] 失败了"}

        score_user = fm.score_message(user_msg, 5, 10)
        score_tool = fm.score_message(tool_msg, 5, 10)
        score_error = fm.score_message(error_msg, 5, 10)

        assert score_user > score_tool  # 用户消息比工具结果重要
        assert score_tool > score_error  # 成功结果比失败结果重要


# ═══════════════════════════════════════════════════════════
# 痛点三: 上下文混乱 — ToolBudget
# ═══════════════════════════════════════════════════════════

class TestToolBudget:
    def _make_tool(self, name, desc="工具描述"):
        return {
            "type": "function",
            "function": {"name": name, "description": desc, "parameters": {}},
        }

    def test_core_tools_always_kept(self):
        """核心工具应始终保留"""
        tb = ToolBudget()
        tools = [
            self._make_tool("web_search", "搜索"),
            self._make_tool("shell", "执行命令"),
            self._make_tool("read_file", "读文件"),
            self._make_tool("mcp_random_tool", "随机工具"),
            self._make_tool("calculator", "计算"),
        ]
        selected = tb.select_tools("你好", tools, 4000, {})
        names = {t["function"]["name"] for t in selected}
        assert "web_search" in names
        assert "shell" in names
        assert "read_file" in names

    def test_mcp_tools_filtered(self):
        """MCP 工具应参与筛选，不再无条件保留"""
        tb = ToolBudget(max_tool_token_ratio=0.1)
        tools = [
            self._make_tool("web_search", "搜索"),
            self._make_tool("shell", "执行命令"),
            self._make_tool("read_file", "读文件"),
            self._make_tool("write_file", "写文件"),
        ]
        # 添加大量 MCP 工具
        for i in range(20):
            tools.append(self._make_tool(f"mcp_server_{i}", f"MCP 工具 {i} 的描述"))

        selected = tb.select_tools("你好", tools, 4000, {})
        # 不应全部保留 20 个 MCP 工具
        assert len(selected) < len(tools)

    def test_frequency_based_selection(self):
        """高频使用的工具应优先保留"""
        tb = ToolBudget()
        # 模拟 calculator 被频繁使用
        for _ in range(10):
            tb.record_usage("calculator")

        tools = [
            self._make_tool("web_search", "搜索"),
            self._make_tool("shell", "执行命令"),
            self._make_tool("read_file", "读文件"),
            self._make_tool("write_file", "写文件"),
            self._make_tool("calculator", "计算"),
            self._make_tool("weather", "天气"),
        ]
        selected = tb.select_tools("你好", tools, 4000, {})
        names = {t["function"]["name"] for t in selected}
        assert "calculator" in names  # 高频工具应被保留

    def test_keyword_match_includes_mcp(self):
        """MCP 工具应通过描述关键词匹配"""
        tb = ToolBudget()
        tools = [
            self._make_tool("web_search", "搜索"),
            self._make_tool("shell", "执行命令"),
            self._make_tool("read_file", "读文件"),
            self._make_tool("write_file", "写文件"),
            self._make_tool("mcp_db_query", "数据库查询工具"),
            self._make_tool("mcp_email_send", "发送邮件工具"),
        ]
        selected = tb.select_tools("查询数据库", tools, 4000, {})
        names = {t["function"]["name"] for t in selected}
        assert "mcp_db_query" in names  # 描述匹配的 MCP 工具应被选中


# ═══════════════════════════════════════════════════════════
# 痛点四: 上下文冲突 — ConflictDetector
# ═══════════════════════════════════════════════════════════

class TestConflictDetector:
    def test_detect_decision_conflict(self):
        """应检测到决策冲突"""
        cd = ConflictDetector()
        memory = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "我们选择方案 A"},
            {"role": "assistant", "content": "好的，采用方案 A"},
            {"role": "user", "content": "还是选择方案 B 吧"},
        ]
        conflicts = cd.detect(memory)
        assert len(conflicts) >= 1
        assert conflicts[0].type == "decision"

    def test_detect_preference_conflict(self):
        """应检测到偏好冲突"""
        cd = ConflictDetector()
        memory = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "以后都用中文回答"},
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": "以后都用英文回答"},
        ]
        conflicts = cd.detect(memory)
        assert len(conflicts) >= 1
        # 可能被检测为 decision 或 preference（两个模式都能匹配）
        assert conflicts[0].type in ("preference", "decision")

    def test_no_conflict_different_topics(self):
        """不同主题的决策不应被视为冲突"""
        cd = ConflictDetector()
        memory = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "使用 Python 写脚本"},
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": "采用 REST 架构"},
        ]
        conflicts = cd.detect(memory)
        # Python 和 REST 是不同主题，不应冲突
        assert len(conflicts) == 0

    def test_resolve_prompt(self):
        """冲突解决提示应以最新指令为准"""
        cd = ConflictDetector()
        memory = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "选择方案 A"},
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": "选择方案 B"},
        ]
        conflicts = cd.detect(memory)
        prompt = cd.resolve_prompt(conflicts)
        if prompt:
            assert "最新" in prompt


# ═══════════════════════════════════════════════════════════
# 统一入口 — ContextHygiene
# ═══════════════════════════════════════════════════════════

class TestContextHygiene:
    def test_scan_and_clean(self):
        """统一扫描应返回完整报告"""
        ch = ContextHygiene()
        memory = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "查天气"},
            {"role": "tool", "content": "[错误] 超时"},
            {"role": "assistant", "content": "抱歉，查询失败"},
        ]
        report = ch.scan_and_clean(memory)
        assert "toxic_count" in report
        assert "conflicts" in report
        assert report["toxic_count"] >= 1

    def test_score_messages_with_toxic(self):
        """有毒消息的评分应被降权"""
        ch = ContextHygiene()
        memory = [
            {"role": "system", "content": "你是助手"},
            {"role": "assistant", "content": "Python 是编译型语言"},
            {"role": "user", "content": "不对，Python 是解释型语言"},
            {"role": "assistant", "content": "你说得对，Python 是解释型语言"},
        ]
        ch.detox.scan(memory)
        scores = ch.score_messages(memory)
        # 被纠正的 assistant 消息（index 1）应该分数更低
        assert scores[1] < scores[3]  # 被纠正的 < 正确的

    def test_reset(self):
        """重置应清除所有状态"""
        ch = ContextHygiene()
        ch.record_tool_usage("web_search")
        ch.detox.scan([{"role": "tool", "content": "[错误] 失败"}])
        ch.reset()
        assert len(ch.detox.get_toxic_indices()) == 0


# ═══════════════════════════════════════════════════════════
# 自动压缩 — SessionMixin._auto_compact_memory
# ═══════════════════════════════════════════════════════════

class TestAutoCompact:
    def test_auto_compact_triggers(self):
        """memory 超过阈值时应自动压缩"""
        from agent_core.agent import Agent
        import agent_core.mixins.session_mixin as sm

        # 临时降低阈值方便测试
        old_threshold = sm._AUTO_COMPACT_THRESHOLD
        sm._AUTO_COMPACT_THRESHOLD = 10
        try:
            agent = Agent(stream=False)
            # 填充大量消息
            for i in range(20):
                agent.memory.append({"role": "user", "content": f"问题 {i}"})
                agent.memory.append({"role": "assistant", "content": f"回答 {i}"})

            old_len = len(agent.memory)
            assert old_len > 10

            # 触发自动持久化（内部会自动压缩）
            agent._auto_persist()

            # 压缩后 memory 应该变短
            assert len(agent.memory) < old_len
            # 应该有历史摘要
            has_summary = any(
                "历史摘要" in (m.get("content") or "")
                for m in agent.memory if m.get("role") == "system"
            )
            assert has_summary
        finally:
            sm._AUTO_COMPACT_THRESHOLD = old_threshold

    def test_no_compact_when_short(self):
        """memory 未超过阈值时不应压缩"""
        from agent_core.agent import Agent
        import agent_core.mixins.session_mixin as sm

        old_threshold = sm._AUTO_COMPACT_THRESHOLD
        sm._AUTO_COMPACT_THRESHOLD = 100
        try:
            agent = Agent(stream=False)
            agent.memory.append({"role": "user", "content": "你好"})
            agent.memory.append({"role": "assistant", "content": "你好！"})

            old_len = len(agent.memory)
            agent._auto_persist()
            assert len(agent.memory) == old_len  # 不应变化
        finally:
            sm._AUTO_COMPACT_THRESHOLD = old_threshold

    def test_compact_preserves_recent(self):
        """压缩后应保留最近的对话"""
        from agent_core.agent import Agent
        import agent_core.mixins.session_mixin as sm

        old_threshold = sm._AUTO_COMPACT_THRESHOLD
        old_keep = sm._COMPACT_KEEP_TURNS
        sm._AUTO_COMPACT_THRESHOLD = 10
        sm._COMPACT_KEEP_TURNS = 3
        try:
            agent = Agent(stream=False)
            for i in range(15):
                agent.memory.append({"role": "user", "content": f"问题 {i}"})
                agent.memory.append({"role": "assistant", "content": f"回答 {i}"})

            agent._auto_persist()

            # 最近的消息应该被保留
            contents = [m.get("content", "") for m in agent.memory]
            all_text = " ".join(contents)
            assert "问题 14" in all_text  # 最新的应该在
        finally:
            sm._AUTO_COMPACT_THRESHOLD = old_threshold
            sm._COMPACT_KEEP_TURNS = old_keep

    def test_compact_resets_hygiene(self):
        """压缩后应重置上下文卫生系统的毒性标记"""
        from agent_core.agent import Agent
        import agent_core.mixins.session_mixin as sm

        old_threshold = sm._AUTO_COMPACT_THRESHOLD
        sm._AUTO_COMPACT_THRESHOLD = 10
        try:
            agent = Agent(stream=False)
            # 添加一些有毒消息
            agent.memory.append({"role": "tool", "content": "[错误] 失败了"})
            agent._hygiene.detox.scan(agent.memory)
            assert len(agent._hygiene.detox.get_toxic_indices()) > 0

            # 填充到超过阈值
            for i in range(20):
                agent.memory.append({"role": "user", "content": f"问题 {i}"})
                agent.memory.append({"role": "assistant", "content": f"回答 {i}"})

            agent._auto_persist()

            # 毒性标记应被重置
            assert len(agent._hygiene.detox.get_toxic_indices()) == 0
        finally:
            sm._AUTO_COMPACT_THRESHOLD = old_threshold
