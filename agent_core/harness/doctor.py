"""Doctor — Harness 健康检查器

检测 Harness 体系的完整性和同步性，包括：
- `.harness/map.md` 存在性
- `.harnessrc.json` 存在性
- `.harness/specs/` 目录存在性
- 认知地图中引用路径的有效性
- Map 同步性（死链接检测）
- 未声明目录检测
- 未引用 Spec 检测
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .config import load_config


# ── 数据类 ──────────────────────────────────────────────

@dataclass
class CheckItem:
    """检查项结果"""
    name: str       # 检查项名称
    status: str     # pass / warn / fail
    message: str    # 描述信息


@dataclass
class HealthReport:
    """健康报告"""
    items: list[CheckItem] = field(default_factory=list)
    passed: int = 0
    warned: int = 0
    failed: int = 0
    total: int = 0


# ── Doctor 类 ──────────────────────────────────────────

class Doctor:
    """Harness 健康检查器，诊断 Harness 体系的完整性和同步性。"""

    def __init__(self, project_root: str) -> None:
        self._project_root = os.path.abspath(project_root)
        self._map_path = os.path.join(
            self._project_root, ".harness", "map.md"
        )
        self._config_path = os.path.join(
            self._project_root, ".harnessrc.json"
        )
        self._specs_dir = os.path.join(
            self._project_root, ".harness", "specs"
        )

    # ── 公共接口 ────────────────────────────────────────

    def check(self) -> HealthReport:
        """执行所有健康检查，返回报告。"""
        items: list[CheckItem] = []

        # 1. .harness/map.md 存在性
        map_exists = os.path.isfile(self._map_path)
        items.append(self._check_map_exists(map_exists))

        # 2. .harnessrc.json 存在性
        items.append(self._check_config_exists())

        # 3. .harness/specs/ 目录存在性
        items.append(self._check_specs_dir())

        # 4-7: Map 相关检查（map.md 不存在时全部标记为 fail）
        if map_exists:
            map_content = self._read_map()
            file_refs = self._extract_file_refs(map_content)
            dir_refs = self._extract_dir_refs(map_content)

            # 4. 路径有效性检查
            items.append(self._check_path_validity(file_refs))

            # 5. Map 同步性检测（死链接）
            items.append(self._check_dead_links(file_refs))

            # 6. 未声明目录检测
            items.append(self._check_undeclared_dirs(dir_refs))

            # 7. 未引用 Spec 检测
            items.append(self._check_unreferenced_specs(map_content))
        else:
            items.append(CheckItem(
                name="路径有效性",
                status="fail",
                message=".harness/map.md 不存在，无法检查路径有效性",
            ))
            items.append(CheckItem(
                name="Map 同步性（死链接）",
                status="fail",
                message=".harness/map.md 不存在，无法检测死链接",
            ))
            items.append(CheckItem(
                name="未声明目录检测",
                status="fail",
                message=".harness/map.md 不存在，无法检测未声明目录",
            ))
            items.append(CheckItem(
                name="未引用 Spec 检测",
                status="fail",
                message=".harness/map.md 不存在，无法检测未引用 Spec",
            ))

        # 汇总
        passed = sum(1 for item in items if item.status == "pass")
        warned = sum(1 for item in items if item.status == "warn")
        failed = sum(1 for item in items if item.status == "fail")

        return HealthReport(
            items=items,
            passed=passed,
            warned=warned,
            failed=failed,
            total=len(items),
        )

    # ── 检查项实现 ──────────────────────────────────────

    def _check_map_exists(self, exists: bool) -> CheckItem:
        """检查 .harness/map.md 是否存在。"""
        if exists:
            return CheckItem(
                name=".harness/map.md 存在性",
                status="pass",
                message="认知地图文件存在",
            )
        return CheckItem(
            name=".harness/map.md 存在性",
            status="fail",
            message=".harness/map.md 不存在，请运行 /harness scan 生成认知地图",
        )

    def _check_config_exists(self) -> CheckItem:
        """检查 .harnessrc.json 是否存在。"""
        if os.path.isfile(self._config_path):
            return CheckItem(
                name=".harnessrc.json 存在性",
                status="pass",
                message="配置文件存在",
            )
        return CheckItem(
            name=".harnessrc.json 存在性",
            status="warn",
            message=".harnessrc.json 不存在，将使用默认配置。建议运行 /harness init 初始化",
        )

    def _check_specs_dir(self) -> CheckItem:
        """检查 .harness/specs/ 目录是否存在且包含规范文件。"""
        if not os.path.isdir(self._specs_dir):
            return CheckItem(
                name=".harness/specs/ 目录",
                status="warn",
                message=".harness/specs/ 目录不存在，暂无规范文件",
            )
        specs = [
            f for f in os.listdir(self._specs_dir)
            if os.path.isfile(os.path.join(self._specs_dir, f))
        ]
        if not specs:
            return CheckItem(
                name=".harness/specs/ 目录",
                status="warn",
                message=".harness/specs/ 目录为空，暂无规范文件",
            )
        return CheckItem(
            name=".harness/specs/ 目录",
            status="pass",
            message=f".harness/specs/ 包含 {len(specs)} 个规范文件",
        )

    def _check_path_validity(self, file_refs: list[str]) -> CheckItem:
        """检查认知地图中引用的路径是否全部有效。"""
        if not file_refs:
            return CheckItem(
                name="路径有效性",
                status="pass",
                message="认知地图中未引用任何文件路径",
            )

        invalid: list[str] = []
        for ref in file_refs:
            full_path = os.path.join(self._project_root, ref)
            if not os.path.exists(full_path):
                invalid.append(ref)

        if not invalid:
            return CheckItem(
                name="路径有效性",
                status="pass",
                message=f"认知地图中引用的 {len(file_refs)} 个路径全部有效",
            )
        return CheckItem(
            name="路径有效性",
            status="fail",
            message=f"认知地图中有 {len(invalid)} 个无效路径: {', '.join(invalid[:5])}"
                    + ("..." if len(invalid) > 5 else ""),
        )

    def _check_dead_links(self, file_refs: list[str]) -> CheckItem:
        """检测认知地图中的文件引用是否存在死链接。"""
        if not file_refs:
            return CheckItem(
                name="Map 同步性（死链接）",
                status="pass",
                message="认知地图中未引用任何文件，无死链接",
            )

        dead: list[str] = []
        for ref in file_refs:
            full_path = os.path.join(self._project_root, ref)
            if not os.path.exists(full_path):
                dead.append(ref)

        if not dead:
            return CheckItem(
                name="Map 同步性（死链接）",
                status="pass",
                message=f"认知地图中 {len(file_refs)} 个文件引用均有效，无死链接",
            )
        return CheckItem(
            name="Map 同步性（死链接）",
            status="warn",
            message=f"发现 {len(dead)} 个死链接: {', '.join(dead[:5])}"
                    + ("..." if len(dead) > 5 else "")
                    + "。建议运行 /harness scan 更新认知地图",
        )

    def _check_undeclared_dirs(self, map_dir_refs: list[str]) -> CheckItem:
        """检测源码目录下实际存在但认知地图中未提及的目录。"""
        config = load_config(self._project_root)
        src_dir = os.path.join(self._project_root, config.src_dir)

        if not os.path.isdir(src_dir):
            return CheckItem(
                name="未声明目录检测",
                status="pass",
                message=f"源码目录 {config.src_dir}/ 不存在，跳过检测",
            )

        # 获取 srcDir 下的直接子目录
        actual_dirs: set[str] = set()
        try:
            for entry in os.listdir(src_dir):
                entry_path = os.path.join(src_dir, entry)
                if os.path.isdir(entry_path) and not entry.startswith("."):
                    actual_dirs.add(entry)
        except OSError:
            return CheckItem(
                name="未声明目录检测",
                status="warn",
                message=f"无法读取源码目录 {config.src_dir}/",
            )

        # 将 map 中引用的目录名提取为集合
        declared: set[str] = set()
        for d in map_dir_refs:
            # 取最后一个路径段作为目录名
            parts = d.strip("/").split("/")
            if parts:
                declared.add(parts[-1])

        undeclared = actual_dirs - declared
        if not undeclared:
            return CheckItem(
                name="未声明目录检测",
                status="pass",
                message=f"源码目录下所有 {len(actual_dirs)} 个子目录均已在认知地图中声明",
            )
        return CheckItem(
            name="未声明目录检测",
            status="warn",
            message=f"发现 {len(undeclared)} 个未声明目录: {', '.join(sorted(undeclared)[:5])}"
                    + ("..." if len(undeclared) > 5 else "")
                    + "。建议运行 /harness scan 更新认知地图",
        )

    def _check_unreferenced_specs(self, map_content: str) -> CheckItem:
        """检测 .harness/specs/ 下存在但认知地图中未引用的规范文件。"""
        if not os.path.isdir(self._specs_dir):
            return CheckItem(
                name="未引用 Spec 检测",
                status="pass",
                message=".harness/specs/ 目录不存在，跳过检测",
            )

        spec_files: list[str] = []
        try:
            for f in os.listdir(self._specs_dir):
                if os.path.isfile(os.path.join(self._specs_dir, f)):
                    spec_files.append(f)
        except OSError:
            return CheckItem(
                name="未引用 Spec 检测",
                status="warn",
                message="无法读取 .harness/specs/ 目录",
            )

        if not spec_files:
            return CheckItem(
                name="未引用 Spec 检测",
                status="pass",
                message=".harness/specs/ 目录为空，无需检测",
            )

        unreferenced: list[str] = []
        for spec in spec_files:
            if spec not in map_content:
                unreferenced.append(spec)

        if not unreferenced:
            return CheckItem(
                name="未引用 Spec 检测",
                status="pass",
                message=f".harness/specs/ 下 {len(spec_files)} 个规范文件均已在认知地图中引用",
            )
        return CheckItem(
            name="未引用 Spec 检测",
            status="warn",
            message=f"发现 {len(unreferenced)} 个未引用的 Spec: {', '.join(unreferenced[:5])}"
                    + ("..." if len(unreferenced) > 5 else "")
                    + "。建议在认知地图中添加引用或清理无用 Spec",
        )

    # ── 辅助方法 ────────────────────────────────────────

    def _read_map(self) -> str:
        """读取 .harness/map.md 内容。"""
        try:
            with open(self._map_path, "r", encoding="utf-8") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return ""

    def _extract_file_refs(self, map_content: str) -> list[str]:
        """从认知地图内容中提取文件引用路径。

        匹配 Markdown 列表项中的文件路径，格式如：
        - src/api/user.ts
        - src/views/login/LoginView.vue
        """
        refs: list[str] = []
        # 匹配 "- path/to/file.ext" 格式的列表项（带文件扩展名）
        pattern = re.compile(r"^[-*]\s+(\S+\.\w+)\s*$", re.MULTILINE)
        for match in pattern.finditer(map_content):
            path = match.group(1)
            # 排除 Markdown 链接和纯 URL
            if not path.startswith("http") and not path.startswith("["):
                refs.append(path)
        return refs

    def _extract_dir_refs(self, map_content: str) -> list[str]:
        """从认知地图内容中提取目录引用。

        匹配目录树中的目录名，格式如：
        ├── api/
        ├── views/
        """
        dirs: list[str] = []
        # 匹配 "├── dirname/" 或 "└── dirname/" 格式
        tree_pattern = re.compile(r"[├└]──\s+(\S+)/")
        for match in tree_pattern.finditer(map_content):
            dirs.append(match.group(1))
        return dirs
