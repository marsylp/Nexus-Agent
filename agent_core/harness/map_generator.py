"""MapGenerator — 认知地图生成器

基于 Scanner 的扫描结果生成 Markdown 格式的认知地图文件（`.harness/map.md`），
包含项目目录树、模块清单、层级依赖关系等信息，供 Agent 快速理解项目全貌。
"""

from __future__ import annotations

import os
from datetime import datetime

from .config import HarnessConfig
from .scanner import ScanResult


class MapGenerator:
    """认知地图生成器，基于扫描结果生成 `.harness/map.md`。"""

    def __init__(self, config: HarnessConfig, project_root: str) -> None:
        self._config = config
        self._project_root = os.path.abspath(project_root)

    # ── 公共接口 ────────────────────────────────────────

    def generate(self, scan_result: ScanResult) -> str:
        """基于扫描结果生成 Markdown 认知地图内容。"""
        lines: list[str] = []

        # 标题
        lines.append("# 项目认知地图")
        lines.append("")

        # 元信息：生成时间戳和技术栈
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"> 生成时间: {timestamp}")
        lines.append(f"> 技术栈: {self._config.tech_stack}")
        lines.append("")

        # 目录结构
        lines.append("## 目录结构")
        lines.append("")
        lines.append(self._build_directory_tree(scan_result))
        lines.append("")

        # 模块清单
        lines.append("## 模块清单")
        lines.append("")
        lines.append(self._build_module_sections(scan_result))

        # 层级依赖
        lines.append("## 层级依赖")
        lines.append("")
        lines.append(self._build_layer_dependency())
        lines.append("")

        # 特殊目录
        special = self._build_special_dirs(scan_result)
        if special:
            lines.append("## 特殊目录")
            lines.append("")
            lines.append(special)
            lines.append("")

        return "\n".join(lines)

    def write(self, scan_result: ScanResult) -> str:
        """生成认知地图并写入 `.harness/map.md`，返回文件路径。"""
        harness_dir = os.path.join(self._project_root, ".harness")
        os.makedirs(harness_dir, exist_ok=True)

        content = self.generate(scan_result)
        map_path = os.path.join(harness_dir, "map.md")

        with open(map_path, "w", encoding="utf-8") as f:
            f.write(content)

        return map_path

    # ── 内部方法 ────────────────────────────────────────

    def _build_directory_tree(self, scan_result: ScanResult) -> str:
        """构建目录树结构文本。"""
        src_dir = self._config.src_dir
        tree_lines: list[str] = ["```", f"{src_dir}/"]

        # 收集所有文件路径，提取直接子目录
        all_files: list[str] = []
        all_files.extend(scan_result.apis)
        all_files.extend(scan_result.views)
        all_files.extend(scan_result.components)
        all_files.extend(scan_result.stores)
        all_files.extend(scan_result.utils)

        # 提取 src_dir 下的直接子目录
        subdirs: set[str] = set()
        for file_path in all_files:
            # file_path 格式: src_dir/subdir/...
            rel = file_path
            if rel.startswith(src_dir + "/"):
                rest = rel[len(src_dir) + 1:]
                parts = rest.split("/")
                if len(parts) > 1:
                    subdirs.add(parts[0])

        # 添加特殊目录
        if scan_result.has_types:
            subdirs.add("types")
        if scan_result.has_router:
            subdirs.add("router")
        if scan_result.has_locales:
            subdirs.add("locales")

        for d in sorted(subdirs):
            tree_lines.append(f"├── {d}/")

        tree_lines.append("```")
        return "\n".join(tree_lines)

    def _build_module_sections(self, scan_result: ScanResult) -> str:
        """构建模块清单部分，只包含非空模块。"""
        sections: list[str] = []

        module_map: list[tuple[str, str, list[str]]] = [
            ("API 模块", "apis", scan_result.apis),
            ("页面（Views）", "views", scan_result.views),
            ("组件（Components）", "components", scan_result.components),
            ("状态管理（Stores）", "stores", scan_result.stores),
            ("工具函数（Utils）", "utils", scan_result.utils),
        ]

        for title, _field_name, file_list in module_map:
            if not file_list:
                continue
            section_lines = [f"### {title}"]
            for f in file_list:
                section_lines.append(f"- {f}")
            section_lines.append("")
            sections.append("\n".join(section_lines))

        return "\n".join(sections)

    def _build_layer_dependency(self) -> str:
        """构建层级依赖描述。"""
        layers = self._config.layers
        if not layers:
            return "未配置层级依赖关系。"

        chain = " → ".join(layers)
        return (
            f"{chain}\n"
            f"（箭头方向为允许的依赖方向，只允许向右依赖）"
        )

    def _build_special_dirs(self, scan_result: ScanResult) -> str:
        """构建特殊目录描述，无特殊目录时返回空字符串。"""
        items: list[str] = []
        if scan_result.has_types:
            items.append("- `types/` — 类型定义目录")
        if scan_result.has_router:
            items.append("- `router/` — 路由配置目录")
        if scan_result.has_locales:
            items.append("- `locales/` — 国际化资源目录")

        return "\n".join(items)
