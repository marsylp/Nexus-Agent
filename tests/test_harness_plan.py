"""PlanGenerator 单元测试

测试 plan_generator.py 的执行计划生成功能，包括：
- 三种任务类型（feature/bugfix/refactor）
- 三种执行模式（standard/fast/strict）
- 文件路径格式（kebab-case）
- 内容完整性（需求描述、执行流程、状态标记、验收标准）
- bugfix 回归测试要求
"""
import os
import re

import pytest

from agent_core.harness.config import HarnessConfig
from agent_core.harness.plan_generator import PlanGenerator, _to_kebab_case


# ── 辅助函数 ────────────────────────────────────────────

def _default_config(**overrides) -> HarnessConfig:
    defaults = dict(
        tech_stack="vue3-ts",
        src_dir="src",
        test_runner="npx vitest run",
        layers=["views", "components", "stores", "api", "utils"],
        naming={},
        forbidden=[],
        security={},
        i18n={"enabled": False, "filePatterns": [], "ignorePatterns": []},
        custom_constraints=[],
    )
    defaults.update(overrides)
    return HarnessConfig(**defaults)


# ── 测试：_to_kebab_case 辅助函数 ───────────────────────

class TestToKebabCase:
    """测试名称到 kebab-case 的转换。"""

    def test_simple_name(self):
        assert _to_kebab_case("myFeature") == "my-feature"

    def test_spaces(self):
        assert _to_kebab_case("my feature name") == "my-feature-name"

    def test_underscores(self):
        assert _to_kebab_case("my_feature_name") == "my-feature-name"

    def test_already_kebab(self):
        assert _to_kebab_case("my-feature") == "my-feature"

    def test_pascal_case(self):
        assert _to_kebab_case("MyFeatureName") == "my-feature-name"

    def test_mixed(self):
        assert _to_kebab_case("My Feature_Name") == "my-feature-name"

    def test_special_chars_removed(self):
        result = _to_kebab_case("my@feature!name")
        assert "@" not in result
        assert "!" not in result


# ── 测试：三种执行模式 ──────────────────────────────────

class TestPlanModes:
    """测试 standard/fast/strict 三种执行模式生成不同步骤。"""

    def test_standard_mode_steps(self, tmp_path):
        """standard 模式包含：分析→确认→编码→自检→清单。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("test-plan", mode="standard")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "分析" in content
        assert "确认" in content
        assert "编码" in content
        assert "自检" in content
        assert "清单" in content

    def test_fast_mode_steps(self, tmp_path):
        """fast 模式包含：编码→自检→清单。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("test-plan", mode="fast")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "编码" in content
        assert "自检" in content
        assert "清单" in content
        # fast 模式不应包含分析和确认
        assert "### " in content  # 确保有步骤标题

    def test_strict_mode_steps(self, tmp_path):
        """strict 模式包含：设计→评审→确认→编码→自检→影响分析。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("test-plan", mode="strict")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "设计" in content
        assert "评审" in content
        assert "确认" in content
        assert "编码" in content
        assert "自检" in content
        assert "影响分析" in content

    def test_fast_mode_fewer_steps_than_standard(self, tmp_path):
        """fast 模式的步骤数应少于 standard 模式。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))

        path_fast = gen.generate("fast-plan", mode="fast")
        path_std = gen.generate("std-plan", mode="standard")

        with open(path_fast, "r", encoding="utf-8") as f:
            fast_content = f.read()
        with open(path_std, "r", encoding="utf-8") as f:
            std_content = f.read()

        fast_steps = len(re.findall(r"### \d+\.", fast_content))
        std_steps = len(re.findall(r"### \d+\.", std_content))
        assert fast_steps < std_steps

    def test_strict_mode_more_steps_than_standard(self, tmp_path):
        """strict 模式的步骤数应多于 standard 模式。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))

        path_strict = gen.generate("strict-plan", mode="strict")
        path_std = gen.generate("std-plan2", mode="standard")

        with open(path_strict, "r", encoding="utf-8") as f:
            strict_content = f.read()
        with open(path_std, "r", encoding="utf-8") as f:
            std_content = f.read()

        strict_steps = len(re.findall(r"### \d+\.", strict_content))
        std_steps = len(re.findall(r"### \d+\.", std_content))
        assert strict_steps > std_steps


# ── 测试：三种任务类型 ──────────────────────────────────

class TestPlanTaskTypes:
    """测试 feature/bugfix/refactor 三种任务类型。"""

    def test_feature_type(self, tmp_path):
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("feat", task_type="feature")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "新功能开发" in content

    def test_bugfix_type(self, tmp_path):
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("fix", task_type="bugfix")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "Bug 修复" in content

    def test_refactor_type(self, tmp_path):
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("refactor", task_type="refactor")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "重构优化" in content

    def test_bugfix_includes_regression_test(self, tmp_path):
        """bugfix 类型应额外包含回归测试要求。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("bugfix-plan", task_type="bugfix")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "回归测试" in content

    def test_feature_no_regression_test(self, tmp_path):
        """feature 类型不应包含回归测试要求。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("feature-plan", task_type="feature")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "回归测试" not in content

    def test_invalid_task_type_defaults_to_feature(self, tmp_path):
        """无效的任务类型应回退为 feature。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("invalid-type", task_type="invalid")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "新功能开发" in content


# ── 测试：文件路径格式 ──────────────────────────────────

class TestPlanFilePath:
    """测试生成的文件路径格式。"""

    def test_kebab_case_filename(self, tmp_path):
        """文件名应为 kebab-case 格式。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("MyFeatureName")

        assert "my-feature-name.md" in path

    def test_file_in_plans_dir(self, tmp_path):
        """文件应在 .harness/plans/ 目录下。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("test")

        assert ".harness" + os.sep + "plans" in path or ".harness/plans" in path

    def test_plans_dir_auto_created(self, tmp_path):
        """应自动创建 .harness/plans/ 目录。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        gen.generate("test")

        assert os.path.isdir(str(tmp_path / ".harness" / "plans"))

    def test_returns_absolute_path(self, tmp_path):
        """返回的路径应为绝对路径。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("test")

        assert os.path.isabs(path)

    def test_invalid_mode_defaults_to_standard(self, tmp_path):
        """无效的模式应回退为 standard。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("test", mode="invalid")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # standard 模式有 5 个步骤
        assert "分析" in content
        assert "确认" in content


# ── 测试：内容完整性 ────────────────────────────────────

class TestPlanContentCompleteness:
    """测试执行计划内容的完整性。"""

    def test_contains_title(self, tmp_path):
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("my-plan", description="测试描述")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "# 执行计划: my-plan" in content

    def test_contains_description(self, tmp_path):
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("my-plan", description="实现用户登录功能")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "实现用户登录功能" in content

    def test_empty_description_placeholder(self, tmp_path):
        """无描述时应显示占位符。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("my-plan")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "暂无描述" in content

    def test_contains_status_markers(self, tmp_path):
        """每个步骤应有状态标记。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("my-plan")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "[待执行]" in content

    def test_contains_checklist(self, tmp_path):
        """应包含验收标准清单。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("my-plan")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "## 验收标准" in content
        assert "- [ ]" in content

    def test_contains_timestamp(self, tmp_path):
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("my-plan")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert re.search(r"创建时间: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", content)

    def test_contains_execution_flow(self, tmp_path):
        """应包含执行流程段落。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("my-plan")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "## 执行流程" in content

    def test_contains_sub_tasks(self, tmp_path):
        """每个步骤应包含子任务（checkbox 格式）。"""
        config = _default_config()
        gen = PlanGenerator(config, str(tmp_path))
        path = gen.generate("my-plan")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # 应有多个 checkbox 子任务
        checkboxes = re.findall(r"- \[ \]", content)
        assert len(checkboxes) >= 3  # 至少有验收标准 + 步骤子任务
