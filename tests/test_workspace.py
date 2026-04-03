"""多工作区测试"""
import os, shutil, tempfile, pytest
from agent_core.workspace import Workspace, WorkspaceManager


class TestWorkspace:
    def test_properties(self):
        ws = Workspace(name="test", path="/tmp/test-ws")
        assert ws.config_dir == "/tmp/test-ws/config"
        assert ws.steering_dir == "/tmp/test-ws/steering"
        assert ws.agents_dir == "/tmp/test-ws/agents"
        assert ws.powers_dir == "/tmp/test-ws/powers"

    def test_serialization(self):
        ws = Workspace(name="test", path="/tmp/test-ws")
        d = ws.to_dict()
        ws2 = Workspace.from_dict(d)
        assert ws2.name == ws.name
        assert ws2.path == ws.path

    def test_exists(self):
        ws = Workspace(name="test", path="/nonexistent/path")
        assert ws.exists() is False


class TestWorkspaceManager:
    def test_init(self):
        wm = WorkspaceManager()
        assert wm.current is not None
        assert wm.current.path == os.getcwd()

    def test_add_list_remove(self):
        wm = WorkspaceManager()
        tmpdir = tempfile.mkdtemp()
        try:
            ws = wm.add(tmpdir, "test-ws")
            assert ws.name == "test-ws"
            names = [w["name"] for w in wm.list_workspaces()]
            assert "test-ws" in names
            # 不重复添加
            ws2 = wm.add(tmpdir)
            assert ws2.path == ws.path
            # 移除
            assert wm.remove("test-ws") is True
            assert wm.remove("nonexistent") is False
        finally:
            shutil.rmtree(tmpdir)

    def test_switch(self):
        wm = WorkspaceManager()
        tmpdir = tempfile.mkdtemp()
        try:
            wm.add(tmpdir, "switch-test")
            ws = wm.switch("switch-test")
            assert ws is not None
            assert wm.current.name == "switch-test"
            # 切回
            first = wm.list_workspaces()[0]["name"]
            wm.switch(first)
            wm.remove("switch-test")
        finally:
            shutil.rmtree(tmpdir)

    def test_all_active(self):
        wm = WorkspaceManager()
        active = wm.all_active
        assert len(active) >= 1
        assert all(w.active for w in active)


class TestWorkspaceConfigMerge:
    def test_merge_mcp_config(self):
        wm = WorkspaceManager()
        merged = wm.merge_mcp_config()
        assert isinstance(merged, dict)

    def test_merge_steering_dirs(self):
        wm = WorkspaceManager()
        dirs = wm.merge_steering_dirs()
        assert isinstance(dirs, list)
        assert any("steering" in d for d in dirs)

    def test_merge_hooks_configs(self):
        wm = WorkspaceManager()
        paths = wm.merge_hooks_configs()
        assert isinstance(paths, list)

    def test_merge_agents_dirs(self):
        wm = WorkspaceManager()
        dirs = wm.merge_agents_dirs()
        assert isinstance(dirs, list)

    def test_merge_powers_dirs(self):
        wm = WorkspaceManager()
        dirs = wm.merge_powers_dirs()
        assert isinstance(dirs, list)
