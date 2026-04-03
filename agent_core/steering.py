"""Steering 系统 — 三种 inclusion 模式

支持在 .md 文件头部添加 YAML front-matter 控制加载策略:

1. inclusion: always  — 始终加载（默认，无 front-matter 时也是此模式）
2. inclusion: fileMatch + fileMatchPattern — 当上下文中有匹配文件时加载
3. inclusion: manual  — 仅通过 /steer <name> 手动加载

示例 front-matter:
---
inclusion: fileMatch
fileMatchPattern: "*.py"
---
"""
from __future__ import annotations
import os, fnmatch
from typing import Optional


def _parse_front_matter(content: str) -> tuple[dict, str]:
    """解析 YAML front-matter，返回 (meta, body)"""
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end == -1:
        return {}, content
    raw = content[3:end].strip()
    body = content[end + 3:].strip()
    meta = {}
    for line in raw.splitlines():
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            val = val.strip().strip('"').strip("'")
            meta[key.strip()] = val
    return meta, body


class SteeringFile:
    """单个 steering 文件"""
    __slots__ = ("name", "path", "inclusion", "pattern", "meta", "body")

    def __init__(self, name, path, meta, body):
        self.name = name
        self.path = path
        self.inclusion = meta.get("inclusion", "always").lower()
        self.pattern = meta.get("fileMatchPattern", "")
        self.meta = meta
        self.body = body

    def matches_file(self, filepath: str) -> bool:
        """检查文件路径是否匹配 fileMatchPattern"""
        if not self.pattern:
            return False
        basename = os.path.basename(filepath)
        return fnmatch.fnmatch(basename, self.pattern) or fnmatch.fnmatch(filepath, self.pattern)


class SteeringManager:
    """Steering 管理器 — 管理所有 steering 文件的加载和匹配"""

    def __init__(self, steering_dir: str = "steering"):
        self.steering_dir = steering_dir
        self._files: list[SteeringFile] = []
        self._manual_active: set[str] = set()
        self.reload()

    def reload(self):
        """重新扫描 steering 目录"""
        self._files = []
        if not os.path.isdir(self.steering_dir):
            return
        for f in sorted(os.listdir(self.steering_dir)):
            if not f.endswith(".md") or f == "README.md":
                continue
            path = os.path.join(self.steering_dir, f)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    content = fh.read().strip()
                if not content:
                    continue
                meta, body = _parse_front_matter(content)
                self._files.append(SteeringFile(f, path, meta, body))
            except Exception:
                pass

    def activate_manual(self, name: str) -> bool:
        """手动激活一个 steering 文件"""
        for sf in self._files:
            if sf.name == name or sf.name == name + ".md":
                self._manual_active.add(sf.name)
                return True
        return False

    def deactivate_manual(self, name: str):
        self._manual_active.discard(name)
        self._manual_active.discard(name + ".md")

    def get_active_content(self, context_files=None) -> str:
        """根据当前上下文获取应加载的 steering 内容"""
        parts = []
        for sf in self._files:
            if sf.inclusion == "always":
                parts.append("[{}]\n{}".format(sf.name, sf.body))
            elif sf.inclusion == "filematch" and context_files:
                if any(sf.matches_file(fp) for fp in context_files):
                    parts.append("[{} (auto-matched)]\n{}".format(sf.name, sf.body))
            elif sf.inclusion == "manual":
                if sf.name in self._manual_active:
                    parts.append("[{} (manual)]\n{}".format(sf.name, sf.body))
        return "\n\n".join(parts)

    def list_files(self) -> list[dict]:
        result = []
        for sf in self._files:
            result.append({
                "name": sf.name,
                "inclusion": sf.inclusion,
                "pattern": sf.pattern,
                "active": sf.name in self._manual_active if sf.inclusion == "manual" else None,
            })
        return result


# ── 兼容旧接口 ─────────────────────────────────────────────

_manager: Optional[SteeringManager] = None


def get_steering_manager(steering_dir: str = "steering") -> SteeringManager:
    global _manager
    if _manager is None or _manager.steering_dir != steering_dir:
        _manager = SteeringManager(steering_dir)
    return _manager


def load_steering(steering_dir: str = "steering", context_files=None) -> str:
    """兼容旧接口 — 加载 steering 内容"""
    mgr = get_steering_manager(steering_dir)
    return mgr.get_active_content(context_files)
