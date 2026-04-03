"""审批系统测试"""
import os, pytest
from agent_core.approval import (
    get_safety_level, is_auto_approved, format_approval_prompt,
)


class TestSafetyLevel:
    def test_safe_tools(self):
        assert get_safety_level("calculator") == "safe"

    def test_confirm_tools(self):
        assert get_safety_level("shell") == "confirm"

    def test_dangerous_shell(self):
        assert get_safety_level("shell", {"command": "sudo rm -rf /tmp"}) == "dangerous"

    def test_mcp_default_confirm(self):
        assert get_safety_level("mcp_test") == "confirm"


class TestAutoApprove:
    def test_empty_not_approved(self):
        old = os.environ.get("AUTO_APPROVE", "")
        os.environ["AUTO_APPROVE"] = ""
        assert not is_auto_approved("shell")
        os.environ["AUTO_APPROVE"] = old

    def test_wildcard_approves_all(self):
        old = os.environ.get("AUTO_APPROVE", "")
        os.environ["AUTO_APPROVE"] = "*"
        assert is_auto_approved("shell")
        assert is_auto_approved("write_file")
        os.environ["AUTO_APPROVE"] = old

    def test_specific_tools(self):
        old = os.environ.get("AUTO_APPROVE", "")
        os.environ["AUTO_APPROVE"] = "shell,run_python"
        assert is_auto_approved("shell")
        assert not is_auto_approved("write_file")
        os.environ["AUTO_APPROVE"] = old


class TestApprovalPrompt:
    def test_confirm_format(self):
        p = format_approval_prompt("shell", {"command": "ls"}, "confirm")
        assert "确认执行" in p

    def test_dangerous_format(self):
        p = format_approval_prompt("shell", {"command": "sudo rm"}, "dangerous")
        assert "高风险" in p
