"""抖音热搜 Skill 测试"""
import pytest
from agent_core.tools import _REGISTRY
from agent_core.token_optimizer import compress_tool_result


class TestDouyinRegistration:
    def test_function_callable(self):
        from skills.douyin import douyin_hot
        assert callable(douyin_hot)

    def test_tool_registered(self):
        assert "douyin_hot" in _REGISTRY
        spec = _REGISTRY["douyin_hot"]["spec"]["function"]
        assert "抖音" in spec["description"]

    def test_news_source_includes_douyin(self):
        spec = _REGISTRY["news"]["spec"]["function"]
        enum_vals = spec["parameters"]["properties"]["source"]["enum"]
        assert "douyin" in enum_vals


class TestDouyinTokenOptimizer:
    def test_short_content_unchanged(self):
        short = "test content"
        assert compress_tool_result(short, "douyin_hot") == short

    def test_structured_whitelist(self):
        long_content = "\n".join(["line {}".format(i) for i in range(45)])
        result = compress_tool_result(long_content, "douyin_hot")
        assert "line 44" in result


class TestDouyinHelpers:
    def test_clean_url(self):
        from skills.douyin import _clean_url
        url = "https://example.com/path?utm_source=test&id=123"
        cleaned = _clean_url(url)
        assert "utm_source" not in cleaned
        assert "id=123" in cleaned

    def test_format_hot(self):
        from skills.douyin import _format_hot
        assert "万" in _format_hot(22793981)
        assert _format_hot("") == ""
        assert _format_hot(None) == ""
        assert _format_hot(500) == "500"
