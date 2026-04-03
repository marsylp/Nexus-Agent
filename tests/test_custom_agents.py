"""自定义 Agent 测试"""
import os, shutil, tempfile, pytest
from agent_core.custom_agents import AgentConfig, CustomAgentManager


class TestAgentConfig:
    def test_from_file(self, tmp_path):
        # 创建临时配置文件
        cfg_data = {"name": "test-reviewer", "description": "测试审查",
                     "system_prompt": "你是测试", "tools": ["read_file", "list_dir"],
                     "max_iterations": 5, "auto_approve": ["read_file"]}
        import json
        f = tmp_path / "test-reviewer.json"
        f.write_text(json.dumps(cfg_data))
        cfg = AgentConfig.from_file(str(f))
        assert cfg.name == "test-reviewer"
        assert cfg.tools is not None
        assert "read_file" in cfg.tools
        assert cfg.max_iterations == 5
        assert "read_file" in cfg.auto_approve

    def test_serialization(self):
        cfg = AgentConfig(
            name="test", description="测试",
            system_prompt="你是测试", model="zhipu",
            tools=["calculator"], max_iterations=3,
        )
        d = cfg.to_dict()
        cfg2 = AgentConfig.from_dict(d)
        assert cfg2.name == cfg.name
        assert cfg2.model == cfg.model
        assert cfg2.tools == cfg.tools
        assert cfg2.max_iterations == cfg.max_iterations

    def test_minimal_config(self, tmp_path):
        import json
        f = tmp_path / "minimal.json"
        f.write_text(json.dumps({"name": "minimal", "tools": [], "max_iterations": 1}))
        cfg = AgentConfig.from_file(str(f))
        assert cfg.name == "minimal"
        assert cfg.tools == []
        assert cfg.max_iterations == 1


class TestCustomAgentManager:
    def test_discover(self, tmp_path):
        import json
        (tmp_path / "agent-a.json").write_text(json.dumps({"name": "agent-a", "description": "A"}))
        (tmp_path / "agent-b.json").write_text(json.dumps({"name": "agent-b", "description": "B"}))
        am = CustomAgentManager([str(tmp_path)])
        agents = am.list_agents()
        assert len(agents) >= 2
        names = [a["name"] for a in agents]
        assert "agent-a" in names
        assert "agent-b" in names

    def test_get(self, tmp_path):
        import json
        (tmp_path / "test-agent.json").write_text(json.dumps({"name": "test-agent"}))
        am = CustomAgentManager([str(tmp_path)])
        cfg = am.get("test-agent")
        assert cfg is not None
        assert cfg.name == "test-agent"

    def test_get_nonexistent(self):
        am = CustomAgentManager(["agents"])
        assert am.get("nonexistent") is None

    def test_create_delete(self):
        tmpdir = tempfile.mkdtemp()
        try:
            am = CustomAgentManager([tmpdir])
            cfg = am.create_config("test-agent", "测试Agent", "你是测试", save_dir=tmpdir)
            assert cfg.name == "test-agent"
            assert am.get("test-agent") is not None
            assert os.path.exists(os.path.join(tmpdir, "test-agent.json"))
            assert am.delete_config("test-agent") is True
            assert am.get("test-agent") is None
        finally:
            shutil.rmtree(tmpdir)

    def test_invoke_nonexistent(self):
        am = CustomAgentManager(["agents"])
        result = am.invoke("nonexistent", "test")
        assert "错误" in result or "未找到" in result
