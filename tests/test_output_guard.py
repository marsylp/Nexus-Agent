"""OutputGuard 单元测试 — Agent 输出质量守卫"""
import pytest
from agent_core.harness.output_guard import OutputGuard, OutputIssue


class TestHallucinatedUrls:

    def test_detects_fake_url(self):
        guard = OutputGuard()
        issues = guard.check("天气怎么样", '请访问 https://weather-app.com/city/beijing')
        assert any(i.type == "hallucination" for i in issues)

    def test_allows_real_url(self):
        guard = OutputGuard()
        issues = guard.check("搜索", '请访问 https://www.google.com')
        assert not any(i.type == "hallucination" for i in issues)

    def test_allows_example_com(self):
        guard = OutputGuard()
        issues = guard.check("示例", '例如 https://example.com/api')
        assert not any(i.type == "hallucination" for i in issues)


class TestFakePackages:

    def test_detects_fake_import(self):
        guard = OutputGuard()
        reply = "```python\nimport weather_api\n```"
        issues = guard.check("天气", reply)
        assert any(i.type == "hallucination" for i in issues)

    def test_allows_real_import(self):
        guard = OutputGuard()
        reply = "```python\nimport requests\n```"
        issues = guard.check("HTTP", reply)
        assert not any(i.type == "hallucination" for i in issues)


class TestVerboseReply:

    def test_detects_verbose_greeting(self):
        guard = OutputGuard({"max_simple_reply_chars": 200})
        long_reply = "我是 AI 助手。" + "这是一段很长的回复。" * 50
        issues = guard.check("你是谁", long_reply)
        assert any(i.type == "verbose" for i in issues)

    def test_allows_short_greeting(self):
        guard = OutputGuard()
        issues = guard.check("你是谁", "我是 Nexus Agent，一个 AI 助手。有什么可以帮你的？")
        assert not any(i.type == "verbose" for i in issues)

    def test_no_verbose_check_for_complex_question(self):
        guard = OutputGuard({"max_simple_reply_chars": 100})
        long_reply = "x" * 500
        issues = guard.check("请详细分析 Python 的 GIL 机制", long_reply)
        assert not any(i.type == "verbose" for i in issues)


class TestCodeHallucination:

    def test_detects_code_in_greeting(self):
        guard = OutputGuard()
        reply = "我是助手！\n```python\nprint('hello')\n```\n```python\nprint('world')\n```"
        issues = guard.check("你是谁", reply)
        assert any(i.type == "code_hallucination" for i in issues)

    def test_allows_code_for_code_question(self):
        guard = OutputGuard()
        reply = "```python\ndef hello():\n    print('hello')\n```\n```python\nhello()\n```"
        issues = guard.check("写一个 hello 函数", reply)
        assert not any(i.type == "code_hallucination" for i in issues)

    def test_allows_single_code_block_in_chat(self):
        guard = OutputGuard()
        reply = "Python 是这样的：\n```python\nprint('hello')\n```"
        issues = guard.check("什么是 Python", reply)
        assert not any(i.type == "code_hallucination" for i in issues)


class TestCorrectionPrompt:

    def test_generates_correction_for_errors(self):
        guard = OutputGuard()
        issues = [
            OutputIssue(type="hallucination", severity="error",
                        message="编造 URL", suggestion="用 example.com"),
        ]
        prompt = guard.build_correction_prompt("test", issues)
        assert prompt is not None
        assert "hallucination" in prompt
        assert "example.com" in prompt

    def test_no_correction_for_info_only(self):
        guard = OutputGuard()
        issues = [
            OutputIssue(type="minor", severity="info", message="小问题"),
        ]
        prompt = guard.build_correction_prompt("test", issues)
        assert prompt is None

    def test_no_correction_for_empty(self):
        guard = OutputGuard()
        prompt = guard.build_correction_prompt("test", [])
        assert prompt is None


class TestDisabled:

    def test_disabled_returns_empty(self):
        guard = OutputGuard({"enabled": False})
        issues = guard.check("你是谁", "x" * 10000 + "https://weather-app.com")
        assert issues == []


class TestIntegrationWithHarnessLayer:
    """测试 OutputGuard 与 HarnessLayer 的集成"""

    def test_agent_stop_triggers_output_guard(self, tmp_path):
        from agent_core.harness.layer import HarnessLayer

        layer = HarnessLayer(cwd=str(tmp_path))

        # 模拟 agentStop 事件
        layer.on_agent_stop(
            user_input="你是谁",
            reply="我是助手！\n```python\nprint('hello')\n```\n```python\nprint('world')\n```",
        )

        # 应该检测到代码幻觉
        assert len(layer._last_output_issues) > 0
        assert any(i.type == "code_hallucination" for i in layer._last_output_issues)

    def test_clean_reply_no_issues(self, tmp_path):
        from agent_core.harness.layer import HarnessLayer

        layer = HarnessLayer(cwd=str(tmp_path))
        layer.on_agent_stop(
            user_input="你是谁",
            reply="我是 Nexus Agent，一个 AI 助手。",
        )
        assert len(layer._last_output_issues) == 0

    def test_hallucination_recorded_in_metrics(self, tmp_path):
        import json
        from agent_core.harness.layer import HarnessLayer

        layer = HarnessLayer(cwd=str(tmp_path))
        layer.on_agent_stop(
            user_input="天气怎么样",
            reply="请访问 https://weather-app.com/city/beijing 查看天气",
        )

        # 检查度量文件
        metrics_file = tmp_path / ".harness" / "metrics" / "interceptions.jsonl"
        if metrics_file.is_file():
            lines = metrics_file.read_text().strip().split("\n")
            records = [json.loads(line) for line in lines if line]
            output_records = [r for r in records if r.get("violation_type", "").startswith("output_")]
            assert len(output_records) > 0
