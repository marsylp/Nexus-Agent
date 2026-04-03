"""HarnessLayer — 核心中间件调度器

协调 ConfigLoader、Verifier、MetricsCollector 等子模块，
通过 HookManager 的 preToolUse/postToolUse 事件注册回调。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .config import HarnessConfig, load_config
from .metrics import MetricsCollector
from .verifier import Verifier, Violation
from .output_guard import OutputGuard, OutputIssue

if TYPE_CHECKING:
    pass

# write 类工具名称集合
_WRITE_TOOLS = {"write_file", "shell", "run_python", "http_post"}

# 各违规类型的正确做法指导
_STEERING_GUIDANCE: dict[str, str] = {
    "dependency": "遵守层级依赖方向，只允许向下层依赖。例如 views 可以依赖 components，但 components 不应依赖 views。",
    "naming": "遵守命名规范。页面文件使用 PascalCase+View（如 LoginView.vue），组件使用 PascalCase+Component（如 ButtonComponent.vue）。",
    "forbidden": "避免使用禁止的模式。例如使用 ButtonComponent 替代原生 <button>，使用 InputComponent 替代原生 <input>。",
    "security": "遵守安全合规规则。不要硬编码密钥，避免使用 eval()，不要在 localStorage 中存储敏感数据，不要在 URL 中暴露 Token。",
    "i18n": "所有用户可见文本必须使用国际化函数 $t('key')，禁止硬编码中文文本。",
    "hallucination": "确保 import 引用的模块路径在项目中实际存在，避免引用不存在的文件或包。",
    "output_hallucination": "不要编造不存在的 URL、API 或库名。如需示例，使用 example.com。",
    "output_verbose": "对简单问题（问候、身份等）用 2-3 句话简洁回答，不要编造长篇内容。",
    "output_code_hallucination": "用户没有要求代码时，不要编造代码示例。用自然语言回答即可。",
}


class HarnessLayer:
    """Harness 驾驭层核心中间件。

    初始化失败时以 enabled=False 状态运行，不阻塞 Agent。
    """

    def __init__(self, agent: Any = None, cwd: str | None = None):
        self._agent = agent
        self._cwd = cwd or os.getcwd()
        self._enabled = False
        self._config: HarnessConfig | None = None
        self._verifier: Verifier | None = None
        self._metrics: MetricsCollector | None = None
        self._run_id: str = f"R{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        # 记录上一次拦截信息，用于 postToolUse 检测自修复
        self._last_violations: list[Violation] = []
        self._last_violation_file: str | None = None
        # Steering 增强阈值与冷却参数
        self._steering_threshold: int = 5
        self._steering_cooldown: int = 10

        try:
            self._config = load_config(self._cwd)
            self._verifier = Verifier(self._config, self._cwd)
            self._enabled = True
        except Exception as e:
            print(f"⚠️  HarnessLayer 初始化失败: {e}，以禁用状态运行", file=sys.stderr)
            self._enabled = False

        # OutputGuard — Agent 输出质量守卫
        self._output_guard = OutputGuard()
        # 上一次输出质量问题（供 Steering 增强使用）
        self._last_output_issues: list[OutputIssue] = []

        # MetricsCollector 初始化独立于主流程，失败不影响 HarnessLayer
        try:
            metrics_dir = os.path.join(self._cwd, ".harness", "metrics")
            self._metrics = MetricsCollector(metrics_dir)
        except Exception as e:
            print(f"⚠️  MetricsCollector 初始化失败: {e}，度量采集不可用", file=sys.stderr)
            self._metrics = None

    # ── 启用/禁用 ──────────────────────────────────────

    def enable(self) -> None:
        """启用约束检查。"""
        self._enabled = True

    def disable(self) -> None:
        """禁用约束检查。"""
        self._enabled = False

    @property
    def enabled(self) -> bool:
        """当前是否启用。"""
        return self._enabled

    # ── Hook 注册 ──────────────────────────────────────

    def register_hooks(self, hook_manager: Any) -> None:
        """向 HookManager 注册 preToolUse/postToolUse/agentStop 回调。"""
        hook_manager.on("preToolUse", self.on_pre_tool_use)
        hook_manager.on("postToolUse", self.on_post_tool_use)
        hook_manager.on("agentStop", self.on_agent_stop)

    # ── preToolUse 回调 ────────────────────────────────

    def on_pre_tool_use(self, **context: Any) -> str | bool | None:
        """preToolUse 回调：对 write 类工具执行约束检查。

        返回值:
            - None: 允许执行
            - False: 阻止执行（HookManager 会设置 allowed=False）
            - str: 阻止执行并返回违规详情
        """
        if not self._enabled or self._verifier is None:
            return None

        tool_name = context.get("tool_name", "")
        tool_args = context.get("tool_args", {})

        # 只拦截 write 类工具
        if tool_name not in _WRITE_TOOLS:
            return None

        # 提取文件路径和内容
        file_path = tool_args.get("path") or tool_args.get("file_path", "")
        content = tool_args.get("content", "")

        if not file_path or not content:
            return None

        violations = self._verifier.verify(file_path, content)

        if not violations:
            self._last_violations = []
            self._last_violation_file = None
            return None

        # 记录拦截信息（供 postToolUse 检测自修复）
        self._last_violations = violations
        self._last_violation_file = file_path

        # 记录拦截度量（需求 3.5）
        if self._metrics is not None:
            try:
                self._metrics.record_interception(self._run_id, violations)
            except Exception:
                pass  # 度量记录失败不影响主流程

        # 构建违规详情
        details = self._format_violations(violations)
        return details

    # ── postToolUse 回调 ───────────────────────────────

    def on_post_tool_use(self, **context: Any) -> None:
        """postToolUse 回调：检测自修复行为。"""
        if not self._enabled:
            return

        tool_name = context.get("tool_name", "")
        tool_args = context.get("tool_args", {})

        # 检测是否是对之前违规文件的重新写入（自修复）
        if self._last_violation_file and tool_name in _WRITE_TOOLS:
            file_path = tool_args.get("path") or tool_args.get("file_path", "")
            if file_path == self._last_violation_file:
                # Agent 尝试修复了之前的违规 — 记录自修复度量
                if self._metrics is not None:
                    try:
                        violation_types = [v.type for v in self._last_violations]
                        self._metrics.record_self_repair(
                            run_id=self._run_id,
                            failed_run_id=self._run_id,
                            success=True,
                            attempts=1,
                            fixed_tests=violation_types,
                            remaining_failures=[],
                        )
                    except Exception:
                        pass  # 度量记录失败不影响主流程
                self._last_violations = []
                self._last_violation_file = None

    # ── agentStop 回调 — 输出质量守卫 ─────────────────

    def on_agent_stop(self, **context: Any) -> None:
        """agentStop 回调：检查 Agent 回复的输出质量。

        这是 Harness 层最重要的创新之一（详见 docs/ARCHITECTURE.md §4.3）。
        传统 Agent 框架只管"写操作"的质量，不管"说话"的质量。
        OutputGuard 填补了这个盲区。

        检测幻觉、跑题、过度输出、代码幻觉等问题，
        发现问题时：
        1. 记录到度量系统（output_* 类型，与写操作违规统一管理）
        2. 注入修正提示到 Agent memory（影响下一轮对话）
        3. 终端显示质量警告（error 级别）
        4. 高频问题通过 Steering 增强闭环自动生成指导
        """
        if not self._enabled:
            return

        user_input = context.get("user_input", "")
        reply = context.get("reply", "")

        if not user_input or not reply:
            return

        issues = self._output_guard.check(user_input, reply)
        self._last_output_issues = issues

        if not issues:
            return

        # 记录到度量系统
        if self._metrics is not None:
            try:
                from .verifier import Violation
                violations = [
                    Violation(
                        file="<agent_output>",
                        line=None,
                        type="output_{}".format(issue.type),
                        message=issue.message,
                    )
                    for issue in issues
                    if issue.severity in ("error", "warn")
                ]
                if violations:
                    self._metrics.record_interception(self._run_id, violations)
            except Exception:
                pass

        # 生成修正提示并注入到 Agent memory
        correction = self._output_guard.build_correction_prompt(user_input, issues)
        if correction and self._agent:
            try:
                memory = getattr(self._agent, "memory", None)
                if memory and isinstance(memory, list):
                    memory.append({"role": "system", "content": correction})
            except Exception:
                pass

        # 在终端显示质量警告
        has_errors = any(i.severity == "error" for i in issues)
        if has_errors:
            try:
                from agent_core.ui import yellow, gray
                for issue in issues:
                    if issue.severity == "error":
                        sys.stderr.write("    {} {}\n".format(
                            yellow("!"), gray(issue.message[:80])))
                sys.stderr.flush()
            except ImportError:
                pass

    # ── Steering 增强 ─────────────────────────────────

    def get_steering_enhancement(self) -> str | None:
        """基于高频违规生成 Steering 增强内容。

        当某种违规类型拦截次数超过 _steering_threshold 时，自动生成 Steering 提示。
        当某种违规类型连续 _steering_cooldown 次运行未出现时，自动移除其 Steering。

        返回:
            Steering 增强内容字符串，无需增强时返回 None。
        """
        if self._metrics is None:
            return None

        try:
            summary = self._metrics.compute_summary()
        except Exception:
            return None

        if not summary.interceptions_by_type:
            return None

        # 读取拦截记录以获取历史示例和最近运行信息
        try:
            interceptions = self._metrics._read_records(
                self._metrics._interceptions_file
            )
        except Exception:
            interceptions = []

        # 收集所有 run_id 并按时间排序（用于冷却检测）
        all_run_ids: list[str] = []
        seen_run_ids: set[str] = set()
        for rec in interceptions:
            rid = rec.get("run_id", "")
            if rid and rid not in seen_run_ids:
                seen_run_ids.add(rid)
                all_run_ids.append(rid)

        # 获取最近 N 次运行的 run_id 集合（用于冷却判断）
        recent_run_ids = set(all_run_ids[-self._steering_cooldown:]) if all_run_ids else set()

        # 构建每种违规类型在最近运行中是否出现的映射
        type_in_recent: dict[str, bool] = {}
        for rec in interceptions:
            vtype = rec.get("violation_type", "")
            rid = rec.get("run_id", "")
            if vtype and rid in recent_run_ids:
                type_in_recent[vtype] = True

        # 筛选需要生成 Steering 的违规类型
        steering_sections: list[str] = []

        for vtype, count in summary.interceptions_by_type.items():
            # 未超过阈值，跳过
            if count < self._steering_threshold:
                continue

            # 冷却检测：如果最近 N 次运行中该类型未出现，自动移除
            if len(all_run_ids) >= self._steering_cooldown and not type_in_recent.get(vtype, False):
                continue

            # 收集该类型的历史示例（脱敏，最多 3 条）
            examples: list[str] = []
            for rec in interceptions:
                if rec.get("violation_type") == vtype and len(examples) < 3:
                    msg = rec.get("message", "")
                    # 脱敏：移除具体文件路径中的用户目录信息
                    file_path = rec.get("file_path", "")
                    basename = os.path.basename(file_path) if file_path else "unknown"
                    examples.append(f"  - 文件 `{basename}`: {msg}")

            # 生成该类型的 Steering 内容
            section_lines = [
                f"### ⚠️ 高频违规：{vtype}（已拦截 {count} 次）",
                "",
                f"**违规类型描述**：`{vtype}` 类型的约束违规在历史运行中频繁出现。",
                "",
            ]

            if examples:
                section_lines.append("**历史违规示例**：")
                section_lines.extend(examples)
                section_lines.append("")

            # 根据违规类型生成正确做法指导
            guidance = _STEERING_GUIDANCE.get(vtype, f"请遵守 `{vtype}` 类型的约束规则，避免重复违规。")
            section_lines.append(f"**正确做法**：{guidance}")
            section_lines.append("")

            steering_sections.append("\n".join(section_lines))

        if not steering_sections:
            return None

        header = (
            "# 🛡️ Harness Steering 增强提示\n\n"
            "以下是基于历史度量数据自动生成的指导内容，请在编码时注意避免这些高频违规：\n"
        )
        return header + "\n" + "\n".join(steering_sections)

    # ── 格式化 ─────────────────────────────────────────

    @staticmethod
    def _format_violations(violations: list[Violation]) -> str:
        """将违规列表格式化为可读的错误信息。"""
        lines = [f"🚫 Harness 约束检查发现 {len(violations)} 个违规：\n"]
        for i, v in enumerate(violations, 1):
            loc = f"第 {v.line} 行" if v.line else "文件级别"
            lines.append(f"  {i}. [{v.type}] {v.file} ({loc})")
            lines.append(f"     {v.message}")
        lines.append("\n请修复以上违规后重新提交。")
        return "\n".join(lines)
