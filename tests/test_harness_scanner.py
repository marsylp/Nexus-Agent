"""Scanner 单元测试

测试 scanner.py 的项目结构扫描功能，包括：
- 各模块扫描（API/Views/Components/Stores/Utils）
- 特殊目录检测（types/router/locales）
- 空目录和不存在目录的处理
- 技术栈适配（vue3-ts/react-ts/python/node-express/android-kotlin/custom）
"""
import os
import tempfile

import pytest

from agent_core.harness.config import HarnessConfig
from agent_core.harness.scanner import Scanner, ScanResult


# ── 辅助函数 ────────────────────────────────────────────

def _make_files(root: str, paths: list[str]) -> None:
    """在 root 下批量创建文件（自动创建父目录）。"""
    for p in paths:
        full = os.path.join(root, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write("")


def _default_config(**overrides) -> HarnessConfig:
    """创建带默认值的 HarnessConfig，可通过 overrides 覆盖。"""
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


# ── 测试：vue3-ts 技术栈扫描 ────────────────────────────

class TestScannerVue3Ts:
    """vue3-ts 技术栈的扫描测试。"""

    def test_scan_all_modules(self, tmp_path):
        """扫描包含所有模块的 vue3-ts 项目。"""
        _make_files(str(tmp_path), [
            "src/api/user.ts",
            "src/api/order.ts",
            "src/views/login/LoginView.vue",
            "src/components/ButtonComponent.vue",
            "src/stores/userStore.ts",
            "src/utils/format.ts",
        ])
        config = _default_config(tech_stack="vue3-ts", src_dir="src")
        scanner = Scanner(config, str(tmp_path))
        result = scanner.scan()

        assert len(result.apis) == 2
        assert len(result.views) == 1
        assert len(result.components) == 1
        assert len(result.stores) == 1
        assert len(result.utils) == 1
        # 路径应为相对于 project_root 的路径
        assert "src/api/user.ts" in result.apis
        assert "src/api/order.ts" in result.apis

    def test_scan_empty_src_dir(self, tmp_path):
        """源码目录存在但为空时，返回空列表。"""
        os.makedirs(str(tmp_path / "src"))
        config = _default_config(tech_stack="vue3-ts", src_dir="src")
        scanner = Scanner(config, str(tmp_path))
        result = scanner.scan()

        assert result.apis == []
        assert result.views == []
        assert result.components == []
        assert result.stores == []
        assert result.utils == []

    def test_scan_nonexistent_src_dir(self, tmp_path):
        """源码目录不存在时，返回空 ScanResult。"""
        config = _default_config(tech_stack="vue3-ts", src_dir="src")
        scanner = Scanner(config, str(tmp_path))
        result = scanner.scan()

        assert result == ScanResult()

    def test_detect_special_dirs(self, tmp_path):
        """检测 types/router/locales 特殊目录。"""
        _make_files(str(tmp_path), [
            "src/types/index.ts",
            "src/router/index.ts",
            "src/locales/zh.json",
        ])
        config = _default_config(tech_stack="vue3-ts", src_dir="src")
        scanner = Scanner(config, str(tmp_path))
        result = scanner.scan()

        assert result.has_types is True
        assert result.has_router is True
        assert result.has_locales is True

    def test_no_special_dirs(self, tmp_path):
        """无特殊目录时，布尔标志为 False。"""
        _make_files(str(tmp_path), ["src/api/user.ts"])
        config = _default_config(tech_stack="vue3-ts", src_dir="src")
        scanner = Scanner(config, str(tmp_path))
        result = scanner.scan()

        assert result.has_types is False
        assert result.has_router is False
        assert result.has_locales is False

    def test_scan_recursive_subdirs(self, tmp_path):
        """递归扫描子目录中的文件。"""
        _make_files(str(tmp_path), [
            "src/api/user.ts",
            "src/api/v2/order.ts",
            "src/api/v2/payment.ts",
        ])
        config = _default_config(tech_stack="vue3-ts", src_dir="src")
        scanner = Scanner(config, str(tmp_path))
        result = scanner.scan()

        assert len(result.apis) == 3
        assert "src/api/v2/order.ts" in result.apis


# ── 测试：react-ts 技术栈扫描 ───────────────────────────

class TestScannerReactTs:
    """react-ts 技术栈使用不同的目录约定。"""

    def test_scan_react_dirs(self, tmp_path):
        """react-ts 使用 services/pages/components/hooks/utils 目录。"""
        _make_files(str(tmp_path), [
            "src/services/api.ts",
            "src/pages/HomePage.tsx",
            "src/components/Button.tsx",
            "src/hooks/useAuth.ts",
            "src/utils/helpers.ts",
        ])
        config = _default_config(tech_stack="react-ts", src_dir="src")
        scanner = Scanner(config, str(tmp_path))
        result = scanner.scan()

        assert len(result.apis) == 1      # services -> apis
        assert len(result.views) == 1     # pages -> views
        assert len(result.components) == 1
        assert len(result.stores) == 1    # hooks -> stores
        assert len(result.utils) == 1


# ── 测试：python 技术栈扫描 ─────────────────────────────

class TestScannerPython:
    """python 技术栈使用 services/views/models/utils 目录。"""

    def test_scan_python_dirs(self, tmp_path):
        _make_files(str(tmp_path), [
            "app/services/user_service.py",
            "app/views/user_view.py",
            "app/models/user.py",
            "app/utils/helpers.py",
        ])
        config = _default_config(tech_stack="python", src_dir="app")
        scanner = Scanner(config, str(tmp_path))
        result = scanner.scan()

        assert len(result.apis) == 1      # services -> apis
        assert len(result.views) == 1
        assert len(result.components) == 1  # models -> components
        assert len(result.utils) == 1


# ── 测试：node-express 技术栈扫描 ───────────────────────

class TestScannerNodeExpress:
    """node-express 技术栈使用 routes/controllers/services/models/utils 目录。"""

    def test_scan_node_dirs(self, tmp_path):
        _make_files(str(tmp_path), [
            "src/routes/user.routes.ts",
            "src/controllers/user.controller.ts",
            "src/services/user.service.ts",
            "src/models/user.model.ts",
            "src/utils/logger.ts",
        ])
        config = _default_config(tech_stack="node-express", src_dir="src")
        scanner = Scanner(config, str(tmp_path))
        result = scanner.scan()

        assert len(result.apis) == 1      # routes -> apis
        assert len(result.views) == 1     # controllers -> views
        assert len(result.components) == 1  # services -> components
        assert len(result.stores) == 1    # models -> stores
        assert len(result.utils) == 1


# ── 测试：android-kotlin 技术栈扫描 ─────────────────────

class TestScannerAndroidKotlin:
    """android-kotlin 技术栈使用 glob 模式匹配深层目录。"""

    def test_scan_android_dirs(self, tmp_path):
        _make_files(str(tmp_path), [
            "app/src/main/java/com/example/ui/MainActivity.kt",
            "app/src/main/java/com/example/viewmodel/MainViewModel.kt",
            "app/src/main/java/com/example/repository/UserRepo.kt",
            "app/src/main/java/com/example/data/UserEntity.kt",
            "app/src/main/java/com/example/util/Extensions.kt",
        ])
        config = _default_config(
            tech_stack="android-kotlin",
            src_dir="app/src/main/java",
        )
        scanner = Scanner(config, str(tmp_path))
        result = scanner.scan()

        assert len(result.views) == 1       # ui -> views
        assert len(result.stores) == 1      # viewmodel -> stores
        assert len(result.apis) == 1        # repository -> apis
        assert len(result.components) == 1  # data -> components
        assert len(result.utils) == 1       # util -> utils


# ── 测试：custom 技术栈扫描 ─────────────────────────────

class TestScannerCustom:
    """custom/未知技术栈使用标准目录名。"""

    def test_scan_custom_uses_standard_dirs(self, tmp_path):
        _make_files(str(tmp_path), [
            "src/api/index.ts",
            "src/views/Home.vue",
            "src/components/Nav.vue",
            "src/stores/main.ts",
            "src/utils/helpers.ts",
        ])
        config = _default_config(tech_stack="custom", src_dir="src")
        scanner = Scanner(config, str(tmp_path))
        result = scanner.scan()

        assert len(result.apis) == 1
        assert len(result.views) == 1
        assert len(result.components) == 1
        assert len(result.stores) == 1
        assert len(result.utils) == 1

    def test_unknown_tech_stack_falls_back_to_custom(self, tmp_path):
        """未知技术栈名称也使用标准目录名。"""
        _make_files(str(tmp_path), ["src/api/test.ts"])
        config = _default_config(tech_stack="unknown-stack", src_dir="src")
        scanner = Scanner(config, str(tmp_path))
        result = scanner.scan()

        assert len(result.apis) == 1


# ── 测试：ScanResult 数据类 ─────────────────────────────

class TestScanResult:
    """ScanResult 数据类的默认值。"""

    def test_default_values(self):
        result = ScanResult()
        assert result.apis == []
        assert result.views == []
        assert result.components == []
        assert result.stores == []
        assert result.utils == []
        assert result.has_types is False
        assert result.has_router is False
        assert result.has_locales is False
