"""工具系统测试"""
import pytest
from agent_core.tools import get_tool_specs, call_tool, _check_command_risk


class TestToolRegistry:
    def test_tool_count(self):
        specs = get_tool_specs()
        assert len(specs) >= 15

    def test_required_tools_registered(self):
        names = [s["function"]["name"] for s in get_tool_specs()]
        required = ["calculator", "shell", "current_time", "read_file",
                     "write_file", "web_search", "run_python", "http_get", "system_info"]
        for r in required:
            assert r in names, "缺少工具: {}".format(r)

    def test_calculator(self):
        assert call_tool("calculator", {"expression": "2+3"}) == "5"
        assert call_tool("calculator", {"expression": "sqrt(16)"}) == "4.0"
        assert "3.14" in call_tool("calculator", {"expression": "round(pi, 2)"})

    def test_calculator_rejects_dangerous(self):
        result = call_tool("calculator", {"expression": "__import__('os')"})
        assert "错误" in result or "不允许" in result

    def test_shell_normal(self):
        result = call_tool("shell", {"command": "echo hello"})
        assert "hello" in result

    def test_shell_blocked(self):
        r1 = call_tool("shell", {"command": "rm -rf /"})
        assert "安全拦截" in r1 or "禁止" in r1
        r2 = call_tool("shell", {"command": "mkfs /dev/sda"})
        assert "安全拦截" in r2 or "禁止" in r2

    def test_shell_risk_detection(self):
        assert _check_command_risk("sudo apt install vim") is not None
        assert _check_command_risk("rm -r /tmp/test") is not None
        assert _check_command_risk("ls -la") is None

    def test_unknown_tool(self):
        result = call_tool("nonexistent_tool", {})
        assert "未知工具" in result or "错误" in result
