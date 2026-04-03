"""额外 Skills 测试 — system_info, code_runner, env"""
import pytest
from agent_core.tools import call_tool


class TestSystemInfo:
    def test_system_info(self):
        r = call_tool("system_info", {})
        assert "Python" in r and "系统" in r

    def test_env_sensitive_masked(self):
        r = call_tool("get_env", {"name": "ZHIPU_API_KEY"})
        assert "已隐藏" in r


class TestCodeRunner:
    def test_basic_execution(self):
        r = call_tool("run_python", {"code": "print(1+1)"})
        assert "2" in r

    def test_sandbox_blocks_os(self):
        r = call_tool("run_python", {"code": "import os; os.listdir('.')"})
        assert "安全拦截" in r or "不允许" in r

    def test_sandbox_blocks_subprocess(self):
        r = call_tool("run_python", {"code": "import subprocess"})
        assert "安全拦截" in r or "不允许" in r

    def test_sandbox_allows_math(self):
        r = call_tool("run_python", {"code": "import math; print(math.sqrt(16))"})
        assert "4.0" in r

    def test_sandbox_blocks_exec(self):
        r = call_tool("run_python", {"code": "exec('print(1)')"})
        assert "安全拦截" in r or "不允许" in r

    def test_sandbox_blocks_dunder(self):
        r = call_tool("run_python", {"code": "print(''.__class__.__subclasses__())"})
        assert "安全拦截" in r or "不允许" in r
