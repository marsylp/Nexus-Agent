"""Scanner — 项目结构扫描器

扫描项目源码目录，识别 API/Views/Components/Stores/Utils 等模块，
检测 types/router/locales 等特殊目录，适配多种技术栈。
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field

from .config import HarnessConfig


# ── 数据类 ──────────────────────────────────────────────

@dataclass
class ScanResult:
    """扫描结果"""
    apis: list[str] = field(default_factory=list)
    views: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    stores: list[str] = field(default_factory=list)
    utils: list[str] = field(default_factory=list)
    has_types: bool = False
    has_router: bool = False
    has_locales: bool = False


# ── 技术栈目录映射 ──────────────────────────────────────
# 每种技术栈定义 (目录名, ScanResult 字段名) 的映射列表

_STACK_DIR_MAP: dict[str, list[tuple[str, str]]] = {
    "vue3-ts": [
        ("api", "apis"),
        ("views", "views"),
        ("components", "components"),
        ("stores", "stores"),
        ("utils", "utils"),
    ],
    "react-ts": [
        ("services", "apis"),
        ("pages", "views"),
        ("components", "components"),
        ("hooks", "stores"),
        ("utils", "utils"),
    ],
    "python": [
        ("services", "apis"),
        ("views", "views"),
        ("models", "components"),
        ("utils", "utils"),
    ],
    "node-express": [
        ("routes", "apis"),
        ("controllers", "views"),
        ("services", "components"),
        ("models", "stores"),
        ("utils", "utils"),
    ],
}


# ── Scanner 类 ──────────────────────────────────────────

class Scanner:
    """项目结构扫描器，根据技术栈配置扫描源码目录。"""

    def __init__(self, config: HarnessConfig, project_root: str) -> None:
        self._config = config
        self._project_root = os.path.abspath(project_root)
        self._src_dir = os.path.join(self._project_root, config.src_dir)

    # ── 公共接口 ────────────────────────────────────────

    def scan(self) -> ScanResult:
        """扫描项目结构，返回 ScanResult。源码目录不存在时返回空结果。"""
        if not os.path.isdir(self._src_dir):
            return ScanResult()

        tech = self._config.tech_stack

        if tech == "android-kotlin":
            return self._scan_android()

        if tech in _STACK_DIR_MAP:
            return self._scan_by_map(_STACK_DIR_MAP[tech])

        # custom / 未知技术栈：使用 srcDir + 标准目录名
        return self._scan_custom()

    # ── 内部方法 ────────────────────────────────────────

    def _scan_by_map(self, dir_map: list[tuple[str, str]]) -> ScanResult:
        """根据目录映射表扫描，适用于 vue3-ts / react-ts / python / node-express。"""
        result = ScanResult()
        for dir_name, field_name in dir_map:
            target = os.path.join(self._src_dir, dir_name)
            files = self._list_files_recursive(target)
            setattr(result, field_name, files)

        self._detect_special_dirs(result)
        return result

    def _scan_android(self) -> ScanResult:
        """Android Kotlin 项目使用 glob 模式匹配深层目录。"""
        result = ScanResult()

        # android-kotlin 的 srcDir 是 app/src/main/java
        # 需要在 srcDir 下递归查找 ui/ viewmodel/ repository/ data/ util/ 目录
        mapping = [
            ("ui", "views"),
            ("viewmodel", "stores"),
            ("repository", "apis"),
            ("data", "components"),
            ("util", "utils"),
        ]

        for dir_name, field_name in mapping:
            # 使用 glob 递归匹配 **/dir_name/ 下的文件
            pattern = os.path.join(self._src_dir, "**", dir_name, "**", "*")
            matched = []
            for path in glob.glob(pattern, recursive=True):
                if os.path.isfile(path):
                    matched.append(os.path.relpath(path, self._project_root))
            matched.sort()
            setattr(result, field_name, matched)

        self._detect_special_dirs(result)
        return result

    def _scan_custom(self) -> ScanResult:
        """自定义/默认技术栈：使用 srcDir + 标准目录名。"""
        standard_map = [
            ("api", "apis"),
            ("views", "views"),
            ("components", "components"),
            ("stores", "stores"),
            ("utils", "utils"),
        ]
        return self._scan_by_map(standard_map)

    def _list_files_recursive(self, directory: str) -> list[str]:
        """递归列出目录下所有文件，返回相对于 project_root 的路径列表。"""
        if not os.path.isdir(directory):
            return []

        files: list[str] = []
        pattern = os.path.join(directory, "**", "*")
        for path in glob.glob(pattern, recursive=True):
            if os.path.isfile(path):
                files.append(os.path.relpath(path, self._project_root))
        files.sort()
        return files

    def _detect_special_dirs(self, result: ScanResult) -> None:
        """检测 srcDir 下的 types / router / locales 特殊目录。"""
        result.has_types = os.path.isdir(os.path.join(self._src_dir, "types"))
        result.has_router = os.path.isdir(os.path.join(self._src_dir, "router"))
        result.has_locales = os.path.isdir(os.path.join(self._src_dir, "locales"))
