"""Powers 系统 

Powers 是可安装的能力包，每个 Power 可以包含：
- POWER.md       — 文档说明（激活时加载到上下文）
- steering/      — Steering 文件（自动合并到 steering 系统）
- mcp.json       — MCP Server 配置（自动合并到 MCP 管理器）
- skills/        — 自定义 Skill 文件（自动加载工具）

目录结构:
  powers/
    weather-power/
      POWER.md
      steering/
        weather-guide.md
      mcp.json
      skills/
        weather_skill.py


- Powers 打包 documentation + steering + MCP servers
- activate 时加载 POWER.md + 工具定义 + steering
- install/uninstall 管理生命周期
- 支持全局（~/.nexus-agent/powers/）和项目级（powers/）
"""
from __future__ import annotations
import json, os, importlib, importlib.util, glob, shutil
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Power:
    """单个 Power 包"""
    name: str
    path: str  # 包目录绝对路径
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    activated: bool = False

    # 运行时状态
    doc_content: str = ""           # POWER.md 内容
    steering_files: list[str] = field(default_factory=list)
    mcp_config: dict = field(default_factory=dict)
    skill_tools: list[str] = field(default_factory=list)

    @property
    def power_md_path(self) -> str:
        return os.path.join(self.path, "POWER.md")

    @property
    def steering_dir(self) -> str:
        return os.path.join(self.path, "steering")

    @property
    def mcp_json_path(self) -> str:
        return os.path.join(self.path, "mcp.json")

    @property
    def skills_dir(self) -> str:
        return os.path.join(self.path, "skills")

    def has_doc(self) -> bool:
        return os.path.exists(self.power_md_path)

    def has_steering(self) -> bool:
        return os.path.isdir(self.steering_dir)

    def has_mcp(self) -> bool:
        return os.path.exists(self.mcp_json_path)

    def has_skills(self) -> bool:
        return os.path.isdir(self.skills_dir)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "keywords": self.keywords,
            "activated": self.activated,
            "has_doc": self.has_doc(),
            "has_steering": self.has_steering(),
            "has_mcp": self.has_mcp(),
            "has_skills": self.has_skills(),
            "tools": self.skill_tools,
        }


def _parse_power_md(path: str) -> tuple[str, str, list[str]]:
    """解析 POWER.md，提取描述和关键词

    支持 front-matter:
    ---
    description: 天气查询能力
    keywords: weather, forecast, 天气
    ---
    """
    if not os.path.exists(path):
        return "", "", []

    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    description = ""
    keywords = []
    body = content

    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            raw = content[3:end].strip()
            body = content[end + 3:].strip()
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("keywords:"):
                    kw_str = line.split(":", 1)[1].strip()
                    keywords = [k.strip().strip('"').strip("'") for k in kw_str.split(",")]

    if not description and body:
        # 取第一行非标题文本作为描述
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                description = line[:100]
                break

    return body, description, keywords


class PowersManager:
    """Powers 管理器 — 发现、安装、激活、卸载"""

    def __init__(self, powers_dirs: list[str] | None = None):
        self._powers_dirs = powers_dirs or self._default_dirs()
        self._powers: dict[str, Power] = {}
        self._discover()

    @staticmethod
    def _default_dirs() -> list[str]:
        dirs = []
        global_dir = os.path.expanduser("~/.nexus-agent/powers")
        if os.path.isdir(global_dir):
            dirs.append(global_dir)
        local_dir = "powers"
        if os.path.isdir(local_dir):
            dirs.append(os.path.abspath(local_dir))
        return dirs

    def _discover(self):
        """扫描所有 powers 目录，发现已安装的 Power"""
        self._powers.clear()
        for pdir in self._powers_dirs:
            if not os.path.isdir(pdir):
                continue
            for entry in sorted(os.listdir(pdir)):
                full = os.path.join(pdir, entry)
                if not os.path.isdir(full):
                    continue
                if entry.startswith("_") or entry.startswith("."):
                    continue
                power = Power(name=entry, path=full)
                # 解析 POWER.md
                if power.has_doc():
                    body, desc, kws = _parse_power_md(power.power_md_path)
                    power.doc_content = body
                    power.description = desc
                    power.keywords = kws
                self._powers[entry] = power

    # ── 生命周期 ─────────────────────────────────────────────

    def activate(self, name: str) -> Power | None:
        """激活 Power — 加载文档、steering、MCP、skills"""
        power = self._powers.get(name)
        if not power:
            return None
        if power.activated:
            return power

        # 1. 加载 POWER.md
        if power.has_doc() and not power.doc_content:
            body, desc, kws = _parse_power_md(power.power_md_path)
            power.doc_content = body
            power.description = desc
            power.keywords = kws

        # 2. 收集 steering 文件
        if power.has_steering():
            power.steering_files = []
            for f in sorted(os.listdir(power.steering_dir)):
                if f.endswith(".md"):
                    power.steering_files.append(os.path.join(power.steering_dir, f))

        # 3. 加载 MCP 配置
        if power.has_mcp():
            try:
                with open(power.mcp_json_path, "r", encoding="utf-8") as f:
                    power.mcp_config = json.load(f).get("mcpServers", {})
            except Exception:
                pass

        # 4. 加载 Skills
        if power.has_skills():
            power.skill_tools = self._load_power_skills(power)

        power.activated = True
        return power

    def deactivate(self, name: str) -> bool:
        """停用 Power

        注意: Python 不支持真正的模块卸载。已加载的 skill 模块仍驻留在
        sys.modules 中，但其注册的工具会从注册表中移除，不再对 Agent 可见。
        """
        power = self._powers.get(name)
        if not power or not power.activated:
            return False

        # 卸载 skills 工具（从注册表移除）
        if power.skill_tools:
            from agent_core.tools import get_registry
            registry = get_registry()
            for tool_name in power.skill_tools:
                registry.pop(tool_name, None)

        # 清理 sys.modules 中的 power skill 模块引用
        import sys
        prefix = "power_{}_".format(power.name)
        to_remove = [k for k in sys.modules if k.startswith(prefix)]
        for k in to_remove:
            del sys.modules[k]

        power.activated = False
        power.skill_tools = []
        power.mcp_config = {}
        return True

    def install(self, source_path: str, target_dir: str | None = None) -> Power | None:
        """安装 Power（从目录复制到 powers/）"""
        source = os.path.abspath(source_path)
        if not os.path.isdir(source):
            return None

        name = os.path.basename(source)
        target_base = target_dir or (self._powers_dirs[0] if self._powers_dirs
                                     else os.path.abspath("powers"))
        os.makedirs(target_base, exist_ok=True)
        dest = os.path.join(target_base, name)

        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(source, dest)

        # 重新发现
        self._discover()
        return self._powers.get(name)

    def uninstall(self, name: str) -> bool:
        """卸载 Power"""
        power = self._powers.get(name)
        if not power:
            return False
        self.deactivate(name)
        try:
            shutil.rmtree(power.path)
        except Exception:
            return False
        del self._powers[name]
        return True

    # ── 查询 ─────────────────────────────────────────────────

    def list_powers(self) -> list[dict]:
        return [p.to_dict() for p in self._powers.values()]

    def get(self, name: str) -> Power | None:
        return self._powers.get(name)

    def get_activated(self) -> list[Power]:
        return [p for p in self._powers.values() if p.activated]

    def get_all_mcp_config(self) -> dict:
        """获取所有已激活 Power 的 MCP 配置（合并）"""
        merged = {}
        for p in self.get_activated():
            merged.update(p.mcp_config)
        return merged

    def get_all_steering_files(self) -> list[str]:
        """获取所有已激活 Power 的 steering 文件路径"""
        files = []
        for p in self.get_activated():
            files.extend(p.steering_files)
        return files

    def get_all_doc_content(self) -> str:
        """获取所有已激活 Power 的文档内容"""
        parts = []
        for p in self.get_activated():
            if p.doc_content:
                parts.append("[Power: {}]\n{}".format(p.name, p.doc_content))
        return "\n\n".join(parts)

    def find_by_keyword(self, text: str) -> list[Power]:
        """根据关键词匹配 Power（主动激活）"""
        text_lower = text.lower()
        matched = []
        for p in self._powers.values():
            if p.activated:
                continue
            for kw in p.keywords:
                if kw.lower() in text_lower:
                    matched.append(p)
                    break
        return matched

    # ── 内部方法 ─────────────────────────────────────────────

    @staticmethod
    def _load_power_skills(power: Power) -> list[str]:
        """动态加载 Power 的 skill 模块"""
        loaded = []
        skills_dir = power.skills_dir
        if not os.path.isdir(skills_dir):
            return loaded

        from agent_core.tools import get_registry
        before = set(get_registry().keys())

        for py_file in sorted(glob.glob(os.path.join(skills_dir, "*.py"))):
            name = os.path.basename(py_file)[:-3]
            if name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    "power_{}_{}".format(power.name, name), py_file
                )
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
            except Exception as e:
                print("  ⚠️  Power [{}] skill [{}] 加载失败: {}".format(power.name, name, e))

        after = set(get_registry().keys())
        loaded = list(after - before)
        return loaded


# ── 全局单例 ────────────────────────────────────────────────

_manager: PowersManager | None = None


def get_powers_manager(powers_dirs: list[str] | None = None) -> PowersManager:
    global _manager
    if _manager is None:
        _manager = PowersManager(powers_dirs)
    return _manager
