"""Verifier 约束验证引擎 — 单元测试

测试 verifier.py 的 6 种约束检查：dependency、naming、forbidden、security、i18n、hallucination。
"""
import os
import tempfile

import pytest

from agent_core.harness.config import HarnessConfig
from agent_core.harness.verifier import Verifier, Violation


# ── 辅助函数 ────────────────────────────────────────────

def _make_verifier(
    tmp_path,
    layers=None,
    naming=None,
    forbidden=None,
    security=None,
    i18n=None,
    custom_constraints=None,
) -> Verifier:
    """创建带自定义配置的 Verifier 实例。"""
    config = HarnessConfig(
        tech_stack="vue3-ts",
        src_dir="src",
        layers=layers or [],
        naming=naming or {},
        forbidden=forbidden or [],
        security=security or {
            "checkSecretLeak": True,
            "checkEval": True,
            "checkLocalStorage": True,
            "checkUrlToken": True,
            "checkXss": True,
        },
        i18n=i18n or {"enabled": False, "filePatterns": [], "ignorePatterns": []},
        custom_constraints=custom_constraints or [],
    )
    return Verifier(config, str(tmp_path))


# ══════════════════════════════════════════════════════════
# 依赖方向检查
# ══════════════════════════════════════════════════════════


class TestVerifyDependency:
    """测试层级依赖方向检查。"""

    def test_upward_dependency_detected(self, tmp_path):
        """向上层依赖应产生 dependency 违规。"""
        # layers: views(0) > components(1) > stores(2) > api(3) > utils(4)
        # components 依赖 views = 向上依赖 → 违规
        v = _make_verifier(tmp_path, layers=["views", "components", "stores", "api", "utils"])
        content = "import { LoginView } from '../views/LoginView'"
        violations = v.verify_dependency("components/ButtonComponent.vue", content)
        assert len(violations) >= 1
        assert violations[0].type == "dependency"

    def test_downward_dependency_allowed(self, tmp_path):
        """向下层依赖不应产生违规。"""
        v = _make_verifier(tmp_path, layers=["views", "components", "stores", "api", "utils"])
        content = "import { useStore } from '../stores/user'"
        violations = v.verify_dependency("views/LoginView.vue", content)
        assert len(violations) == 0

    def test_same_layer_allowed(self, tmp_path):
        """同层依赖不应产生违规。"""
        v = _make_verifier(tmp_path, layers=["views", "components", "stores"])
        content = "import { Header } from './HeaderComponent'"
        violations = v.verify_dependency("components/Footer.vue", content)
        assert len(violations) == 0

    def test_no_layers_config_skips(self, tmp_path):
        """无 layers 配置时跳过检查。"""
        v = _make_verifier(tmp_path, layers=[])
        content = "import anything from 'anywhere'"
        violations = v.verify_dependency("src/file.ts", content)
        assert len(violations) == 0


# ══════════════════════════════════════════════════════════
# 命名规范检查
# ══════════════════════════════════════════════════════════


class TestVerifyNaming:
    """测试命名规范检查。"""

    def test_invalid_name_detected(self, tmp_path):
        """不符合命名规范的文件应产生 naming 违规。"""
        v = _make_verifier(tmp_path, naming={"views": r"^[A-Z][a-zA-Z]+View\.vue$"})
        violations = v.verify_naming("views/login.vue")
        assert len(violations) == 1
        assert violations[0].type == "naming"

    def test_valid_name_passes(self, tmp_path):
        """符合命名规范的文件不应产生违规。"""
        v = _make_verifier(tmp_path, naming={"views": r"^[A-Z][a-zA-Z]+View\.vue$"})
        violations = v.verify_naming("views/LoginView.vue")
        assert len(violations) == 0

    def test_no_naming_config_skips(self, tmp_path):
        """无 naming 配置时跳过检查。"""
        v = _make_verifier(tmp_path, naming={})
        violations = v.verify_naming("views/anything.vue")
        assert len(violations) == 0

    def test_file_outside_named_dir_skips(self, tmp_path):
        """不在命名规则目录下的文件跳过检查。"""
        v = _make_verifier(tmp_path, naming={"views": r"^[A-Z].*View\.vue$"})
        violations = v.verify_naming("utils/helper.ts")
        assert len(violations) == 0


# ══════════════════════════════════════════════════════════
# 禁止项检查
# ══════════════════════════════════════════════════════════


class TestVerifyForbidden:
    """测试禁止项检查。"""

    def test_forbidden_pattern_detected(self, tmp_path):
        """匹配禁止模式应产生 forbidden 违规。"""
        v = _make_verifier(tmp_path, forbidden=[
            {"pattern": "<button[^>]*>", "message": "禁止原生 button", "filePattern": "*.vue"},
        ])
        content = '<template>\n  <button class="btn">Click</button>\n</template>'
        violations = v.verify_forbidden("src/views/LoginView.vue", content)
        assert len(violations) >= 1
        assert violations[0].type == "forbidden"

    def test_no_match_passes(self, tmp_path):
        """不匹配禁止模式不应产生违规。"""
        v = _make_verifier(tmp_path, forbidden=[
            {"pattern": "<button[^>]*>", "message": "禁止原生 button", "filePattern": "*.vue"},
        ])
        content = "<template>\n  <ButtonComponent />\n</template>"
        violations = v.verify_forbidden("src/views/LoginView.vue", content)
        assert len(violations) == 0

    def test_file_pattern_mismatch_skips(self, tmp_path):
        """文件模式不匹配时跳过检查。"""
        v = _make_verifier(tmp_path, forbidden=[
            {"pattern": "<button>", "message": "禁止", "filePattern": "*.vue"},
        ])
        content = "<button>Click</button>"
        violations = v.verify_forbidden("src/utils/helper.ts", content)
        assert len(violations) == 0

    def test_no_forbidden_config_skips(self, tmp_path):
        """无 forbidden 配置时跳过检查。"""
        v = _make_verifier(tmp_path, forbidden=[])
        violations = v.verify_forbidden("file.vue", "<button>Click</button>")
        assert len(violations) == 0


# ══════════════════════════════════════════════════════════
# 安全合规检查
# ══════════════════════════════════════════════════════════


class TestVerifySecurity:
    """测试安全合规检查。"""

    def test_secret_leak_detected(self, tmp_path):
        """密钥硬编码应产生 security 违规。"""
        v = _make_verifier(tmp_path)
        content = 'API_KEY = "sk-1234567890abcdef"'
        violations = v.verify_security("config.py", content)
        assert len(violations) >= 1
        assert all(vv.type == "security" for vv in violations)

    def test_eval_detected(self, tmp_path):
        """eval() 使用应产生 security 违规。"""
        v = _make_verifier(tmp_path)
        content = "const result = eval(userInput)"
        violations = v.verify_security("utils.js", content)
        assert len(violations) >= 1
        assert any("eval" in vv.message for vv in violations)

    def test_localstorage_sensitive_detected(self, tmp_path):
        """localStorage 存储敏感数据应产生 security 违规。"""
        v = _make_verifier(tmp_path)
        content = 'localStorage.setItem("password", pwd)'
        violations = v.verify_security("auth.ts", content)
        assert len(violations) >= 1

    def test_url_token_detected(self, tmp_path):
        """URL 中包含 token 应产生 security 违规。"""
        v = _make_verifier(tmp_path)
        content = 'const url = "https://api.example.com?token=abc123"'
        violations = v.verify_security("api.ts", content)
        assert len(violations) >= 1

    def test_xss_vhtml_detected(self, tmp_path):
        """v-html 使用应产生 security 违规。"""
        v = _make_verifier(tmp_path)
        content = '<div v-html="rawHtml"></div>'
        violations = v.verify_security("component.vue", content)
        assert len(violations) >= 1

    def test_disabled_check_skipped(self, tmp_path):
        """禁用的安全检查应跳过。"""
        v = _make_verifier(tmp_path, security={
            "checkSecretLeak": False,
            "checkEval": False,
            "checkLocalStorage": False,
            "checkUrlToken": False,
            "checkXss": False,
        })
        content = 'API_KEY = "sk-1234567890abcdef"\neval(x)\nlocalStorage.setItem("password", p)'
        violations = v.verify_security("file.ts", content)
        assert len(violations) == 0

    def test_safe_code_passes(self, tmp_path):
        """安全代码不应产生违规。"""
        v = _make_verifier(tmp_path)
        content = "const name = 'hello'\nconsole.log(name)"
        violations = v.verify_security("file.ts", content)
        assert len(violations) == 0


# ══════════════════════════════════════════════════════════
# 国际化检查
# ══════════════════════════════════════════════════════════


class TestVerifyI18n:
    """测试国际化检查。"""

    def test_hardcoded_chinese_detected(self, tmp_path):
        """Vue 模板中硬编码中文应产生 i18n 违规。"""
        v = _make_verifier(tmp_path, i18n={
            "enabled": True,
            "filePatterns": ["*.vue"],
            "ignorePatterns": [r"console\.log", r"//.*"],
        })
        content = '<template>\n  <div>登录</div>\n</template>\n<script></script>'
        violations = v.verify_i18n("LoginView.vue", content)
        assert len(violations) >= 1
        assert violations[0].type == "i18n"

    def test_i18n_function_passes(self, tmp_path):
        """使用 $t() 的文本不应产生违规。"""
        v = _make_verifier(tmp_path, i18n={
            "enabled": True,
            "filePatterns": ["*.vue"],
            "ignorePatterns": [],
        })
        content = "<template>\n  <div>{{ $t('login') }}</div>\n</template>"
        violations = v.verify_i18n("LoginView.vue", content)
        assert len(violations) == 0

    def test_disabled_i18n_skips(self, tmp_path):
        """i18n 禁用时跳过检查。"""
        v = _make_verifier(tmp_path, i18n={"enabled": False, "filePatterns": [], "ignorePatterns": []})
        content = "<template>\n  <div>中文文本</div>\n</template>"
        violations = v.verify_i18n("LoginView.vue", content)
        assert len(violations) == 0

    def test_comment_lines_ignored(self, tmp_path):
        """注释行中的中文应被忽略。"""
        v = _make_verifier(tmp_path, i18n={
            "enabled": True,
            "filePatterns": ["*.vue"],
            "ignorePatterns": [],
        })
        content = "<template>\n  <!-- 这是注释 -->\n</template>"
        violations = v.verify_i18n("LoginView.vue", content)
        assert len(violations) == 0


# ══════════════════════════════════════════════════════════
# 幻觉防护检查
# ══════════════════════════════════════════════════════════


class TestVerifyHallucination:
    """测试幻觉防护检查。"""

    def test_nonexistent_module_detected(self, tmp_path):
        """引用不存在的模块应产生 hallucination 违规。"""
        v = _make_verifier(tmp_path)
        content = "import { helper } from './nonexistent-module'"
        violations = v.verify_hallucination("src/views/Login.vue", content)
        assert len(violations) >= 1
        assert violations[0].type == "hallucination"

    def test_existing_module_passes(self, tmp_path):
        """引用存在的模块不应产生违规。"""
        # verify_hallucination 从文件所在目录解析相对路径
        # 文件在 src/views/Login.vue，import '../utils/helper' → src/utils/helper
        views_dir = tmp_path / "src" / "views"
        views_dir.mkdir(parents=True)
        utils_dir = tmp_path / "src" / "utils"
        utils_dir.mkdir(parents=True)
        (utils_dir / "helper.ts").write_text("export const helper = 1")

        v = _make_verifier(tmp_path)
        content = "import { helper } from '../utils/helper'"
        violations = v.verify_hallucination("src/views/Login.vue", content)
        assert len(violations) == 0

    def test_non_relative_import_skipped(self, tmp_path):
        """非相对路径 import 不检查。"""
        v = _make_verifier(tmp_path)
        content = "import Vue from 'vue'\nimport axios from 'axios'"
        violations = v.verify_hallucination("src/main.ts", content)
        assert len(violations) == 0


# ══════════════════════════════════════════════════════════
# verify 主方法
# ══════════════════════════════════════════════════════════


class TestVerifyMain:
    """测试 verify 主方法。"""

    def test_verify_aggregates_all_checks(self, tmp_path):
        """verify 应聚合所有检查类型的违规。"""
        v = _make_verifier(
            tmp_path,
            forbidden=[{"pattern": "<button>", "message": "禁止 button", "filePattern": "*"}],
        )
        content = '<button>Click</button>\neval(x)'
        violations = v.verify("file.vue", content)
        types = {vv.type for vv in violations}
        # 至少应包含 forbidden 和 security
        assert "forbidden" in types
        assert "security" in types

    def test_verify_returns_empty_for_clean_code(self, tmp_path):
        """干净代码不应产生任何违规。"""
        v = _make_verifier(tmp_path)
        content = "const x = 1\nconsole.log(x)"
        violations = v.verify("src/utils/helper.ts", content)
        assert len(violations) == 0

    def test_violation_fields_complete(self, tmp_path):
        """每个 Violation 的字段应完整。"""
        v = _make_verifier(tmp_path)
        content = "const result = eval(userInput)"
        violations = v.verify("file.ts", content)
        for vv in violations:
            assert isinstance(vv.file, str) and len(vv.file) > 0
            assert vv.type in ("dependency", "naming", "forbidden", "security", "i18n", "hallucination", "custom")
            assert isinstance(vv.message, str) and len(vv.message) > 0


# ══════════════════════════════════════════════════════════
# 自定义约束
# ══════════════════════════════════════════════════════════


class TestCustomConstraints:
    """测试自定义约束。"""

    def test_custom_constraint_detected(self, tmp_path):
        """自定义约束匹配应产生违规。"""
        v = _make_verifier(tmp_path, custom_constraints=[
            {"pattern": "TODO", "message": "禁止 TODO 注释", "type": "custom", "filePattern": "*"},
        ])
        content = "// TODO: fix this later"
        violations = v.verify("file.ts", content)
        custom_violations = [vv for vv in violations if vv.type == "custom"]
        assert len(custom_violations) >= 1

    def test_custom_constraint_no_match_passes(self, tmp_path):
        """自定义约束不匹配时不产生违规。"""
        v = _make_verifier(tmp_path, custom_constraints=[
            {"pattern": "FIXME", "message": "禁止 FIXME", "type": "custom", "filePattern": "*"},
        ])
        content = "const x = 1"
        violations = v.verify("file.ts", content)
        custom_violations = [vv for vv in violations if vv.type == "custom"]
        assert len(custom_violations) == 0
