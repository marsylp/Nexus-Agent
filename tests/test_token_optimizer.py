"""Token 优化测试"""
import os, json, pytest
from agent_core.token_optimizer import (
    count_tokens, count_messages_tokens, compress_tool_result,
    build_context, cache_tools, get_deduplicated_tools,
    select_tools, _enforce_token_budget,
)
from agent_core.tools import get_tool_specs


class TestTokenCounting:
    def test_basic_count(self):
        t = count_tokens("hello world")
        assert 0 < t < 10

    def test_messages_count(self):
        msgs = [{"role": "user", "content": "hello"}]
        mt = count_messages_tokens(msgs)
        assert mt > 0


class TestCompression:
    def test_long_text_compressed(self):
        long_text = "x" * 5000
        compressed = compress_tool_result(long_text, "shell")
        assert len(compressed) < len(long_text)

    def test_short_text_unchanged(self):
        assert compress_tool_result("hello") == "hello"


class TestLayeredMemory:
    def test_short_conversation_intact(self):
        memory = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        ctx = build_context(memory)
        assert len(ctx) == 3

    def test_long_conversation_compressed(self):
        memory = [{"role": "system", "content": "你是助手"}]
        for i in range(10):
            memory.append({"role": "user", "content": "问题{}".format(i)})
            memory.append({"role": "assistant", "content": "回答{}".format(i)})
        ctx = build_context(memory)
        assert len(ctx) < len(memory)
        has_summary = any("摘要" in m.get("content", "") for m in ctx)
        assert has_summary


class TestToolCache:
    def test_cache_and_dedup(self):
        tools = [
            {"function": {"name": "t1", "description": "test1", "parameters": {}}},
            {"function": {"name": "t2", "description": "test2", "parameters": {}}},
        ]
        cache_tools(tools)
        deduped = get_deduplicated_tools(["t1", "t2"])
        assert len(deduped) == 2


class TestToolSelection:
    def test_intent_based_selection(self):
        all_tools = get_tool_specs()
        selected = select_tools("现在几点了", all_tools)
        names = [s["function"]["name"] for s in selected]
        assert "current_time" in names
        assert len(selected) < len(all_tools)


class TestTokenBudget:
    def test_budget_enforcement(self):
        old = os.environ.get("MAX_CONTEXT_TOKENS", "")
        os.environ["MAX_CONTEXT_TOKENS"] = "100"
        msgs = [{"role": "system", "content": "sys"}]
        for _ in range(50):
            msgs.append({"role": "user", "content": "很长的消息内容 " * 20})
        result = _enforce_token_budget(msgs)
        assert len(result) < len(msgs)
        os.environ["MAX_CONTEXT_TOKENS"] = old or "4000"
