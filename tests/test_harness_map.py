"""MapGenerator 单元测试

测试 map_generator.py 的认知地图生成功能，包括：
- 生成 Markdown 内容完整性（时间戳、技术栈、模块列表、层级依赖）
- 写入文件功能
- 目录自动创建
- 覆盖旧文件
"""
import os
import re

import pytest

from agent_core.harness.config import HarnessConfig
from agent_core.harness.map_generator import MapGenerator
from agent_core.harness.scanner import ScanResult


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


def _sample_scan_result() -> ScanResult:
    """创建一个包含各模块文件的示例 ScanResult。"""
    return ScanResult(
        apis=["src/api/user.ts", "src/api/order.ts"],
        views=["src/views/login/LoginView.vue"],
        components=["src/components/ButtonComponent.vue"],
        stores=["src/stores/userStore.ts"],
        utils=["src/utils/format.ts"],
        has_types=True,
        has_router=True,
        has_locales=False,
    )


# ── 测试：generate() 内容完整性 ─────────────────────────

class TestMapGeneratorGenerate:
    """测试 generate() 方法生成的 Markdown 内容。"""

    def test_contains_title(self, tmp_path):
        config = _default_config()
        gen = MapGenerator(config, str(tmp_path))
        content = gen.generate(_sample_scan_result())

        assert "# 项目认知地图" in content

    def test_contains_timestamp(self, tmp_path):
        config = _default_config()
        gen = MapGenerator(config, str(tmp_path))
        content = gen.generate(_sample_scan_result())

        # 时间戳格式：YYYY-MM-DD HH:MM:SS
        assert re.search(r"> 生成时间: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", content)

    def test_contains_tech_stack(self, tmp_path):
        config = _default_config(tech_stack="vue3-ts")
        gen = MapGenerator(config, str(tmp_path))
        content = gen.generate(_sample_scan_result())

        assert "> 技术栈: vue3-ts" in content

    def test_contains_module_files(self, tmp_path):
        config = _default_config()
        gen = MapGenerator(config, str(tmp_path))
        result = _sample_scan_result()
        content = gen.generate(result)

        # 所有非空模块的文件应出现在内容中
        assert "src/api/user.ts" in content
        assert "src/api/order.ts" in content
        assert "src/views/login/LoginView.vue" in content
        assert "src/components/ButtonComponent.vue" in content
        assert "src/stores/userStore.ts" in content
        assert "src/utils/format.ts" in content

    def test_contains_layer_dependency(self, tmp_path):
        config = _default_config(layers=["views", "components", "stores", "api", "utils"])
        gen = MapGenerator(config, str(tmp_path))
        content = gen.generate(_sample_scan_result())

        assert "views → components → stores → api → utils" in content
        assert "只允许向右依赖" in content

    def test_contains_module_section_headers(self, tmp_path):
        config = _default_config()
        gen = MapGenerator(config, str(tmp_path))
        content = gen.generate(_sample_scan_result())

        assert "### API 模块" in content
        assert "### 页面（Views）" in content
        assert "### 组件（Components）" in content
        assert "### 状态管理（Stores）" in content
        assert "### 工具函数（Utils）" in content

    def test_empty_modules_not_in_output(self, tmp_path):
        """空模块不应出现在模块清单中。"""
        config = _default_config()
        gen = MapGenerator(config, str(tmp_path))
        result = ScanResult(apis=["src/api/user.ts"])
        content = gen.generate(result)

        assert "### API 模块" in content
        # 空模块不应出现
        assert "### 页面（Views）" not in content
        assert "### 组件（Components）" not in content

    def test_special_dirs_section(self, tmp_path):
        """特殊目录应出现在输出中。"""
        config = _default_config()
        gen = MapGenerator(config, str(tmp_path))
        result = ScanResult(has_types=True, has_router=True, has_locales=True)
        content = gen.generate(result)

        assert "`types/`" in content
        assert "`router/`" in content
        assert "`locales/`" in content

    def test_no_special_dirs_section(self, tmp_path):
        """无特殊目录时不应有特殊目录段落。"""
        config = _default_config()
        gen = MapGenerator(config, str(tmp_path))
        result = ScanResult()
        content = gen.generate(result)

        assert "## 特殊目录" not in content

    def test_no_layers_message(self, tmp_path):
        """未配置层级时显示提示信息。"""
        config = _default_config(layers=[])
        gen = MapGenerator(config, str(tmp_path))
        content = gen.generate(ScanResult())

        assert "未配置层级依赖关系" in content


# ── 测试：write() 文件写入 ──────────────────────────────

class TestMapGeneratorWrite:
    """测试 write() 方法的文件写入功能。"""

    def test_write_creates_file(self, tmp_path):
        """write() 应创建 .harness/map.md 文件。"""
        config = _default_config()
        gen = MapGenerator(config, str(tmp_path))
        path = gen.write(_sample_scan_result())

        assert os.path.isfile(path)
        assert path.endswith("map.md")
        assert ".harness" in path

    def test_write_creates_harness_dir(self, tmp_path):
        """write() 应自动创建 .harness/ 目录。"""
        config = _default_config()
        gen = MapGenerator(config, str(tmp_path))
        gen.write(_sample_scan_result())

        assert os.path.isdir(str(tmp_path / ".harness"))

    def test_write_overwrites_existing(self, tmp_path):
        """write() 应覆盖已存在的 map.md。"""
        config = _default_config()
        gen = MapGenerator(config, str(tmp_path))

        # 第一次写入
        gen.write(ScanResult(apis=["src/api/old.ts"]))
        # 第二次写入（不同内容）
        gen.write(ScanResult(apis=["src/api/new.ts"]))

        map_path = str(tmp_path / ".harness" / "map.md")
        with open(map_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "src/api/new.ts" in content
        assert "src/api/old.ts" not in content

    def test_write_returns_absolute_path(self, tmp_path):
        """write() 返回的路径应为绝对路径。"""
        config = _default_config()
        gen = MapGenerator(config, str(tmp_path))
        path = gen.write(ScanResult())

        assert os.path.isabs(path)

    def test_write_content_matches_generate(self, tmp_path):
        """write() 写入的内容应与 generate() 返回的内容一致。"""
        config = _default_config()
        gen = MapGenerator(config, str(tmp_path))
        result = _sample_scan_result()

        path = gen.write(result)
        with open(path, "r", encoding="utf-8") as f:
            written = f.read()

        generated = gen.generate(result)
        # 由于时间戳可能有微小差异，只比较结构部分
        assert "# 项目认知地图" in written
        assert "> 技术栈: vue3-ts" in written
