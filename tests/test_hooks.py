"""Hook 事件系统测试"""
import os, json, pytest
from agent_core.hooks import (
    HookConfig, HookManager, EventType, load_hooks,
    get_tool_category, _matches_tool_types, TOOL_CATEGORIES,
)


class TestToolCategory:
    def test_read_tools(self):
        cats = get_tool_category("read_file")
        assert "read" in cats

    def test_write_tools(self):
        cats = get_tool_category("shell")
        assert "write" in cats or "shell" in cats

    def test_mcp_default_read(self):
        cats = get_tool_category("mcp_test_tool")
        assert "read" in cats

    def test_unknown_default_read(self):
        cats = get_tool_category("unknown_tool")
        assert "read" in cats


class TestToolTypeMatching:
    def test_wildcard(self):
        assert _matches_tool_types("anything", ["*"]) is True

    def test_category_match(self):
        assert _matches_tool_types("read_file", ["read"]) is True
        assert _matches_tool_types("read_file", ["write"]) is False

    def test_regex_match(self):
        assert _matches_tool_types("mcp_sql_query", [".*sql.*"]) is True
        assert _matches_tool_types("calculator", [".*sql.*"]) is False

    def test_empty_matches_all(self):
        assert _matches_tool_types("anything", []) is True


class TestHookConfig:
    def test_from_dict(self):
        d = {
            "name": "test-hook",
            "version": "1.0.0",
            "when": {"type": "fileEdited", "patterns": ["*.py"]},
            "then": {"type": "runCommand", "command": "echo ok"},
        }
        cfg = HookConfig.from_dict(d)
        assert cfg.name == "test-hook"
        assert cfg.event_type == "fileEdited"
        assert cfg.file_patterns == ["*.py"]
        assert cfg.action_type == "runCommand"
        assert cfg.command == "echo ok"

    def test_to_dict(self):
        cfg = HookConfig(
            name="test", event_type="preToolUse",
            tool_types=["write"], action_type="askAgent",
            prompt="check this",
        )
        d = cfg.to_dict()
        assert d["when"]["type"] == "preToolUse"
        assert d["when"]["toolTypes"] == ["write"]
        assert d["then"]["type"] == "askAgent"
        assert d["then"]["prompt"] == "check this"

    def test_roundtrip(self):
        original = {
            "name": "lint",
            "version": "1.0.0",
            "when": {"type": "fileEdited", "patterns": ["*.ts"]},
            "then": {"type": "runCommand", "command": "npm run lint"},
        }
        cfg = HookConfig.from_dict(original)
        d = cfg.to_dict()
        assert d["name"] == "lint"
        assert d["when"]["patterns"] == ["*.ts"]


class TestEventType:
    def test_all_events(self):
        assert len(EventType.ALL) == 10
        assert "fileEdited" in EventType.ALL
        assert "preToolUse" in EventType.ALL

    def test_legacy_map(self):
        assert EventType.LEGACY_MAP["before_tool"] == "preToolUse"
        assert EventType.LEGACY_MAP["after_tool"] == "postToolUse"


class TestHookManager:
    def test_on_emit_callback(self):
        mgr = HookManager()
        log = []
        mgr.on("preToolUse", lambda **kw: log.append(kw))
        mgr.emit("preToolUse", tool_name="shell")
        assert len(log) == 1
        assert log[0]["tool_name"] == "shell"

    def test_load_from_file(self):
        path = "config/hooks.json"
        if os.path.exists(path):
            mgr = HookManager()
            loaded = mgr.load_from_file(path)
            assert isinstance(loaded, list)

    def test_load_nonexistent(self):
        mgr = HookManager()
        loaded = mgr.load_from_file("/nonexistent/hooks.json")
        assert loaded == []

    def test_list_hooks(self):
        mgr = HookManager()
        hooks = mgr.list_hooks()
        assert isinstance(hooks, list)

    def test_get_hooks_for_event(self):
        mgr = HookManager()
        hooks = mgr.get_hooks_for_event("fileEdited")
        assert isinstance(hooks, list)


class TestLoadHooks:
    def test_load_hooks_with_agent(self):
        from agent_core.agent import Agent
        agent = Agent(stream=False)
        hooks = load_hooks(agent)
        assert isinstance(hooks, list)

    def test_hooks_json_parseable(self):
        path = "config/hooks.json"
        if os.path.exists(path):
            with open(path, "r") as f:
                config = json.load(f)
            assert "hooks" in config or isinstance(config, list)
