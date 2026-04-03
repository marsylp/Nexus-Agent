"""MCP 客户端/管理器测试（不依赖外部 MCP Server 连接）"""
import os, json, pytest
from agent_core.mcp_client import MCPClient
from agent_core.mcp_manager import (
    _load_config, _config_fingerprint, _register_tools, _unregister_tools,
    get_mcp_status, is_mcp_auto_approved, list_mcp_tools,
)


class TestMCPClientAutoApprove:
    def test_specific_tools(self):
        client = MCPClient("test", {"autoApprove": ["read_file", "list_directory"]})
        assert client.is_auto_approved("read_file") is True
        assert client.is_auto_approved("write_file") is False

    def test_wildcard(self):
        client = MCPClient("test2", {"autoApprove": ["*"]})
        assert client.is_auto_approved("anything") is True

    def test_empty(self):
        client = MCPClient("test3", {})
        assert client.is_auto_approved("read_file") is False


class TestMCPConfig:
    def test_load_config_returns_dict(self):
        config = _load_config()
        assert isinstance(config, dict)

    def test_config_fingerprint_deterministic(self):
        cfg = {"server1": {"command": "test"}}
        h1 = _config_fingerprint(cfg)
        h2 = _config_fingerprint(cfg)
        assert h1 == h2

    def test_config_fingerprint_changes(self):
        cfg1 = {"server1": {"command": "test"}}
        cfg2 = {"server1": {"command": "test2"}}
        assert _config_fingerprint(cfg1) != _config_fingerprint(cfg2)


class TestMCPStatus:
    def test_get_status(self):
        status = get_mcp_status()
        assert isinstance(status, list)

    def test_list_tools(self):
        tools = list_mcp_tools()
        assert isinstance(tools, list)


class TestMCPAutoApproveGlobal:
    def test_unknown_tool(self):
        assert is_mcp_auto_approved("nonexistent_tool") is False


class TestMCPToolRegistration:
    def test_unregister_cleans_up(self):
        from agent_core.tools import get_registry
        registry = get_registry()
        # 手动注册一个假 MCP 工具
        registry["mcp_fake_test_tool"] = {
            "function": lambda: None,
            "spec": {"type": "function", "function": {"name": "mcp_fake_test_tool", "description": "test", "parameters": {}}},
        }
        assert "mcp_fake_test_tool" in registry
        _unregister_tools("fake")
        assert "mcp_fake_test_tool" not in registry
