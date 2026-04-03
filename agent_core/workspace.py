"""多工作区管理 

特性：
- 工作区注册表（~/.nexus-agent/workspaces.json）
- 每个工作区独立配置目录（config/、steering/、hooks、agents/）
- 多层配置合并：全局 < workspace1 < workspace2（后者覆盖前者）
- 工作区切换与状态管理
- 自动检测项目根目录

"""
from __future__ import annotations
import json, os, hashlib
from dataclasses import dataclass, field
from typing import Optional

# 全局配置目录
GLOBAL_CONFIG_DIR = os.path.expanduser("~/.nexus-agent")

# 工作区注册表路径
_REGISTRY_PATH = os.path.join(GLOBAL_CONFIG_DIR, "workspaces.json")


@dataclass
class Workspace:
    """单个工作区"""
    name: str
    path: str  # 绝对路径
    active: bool = True

    @property
    def config_dir(self) -> str:
        return os.path.join(self.path, "config")

    @property
    def steering_dir(self) -> str:
        return os.path.join(self.path, "steering")

    @property
    def agents_dir(self) -> str:
        return os.path.join(self.path, "agents")

    @property
    def powers_dir(self) -> str:
        return os.path.join(self.path, "powers")

    def exists(self) -> bool:
        return os.path.isdir(self.path)

    def to_dict(self) -> dict:
        return {"name": self.name, "path": self.path, "active": self.active}

    @staticmethod
    def from_dict(d: dict) -> "Workspace":
        return Workspace(name=d["name"], path=d["path"], active=d.get("active", True))


class WorkspaceManager:
    """工作区管理器 — 管理多个工作区的注册、切换和配置合并"""

    def __init__(self):
        self._workspaces: list[Workspace] = []
        self._current_idx: int = 0
        self._load_registry()

    # ── 注册表持久化 ─────────────────────────────────────────

    def _load_registry(self):
        """从 ~/.nexus-agent/workspaces.json 加载"""
        if not os.path.exists(_REGISTRY_PATH):
            # 默认把 CWD 作为第一个工作区
            cwd = os.getcwd()
            self._workspaces = [Workspace(name=os.path.basename(cwd), path=cwd)]
            return
        try:
            with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._workspaces = [Workspace.from_dict(w) for w in data.get("workspaces", [])]
            self._current_idx = data.get("current", 0)
            if not self._workspaces:
                cwd = os.getcwd()
                self._workspaces = [Workspace(name=os.path.basename(cwd), path=cwd)]
        except Exception:
            cwd = os.getcwd()
            self._workspaces = [Workspace(name=os.path.basename(cwd), path=cwd)]

    def _save_registry(self):
        """保存到 ~/.nexus-agent/workspaces.json"""
        os.makedirs(GLOBAL_CONFIG_DIR, exist_ok=True)
        data = {
            "workspaces": [w.to_dict() for w in self._workspaces],
            "current": self._current_idx,
        }
        with open(_REGISTRY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── 工作区管理 ───────────────────────────────────────────

    @property
    def current(self) -> Workspace:
        if 0 <= self._current_idx < len(self._workspaces):
            return self._workspaces[self._current_idx]
        return self._workspaces[0] if self._workspaces else Workspace("default", os.getcwd())

    @property
    def all_active(self) -> list[Workspace]:
        """所有激活的工作区（按注册顺序）"""
        return [w for w in self._workspaces if w.active]

    def add(self, path: str, name: str | None = None) -> Workspace:
        """添加工作区"""
        abs_path = os.path.abspath(path)
        # 检查重复
        for w in self._workspaces:
            if w.path == abs_path:
                return w
        ws = Workspace(name=name or os.path.basename(abs_path), path=abs_path)
        self._workspaces.append(ws)
        self._save_registry()
        return ws

    def remove(self, name_or_path: str) -> bool:
        """移除工作区"""
        abs_path = os.path.abspath(name_or_path)
        for i, w in enumerate(self._workspaces):
            if w.name == name_or_path or w.path == abs_path:
                self._workspaces.pop(i)
                if self._current_idx >= len(self._workspaces):
                    self._current_idx = max(0, len(self._workspaces) - 1)
                self._save_registry()
                return True
        return False

    def switch(self, name_or_path: str) -> Workspace | None:
        """切换当前工作区"""
        abs_path = os.path.abspath(name_or_path)
        for i, w in enumerate(self._workspaces):
            if w.name == name_or_path or w.path == abs_path:
                self._current_idx = i
                self._save_registry()
                return w
        return None

    def list_workspaces(self) -> list[dict]:
        """列出所有工作区"""
        result = []
        for i, w in enumerate(self._workspaces):
            result.append({
                "name": w.name,
                "path": w.path,
                "active": w.active,
                "current": i == self._current_idx,
                "exists": w.exists(),
            })
        return result

    # ── 多工作区配置合并 ─────────────────────────────────────

    def merge_mcp_config(self) -> dict:
        """合并所有工作区的 MCP 配置

        优先级: 全局 < workspace[0] < workspace[1] < ...（后者覆盖前者）
        """
        merged = {}

        # 全局配置
        global_path = os.path.join(GLOBAL_CONFIG_DIR, "mcp.json")
        if os.path.exists(global_path):
            try:
                with open(global_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                merged.update(data.get("mcpServers", {}))
            except Exception:
                pass

        # 各工作区配置（按顺序，后者覆盖前者）
        for ws in self.all_active:
            mcp_path = os.path.join(ws.config_dir, "mcp_servers.json")
            if os.path.exists(mcp_path):
                try:
                    with open(mcp_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    merged.update(data.get("mcpServers", {}))
                except Exception:
                    pass

        return merged

    def merge_steering_dirs(self) -> list[str]:
        """收集所有工作区的 steering 目录列表"""
        dirs = []
        # 全局 steering
        global_steering = os.path.join(GLOBAL_CONFIG_DIR, "steering")
        if os.path.isdir(global_steering):
            dirs.append(global_steering)
        # 各工作区
        for ws in self.all_active:
            if os.path.isdir(ws.steering_dir):
                dirs.append(ws.steering_dir)
        return dirs

    def merge_hooks_configs(self) -> list[str]:
        """收集所有工作区的 hooks 配置文件路径"""
        paths = []
        global_hooks = os.path.join(GLOBAL_CONFIG_DIR, "hooks.json")
        if os.path.exists(global_hooks):
            paths.append(global_hooks)
        for ws in self.all_active:
            hooks_path = os.path.join(ws.config_dir, "hooks.json")
            if os.path.exists(hooks_path):
                paths.append(hooks_path)
        return paths

    def merge_agents_dirs(self) -> list[str]:
        """收集所有工作区的 agents 目录"""
        dirs = []
        global_agents = os.path.join(GLOBAL_CONFIG_DIR, "agents")
        if os.path.isdir(global_agents):
            dirs.append(global_agents)
        for ws in self.all_active:
            if os.path.isdir(ws.agents_dir):
                dirs.append(ws.agents_dir)
        return dirs

    def merge_powers_dirs(self) -> list[str]:
        """收集所有工作区的 powers 目录"""
        dirs = []
        global_powers = os.path.join(GLOBAL_CONFIG_DIR, "powers")
        if os.path.isdir(global_powers):
            dirs.append(global_powers)
        for ws in self.all_active:
            if os.path.isdir(ws.powers_dir):
                dirs.append(ws.powers_dir)
        return dirs


# ── 全局单例 ────────────────────────────────────────────────

_manager: WorkspaceManager | None = None


def get_workspace_manager() -> WorkspaceManager:
    global _manager
    if _manager is None:
        _manager = WorkspaceManager()
    return _manager
