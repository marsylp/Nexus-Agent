"""PlanGenerator — 执行计划生成器

支持 feature/bugfix/refactor 三种任务类型和 standard/fast/strict 三种执行模式，
生成结构化 Markdown 执行计划并写入 `.harness/plans/{kebab-case-name}.md`。
"""

from __future__ import annotations

import os
import re
from datetime import datetime

from .config import HarnessConfig


# ── 执行模式步骤定义 ────────────────────────────────────

_MODE_STEPS: dict[str, list[str]] = {
    "standard": ["分析", "确认", "编码", "自检", "清单"],
    "fast": ["编码", "自检", "清单"],
    "strict": ["设计", "评审", "确认", "编码", "自检", "影响分析"],
}

# 每个步骤的子任务描述
_STEP_DETAILS: dict[str, list[str]] = {
    "分析": ["阅读认知地图", "分析影响范围"],
    "确认": ["确认需求理解正确", "确认实现方案"],
    "编码": ["实现功能", "约束检查通过"],
    "自检": ["运行测试", "代码审查"],
    "清单": ["验收标准逐项确认", "更新文档"],
    "设计": ["编写设计方案", "定义接口与数据结构"],
    "评审": ["设计方案评审", "风险评估"],
    "影响分析": ["分析变更影响范围", "确认无副作用"],
}

# 有效的任务类型
_VALID_TASK_TYPES = {"feature", "bugfix", "refactor"}

# 任务类型中文映射
_TASK_TYPE_LABELS: dict[str, str] = {
    "feature": "新功能开发",
    "bugfix": "Bug 修复",
    "refactor": "重构优化",
}


def _to_kebab_case(name: str) -> str:
    """将名称转换为 kebab-case 格式。

    处理规则：
    - 空白字符和下划线替换为连字符
    - 驼峰命名拆分为连字符分隔
    - 移除非字母数字和连字符的字符
    - 合并连续连字符，去除首尾连字符
    - 全部转小写
    """
    # 驼峰拆分：在大写字母前插入连字符
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", name)
    # 空白和下划线替换为连字符
    s = re.sub(r"[\s_]+", "-", s)
    # 移除非法字符
    s = re.sub(r"[^a-zA-Z0-9\-]", "", s)
    # 合并连续连字符
    s = re.sub(r"-{2,}", "-", s)
    # 去除首尾连字符，转小写
    return s.strip("-").lower()


class PlanGenerator:
    """执行计划生成器，生成结构化 Markdown 执行计划。"""

    def __init__(self, config: HarnessConfig, project_root: str) -> None:
        self._config = config
        self._project_root = os.path.abspath(project_root)

    # ── 公共接口 ────────────────────────────────────────

    def generate(
        self,
        name: str,
        task_type: str = "feature",
        mode: str = "standard",
        description: str = "",
    ) -> str:
        """生成执行计划 Markdown 并写入 `.harness/plans/{name}.md`，返回文件路径。

        Args:
            name: 计划名称（自动转换为 kebab-case 作为文件名）
            task_type: 任务类型，feature / bugfix / refactor
            mode: 执行模式，standard / fast / strict
            description: 需求描述

        Returns:
            生成的计划文件绝对路径
        """
        # 参数校验
        if task_type not in _VALID_TASK_TYPES:
            task_type = "feature"
        if mode not in _MODE_STEPS:
            mode = "standard"

        # 生成 Markdown 内容
        content = self._build_plan_content(name, task_type, mode, description)

        # 写入文件
        kebab_name = _to_kebab_case(name) or "unnamed-plan"
        plans_dir = os.path.join(self._project_root, ".harness", "plans")
        os.makedirs(plans_dir, exist_ok=True)

        file_path = os.path.join(plans_dir, f"{kebab_name}.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return file_path

    # ── 内部方法 ────────────────────────────────────────

    def _build_plan_content(
        self, name: str, task_type: str, mode: str, description: str
    ) -> str:
        """构建完整的执行计划 Markdown 内容。"""
        lines: list[str] = []

        # 标题
        lines.append(f"# 执行计划: {name}")
        lines.append("")

        # 元数据
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        type_label = _TASK_TYPE_LABELS.get(task_type, task_type)
        lines.append(f"- 类型: {type_label}")
        lines.append(f"- 模式: {mode}")
        lines.append(f"- 创建时间: {timestamp}")
        lines.append("")

        # 需求描述
        lines.append("## 需求描述")
        lines.append("")
        lines.append(description if description else "（暂无描述）")
        lines.append("")

        # 执行流程
        lines.append("## 执行流程")
        lines.append("")
        steps = _MODE_STEPS[mode]
        for i, step in enumerate(steps, 1):
            lines.append(f"### {i}. {step} [待执行]")
            details = _STEP_DETAILS.get(step, [])
            for detail in details:
                lines.append(f"- [ ] {detail}")
            lines.append("")

        # 验收标准
        lines.append("## 验收标准")
        lines.append("")
        lines.append("- [ ] 所有约束检查通过")
        lines.append("- [ ] 测试覆盖")
        lines.append("- [ ] 代码审查完成")
        if task_type == "bugfix":
            lines.append("- [ ] 回归测试通过")
        lines.append("")

        return "\n".join(lines)
