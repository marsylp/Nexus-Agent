"""Verifier — 约束验证引擎

执行 6 种约束检查：dependency、naming、forbidden、security、i18n、hallucination。
支持通过 customConstraints 扩展自定义约束，禁用的约束类型自动跳过。
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import HarnessConfig


@dataclass
class Violation:
    """违规记录"""
    file: str
    line: int | None
    type: str
    message: str


# ── 安全反模式 ──────────────────────────────────────────

_SECRET_PATTERNS = [
    (r"""(?:API_KEY|SECRET_KEY|PRIVATE_KEY|PASSWORD|TOKEN)\s*[=:]\s*['"][^'"]{8,}['"]""",
     "检测到疑似密钥硬编码。建议使用环境变量或密钥管理服务。"),
]

_EVAL_PATTERN = re.compile(r"""\beval\s*\(""")
_LOCALSTORAGE_SENSITIVE = re.compile(
    r"""localStorage\s*\.\s*setItem\s*\(\s*['"](?:password|token|secret|key)['"]""",
    re.IGNORECASE,
)
_URL_TOKEN = re.compile(r"""[?&](?:token|api_key|secret)=[^&\s'"]+""", re.IGNORECASE)
_XSS_VHTML = re.compile(r"""v-html\s*=""")

# ── 中文文本检测（i18n）──────────────────────────────────

_CHINESE_CHAR = re.compile(r"[\u4e00-\u9fff]")
# Vue 模板区域：<template> ... </template>
_TEMPLATE_BLOCK = re.compile(r"<template[^>]*>(.*?)</template>", re.DOTALL)


class Verifier:
    """约束验证引擎，执行 6 种约束检查。"""

    def __init__(self, config: "HarnessConfig", project_root: str):
        self.config = config
        self.project_root = project_root

    # ── 主入口 ──────────────────────────────────────────

    def verify(self, file_path: str, content: str) -> list[Violation]:
        """对单个文件执行所有启用的约束检查，返回违规列表。"""
        violations: list[Violation] = []

        checks = [
            ("dependency", lambda: self.verify_dependency(file_path, content)),
            ("naming", lambda: self.verify_naming(file_path)),
            ("forbidden", lambda: self.verify_forbidden(file_path, content)),
            ("security", lambda: self.verify_security(file_path, content)),
            ("i18n", lambda: self.verify_i18n(file_path, content)),
            ("hallucination", lambda: self.verify_hallucination(file_path, content)),
        ]

        for check_type, check_fn in checks:
            try:
                violations.extend(check_fn())
            except Exception as e:
                print(f"⚠️  约束检查 [{check_type}] 异常: {e}", file=sys.stderr)

        # 自定义约束
        violations.extend(self._verify_custom(file_path, content))

        return violations

    # ── 依赖方向检查 ────────────────────────────────────

    def verify_dependency(self, file_path: str, content: str) -> list[Violation]:
        """检查 import 语句是否违反层级依赖方向（只允许向下层依赖）。"""
        layers = self.config.layers
        if not layers:
            return []

        # 确定当前文件所在层级
        rel_path = os.path.relpath(file_path, self.project_root)
        current_layer_idx = self._get_layer_index(rel_path, layers)
        if current_layer_idx is None:
            return []

        violations: list[Violation] = []
        # 匹配 ES/TS import 和 Python import
        import_patterns = [
            re.compile(r"""(?:import|from)\s+['"]([^'"]+)['"]"""),  # JS/TS
            re.compile(r"""(?:from\s+(\S+)\s+import|import\s+(\S+))"""),  # Python
        ]

        for line_no, line in enumerate(content.splitlines(), 1):
            for pattern in import_patterns:
                for match in pattern.finditer(line):
                    module_path = match.group(1) or (match.group(2) if match.lastindex and match.lastindex >= 2 else None)
                    if not module_path:
                        continue
                    target_idx = self._get_layer_index(module_path, layers)
                    if target_idx is not None and target_idx < current_layer_idx:
                        violations.append(Violation(
                            file=file_path,
                            line=line_no,
                            type="dependency",
                            message=(
                                f"层级依赖违规：{layers[current_layer_idx]} 层不应依赖 "
                                f"{layers[target_idx]} 层。建议将共享逻辑下沉到更低层级，"
                                f"或通过事件/回调解耦。"
                            ),
                        ))
        return violations

    def _get_layer_index(self, path: str, layers: list[str]) -> int | None:
        """根据路径判断所属层级索引。"""
        normalized = path.replace("\\", "/")
        for idx, layer in enumerate(layers):
            if f"/{layer}/" in f"/{normalized}/" or normalized.startswith(f"{layer}/"):
                return idx
        return None

    # ── 命名规范检查 ────────────────────────────────────

    def verify_naming(self, file_path: str) -> list[Violation]:
        """检查文件名/目录名是否符合命名规范。"""
        naming = self.config.naming
        if not naming:
            return []

        rel_path = os.path.relpath(file_path, self.project_root)
        normalized = rel_path.replace("\\", "/")
        file_name = os.path.basename(file_path)
        violations: list[Violation] = []

        for dir_pattern, regex in naming.items():
            if f"/{dir_pattern}/" in f"/{normalized}/" or normalized.startswith(f"{dir_pattern}/"):
                try:
                    if not re.match(regex, file_name):
                        violations.append(Violation(
                            file=file_path,
                            line=None,
                            type="naming",
                            message=(
                                f"命名规范违规：文件 '{file_name}' 在 {dir_pattern}/ 目录下，"
                                f"应匹配正则 {regex}。请按照命名规范重命名文件。"
                            ),
                        ))
                except re.error as e:
                    print(f"⚠️  命名规则正则编译失败 [{dir_pattern}]: {e}", file=sys.stderr)

        return violations

    # ── 禁止项检查 ──────────────────────────────────────

    def verify_forbidden(self, file_path: str, content: str) -> list[Violation]:
        """检查文件内容是否包含禁止的模式。"""
        forbidden = self.config.forbidden
        if not forbidden:
            return []

        violations: list[Violation] = []
        for rule in forbidden:
            pattern = rule.get("pattern", "")
            message = rule.get("message", "禁止项违规")
            file_pattern = rule.get("filePattern", "*")

            # 检查文件模式匹配
            if file_pattern != "*" and not self._match_glob(file_path, file_pattern):
                continue

            try:
                regex = re.compile(pattern)
            except re.error:
                continue

            for line_no, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    violations.append(Violation(
                        file=file_path,
                        line=line_no,
                        type="forbidden",
                        message=f"禁止项违规：{message}。请使用推荐的替代方案。",
                    ))

        return violations

    # ── 安全合规检查 ────────────────────────────────────

    def verify_security(self, file_path: str, content: str) -> list[Violation]:
        """检查安全合规（密钥泄露、eval、localStorage、URL Token、XSS）。"""
        security = self.config.security
        if not security:
            return []

        violations: list[Violation] = []

        for line_no, line in enumerate(content.splitlines(), 1):
            # 密钥泄露
            if security.get("checkSecretLeak", True):
                for pattern_str, msg in _SECRET_PATTERNS:
                    try:
                        if re.search(pattern_str, line):
                            violations.append(Violation(
                                file=file_path, line=line_no, type="security",
                                message=f"安全违规：{msg}",
                            ))
                    except re.error:
                        pass

            # eval 使用
            if security.get("checkEval", True) and _EVAL_PATTERN.search(line):
                violations.append(Violation(
                    file=file_path, line=line_no, type="security",
                    message="安全违规：检测到 eval() 使用。建议使用 JSON.parse() 或其他安全替代方案。",
                ))

            # localStorage 敏感数据
            if security.get("checkLocalStorage", True) and _LOCALSTORAGE_SENSITIVE.search(line):
                violations.append(Violation(
                    file=file_path, line=line_no, type="security",
                    message="安全违规：检测到 localStorage 存储敏感数据。建议使用加密存储或 httpOnly Cookie。",
                ))

            # URL Token 泄露
            if security.get("checkUrlToken", True) and _URL_TOKEN.search(line):
                violations.append(Violation(
                    file=file_path, line=line_no, type="security",
                    message="安全违规：检测到 URL 中包含 Token/密钥参数。建议使用 HTTP Header 传递认证信息。",
                ))

            # XSS（v-html）
            if security.get("checkXss", True) and _XSS_VHTML.search(line):
                violations.append(Violation(
                    file=file_path, line=line_no, type="security",
                    message="安全违规：检测到 v-html 使用，存在 XSS 风险。建议使用 v-text 或对内容进行消毒处理。",
                ))

        return violations

    # ── 国际化检查 ──────────────────────────────────────

    def verify_i18n(self, file_path: str, content: str) -> list[Violation]:
        """检查 Vue 模板中硬编码中文文本。"""
        i18n = self.config.i18n
        if not i18n or not i18n.get("enabled", False):
            return []

        # 检查文件模式
        file_patterns = i18n.get("filePatterns", [])
        if file_patterns and not any(self._match_glob(file_path, p) for p in file_patterns):
            return []

        ignore_patterns = [re.compile(p) for p in i18n.get("ignorePatterns", []) if p]
        violations: list[Violation] = []

        # 对 .vue 文件只检查 <template> 区域
        if file_path.endswith(".vue"):
            template_match = _TEMPLATE_BLOCK.search(content)
            if not template_match:
                return []
            template_content = template_match.group(1)
            template_start = content[:template_match.start(1)].count("\n") + 1
        else:
            template_content = content
            template_start = 1

        for offset, line in enumerate(template_content.splitlines()):
            line_no = template_start + offset
            # 跳过忽略模式
            if any(p.search(line) for p in ignore_patterns):
                continue
            # 跳过注释行
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("<!--"):
                continue
            if _CHINESE_CHAR.search(line):
                violations.append(Violation(
                    file=file_path,
                    line=line_no,
                    type="i18n",
                    message="国际化违规：检测到硬编码中文文本。请使用 $t('key') 进行国际化处理。",
                ))

        return violations

    # ── 幻觉防护检查 ────────────────────────────────────

    def verify_hallucination(self, file_path: str, content: str) -> list[Violation]:
        """检查 import 引用的模块是否在项目中实际存在。"""
        violations: list[Violation] = []
        file_dir = os.path.dirname(os.path.join(self.project_root, file_path))

        # 匹配相对路径 import（JS/TS）
        relative_import = re.compile(r"""(?:import|from)\s+['"](\.[^'"]+)['"]""")

        for line_no, line in enumerate(content.splitlines(), 1):
            for match in relative_import.finditer(line):
                module_path = match.group(1)
                resolved = self._resolve_module(file_dir, module_path)
                if not resolved:
                    violations.append(Violation(
                        file=file_path,
                        line=line_no,
                        type="hallucination",
                        message=(
                            f"幻觉防护违规：import 引用的模块 '{module_path}' 在项目中不存在。"
                            f"请确认模块路径是否正确，或先创建该模块。"
                        ),
                    ))

        return violations

    def _resolve_module(self, base_dir: str, module_path: str) -> bool:
        """尝试解析模块路径是否存在。"""
        full_path = os.path.normpath(os.path.join(base_dir, module_path))
        # 直接匹配
        if os.path.exists(full_path):
            return True
        # 尝试常见扩展名
        for ext in [".ts", ".tsx", ".js", ".jsx", ".vue", ".py", ".kt", "/index.ts", "/index.js"]:
            if os.path.exists(full_path + ext):
                return True
        return False

    # ── 自定义约束 ──────────────────────────────────────

    def _verify_custom(self, file_path: str, content: str) -> list[Violation]:
        """执行 customConstraints 中定义的自定义约束。"""
        custom = self.config.custom_constraints
        if not custom:
            return []

        violations: list[Violation] = []
        for constraint in custom:
            pattern = constraint.get("pattern", "")
            message = constraint.get("message", "自定义约束违规")
            file_pattern = constraint.get("filePattern", "*")
            constraint_type = constraint.get("type", "custom")

            if file_pattern != "*" and not self._match_glob(file_path, file_pattern):
                continue

            try:
                regex = re.compile(pattern)
            except re.error:
                continue

            for line_no, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    violations.append(Violation(
                        file=file_path,
                        line=line_no,
                        type=constraint_type,
                        message=f"{message}。请按照项目规范修改。",
                    ))

        return violations

    # ── 工具方法 ────────────────────────────────────────

    @staticmethod
    def _match_glob(file_path: str, pattern: str) -> bool:
        """简单的 glob 模式匹配（支持 *.ext 格式）。"""
        if pattern == "*":
            return True
        if pattern.startswith("*."):
            ext = pattern[1:]  # ".ext"
            return file_path.endswith(ext)
        return pattern in file_path
