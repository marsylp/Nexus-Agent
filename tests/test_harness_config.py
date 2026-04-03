"""ConfigLoader 单元测试 + 属性测试

测试 config.py 的配置加载、序列化/反序列化、深度合并、技术栈模板等功能。
"""
import json
import os
import tempfile

import pytest
from hypothesis import given, settings, strategies as st

from agent_core.harness.config import (
    HarnessConfig,
    TechStackConfig,
    config_from_dict,
    config_to_dict,
    generate_default_config,
    get_available_templates,
    get_tech_stack_template,
    load_config,
    _deep_merge,
    _DEFAULT_CONFIG,
)


# ── Hypothesis 策略 ─────────────────────────────────────

# 安全的文本策略：排除 null 字符和控制字符
_safe_text = st.text(
    min_size=1,
    max_size=30,
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S"),
        blacklist_characters="\x00",
    ),
)

# 用于生成任意 HarnessConfig 的策略
_harness_config_strategy = st.builds(
    HarnessConfig,
    tech_stack=st.sampled_from(
        ["vue3-ts", "react-ts", "python", "node-express", "android-kotlin", "custom"]
    ),
    src_dir=_safe_text,
    test_runner=st.text(min_size=0, max_size=50),
    layers=st.lists(_safe_text, min_size=0, max_size=5),
    naming=st.dictionaries(_safe_text, _safe_text, min_size=0, max_size=3),
    forbidden=st.just([]),
    security=st.just({
        "checkSecretLeak": True,
        "checkEval": True,
        "checkLocalStorage": True,
        "checkUrlToken": True,
        "checkXss": True,
    }),
    i18n=st.just({
        "enabled": False,
        "filePatterns": [],
        "ignorePatterns": [],
    }),
    custom_constraints=st.just([]),
)

# 用于生成部分用户配置字典的策略
_partial_config_strategy = st.fixed_dictionaries(
    {},
    optional={
        "techStack": st.sampled_from(
            ["vue3-ts", "react-ts", "python", "node-express", "android-kotlin", "custom"]
        ),
        "srcDir": _safe_text,
        "testRunner": st.text(min_size=0, max_size=50),
        "layers": st.lists(_safe_text, min_size=0, max_size=5),
        "naming": st.dictionaries(_safe_text, _safe_text, min_size=0, max_size=3),
    },
)


# ══════════════════════════════════════════════════════════
# 属性测试
# ══════════════════════════════════════════════════════════


class TestPropertyConfigRoundtrip:
    """Property 1: 配置往返一致性"""

    # Feature: harness-engineering-layer, Property 1: 配置往返一致性
    # **Validates: Requirements 1.1, 1.7**
    @given(config=_harness_config_strategy)
    @settings(max_examples=100)
    def test_config_roundtrip(self, config: HarnessConfig):
        """HarnessConfig 序列化为 JSON 字典再反序列化应产生等价对象。"""
        serialized = config_to_dict(config)
        # 确保可以 JSON 序列化/反序列化（模拟真实文件读写）
        json_str = json.dumps(serialized, ensure_ascii=False)
        deserialized_dict = json.loads(json_str)
        restored = config_from_dict(deserialized_dict)
        assert restored == config


class TestPropertyDeepMergeUserPriority:
    """Property 2: 配置深度合并保持用户优先"""

    # Feature: harness-engineering-layer, Property 2: 配置深度合并保持用户优先
    # **Validates: Requirements 1.3**
    @given(user_config=_partial_config_strategy)
    @settings(max_examples=100)
    def test_deep_merge_user_priority(self, user_config: dict):
        """用户配置字段优先，缺失字段使用默认值。"""
        merged = _deep_merge(_DEFAULT_CONFIG, user_config)

        # (a) 用户显式设置的字段值等于用户值
        for key, value in user_config.items():
            assert merged[key] == value, (
                f"用户字段 '{key}' 应为 {value!r}，实际为 {merged[key]!r}"
            )

        # (b) 用户未设置的字段值等于默认值
        for key in _DEFAULT_CONFIG:
            if key not in user_config:
                assert merged[key] == _DEFAULT_CONFIG[key], (
                    f"默认字段 '{key}' 应为 {_DEFAULT_CONFIG[key]!r}，实际为 {merged[key]!r}"
                )

        # (c) 结果包含所有必需的顶层字段
        for key in _DEFAULT_CONFIG:
            assert key in merged, f"合并结果缺少必需字段 '{key}'"


class TestPropertyTechStackTemplateCompleteness:
    """Property 29: 技术栈模板字段完整性"""

    # Feature: harness-engineering-layer, Property 29: 技术栈模板字段完整性
    # **Validates: Requirements 11.3**
    @given(
        template_name=st.sampled_from(
            ["vue3-ts", "react-ts", "python", "node-express", "android-kotlin"]
        )
    )
    @settings(max_examples=100)
    def test_tech_stack_template_completeness(self, template_name: str):
        """每种模板包含 src_dir、test_runner、layers、naming 且非空。"""
        template = get_tech_stack_template(template_name)
        assert template is not None, f"模板 '{template_name}' 不存在"

        # src_dir 非空字符串
        assert isinstance(template.src_dir, str) and len(template.src_dir) > 0, (
            f"模板 '{template_name}' 的 src_dir 应为非空字符串"
        )
        # test_runner 非空字符串
        assert isinstance(template.test_runner, str) and len(template.test_runner) > 0, (
            f"模板 '{template_name}' 的 test_runner 应为非空字符串"
        )
        # layers 非空列表
        assert isinstance(template.layers, list) and len(template.layers) > 0, (
            f"模板 '{template_name}' 的 layers 应为非空列表"
        )
        # naming 非空字典
        assert isinstance(template.naming, dict) and len(template.naming) > 0, (
            f"模板 '{template_name}' 的 naming 应为非空字典"
        )


# ══════════════════════════════════════════════════════════
# 单元测试
# ══════════════════════════════════════════════════════════


class TestLoadConfigNoFile:
    """测试配置文件不存在的场景。"""

    def test_no_file_returns_default(self, tmp_path):
        """无 .harnessrc.json 时返回默认配置。"""
        config = load_config(str(tmp_path))
        assert config.tech_stack == "custom"
        assert config.src_dir == "src"
        assert config.test_runner == ""
        assert config.layers == []
        assert config.naming == {}
        assert config.forbidden == []
        assert config.security["checkSecretLeak"] is True
        assert config.i18n["enabled"] is False
        assert config.custom_constraints == []


class TestLoadConfigInvalidJSON:
    """测试无效 JSON 的场景。"""

    def test_invalid_json_returns_default(self, tmp_path):
        """无效 JSON 时输出警告并返回默认配置。"""
        config_file = tmp_path / ".harnessrc.json"
        config_file.write_text("{invalid json content!!!", encoding="utf-8")
        config = load_config(str(tmp_path))
        assert config.tech_stack == "custom"
        assert config.src_dir == "src"

    def test_non_dict_json_returns_default(self, tmp_path):
        """JSON 顶层不是对象时返回默认配置。"""
        config_file = tmp_path / ".harnessrc.json"
        config_file.write_text('["not", "a", "dict"]', encoding="utf-8")
        config = load_config(str(tmp_path))
        assert config.tech_stack == "custom"


class TestLoadConfigPartial:
    """测试部分配置的场景。"""

    def test_partial_config_merges_defaults(self, tmp_path):
        """部分配置与默认值合并，用户值优先。"""
        config_file = tmp_path / ".harnessrc.json"
        config_file.write_text(
            json.dumps({"techStack": "react-ts", "srcDir": "app"}),
            encoding="utf-8",
        )
        config = load_config(str(tmp_path))
        assert config.tech_stack == "react-ts"
        assert config.src_dir == "app"
        # 未设置的字段使用默认值
        assert config.test_runner == ""
        assert config.layers == []
        assert config.security["checkSecretLeak"] is True

    def test_partial_security_deep_merges(self, tmp_path):
        """嵌套字典（security）深度合并。"""
        config_file = tmp_path / ".harnessrc.json"
        config_file.write_text(
            json.dumps({"security": {"checkEval": False}}),
            encoding="utf-8",
        )
        config = load_config(str(tmp_path))
        # 用户覆盖的字段
        assert config.security["checkEval"] is False
        # 未覆盖的字段保持默认
        assert config.security["checkSecretLeak"] is True
        assert config.security["checkXss"] is True


class TestLoadConfigFull:
    """测试完整配置的场景。"""

    def test_full_config_loads_correctly(self, tmp_path):
        """完整配置正确加载所有字段。"""
        full_config = {
            "techStack": "vue3-ts",
            "srcDir": "src",
            "testRunner": "npx vitest run",
            "layers": ["views", "components", "stores"],
            "naming": {"views": "^[A-Z].*View\\.vue$"},
            "forbidden": [{"pattern": "<button>", "message": "禁止原生 button"}],
            "security": {
                "checkSecretLeak": False,
                "checkEval": False,
                "checkLocalStorage": False,
                "checkUrlToken": False,
                "checkXss": False,
            },
            "i18n": {
                "enabled": True,
                "filePatterns": ["*.vue"],
                "ignorePatterns": ["console\\.log"],
            },
            "customConstraints": [{"type": "custom", "pattern": "TODO"}],
        }
        config_file = tmp_path / ".harnessrc.json"
        config_file.write_text(json.dumps(full_config), encoding="utf-8")
        config = load_config(str(tmp_path))

        assert config.tech_stack == "vue3-ts"
        assert config.src_dir == "src"
        assert config.test_runner == "npx vitest run"
        assert config.layers == ["views", "components", "stores"]
        assert config.naming == {"views": "^[A-Z].*View\\.vue$"}
        assert len(config.forbidden) == 1
        assert config.security["checkSecretLeak"] is False
        assert config.i18n["enabled"] is True
        assert len(config.custom_constraints) == 1


class TestConfigSerialization:
    """测试 config_to_dict / config_from_dict 序列化。"""

    def test_roundtrip_default_config(self):
        """默认配置往返一致。"""
        config = HarnessConfig()
        d = config_to_dict(config)
        restored = config_from_dict(d)
        assert restored == config

    def test_config_to_dict_keys(self):
        """序列化后的字典使用 camelCase 键名。"""
        config = HarnessConfig(tech_stack="python", src_dir="app")
        d = config_to_dict(config)
        assert "techStack" in d
        assert "srcDir" in d
        assert "testRunner" in d
        assert "customConstraints" in d


class TestDeepMerge:
    """测试 _deep_merge 函数。"""

    def test_flat_override(self):
        """扁平字段覆盖。"""
        base = {"a": 1, "b": 2}
        override = {"b": 3}
        assert _deep_merge(base, override) == {"a": 1, "b": 3}

    def test_nested_merge(self):
        """嵌套字典深度合并。"""
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 3, "c": 4}}
        result = _deep_merge(base, override)
        assert result == {"x": {"a": 1, "b": 3, "c": 4}}

    def test_base_unchanged(self):
        """合并不修改原始 base 字典。"""
        base = {"a": 1, "b": {"c": 2}}
        override = {"b": {"c": 3}}
        _deep_merge(base, override)
        assert base == {"a": 1, "b": {"c": 2}}

    def test_empty_override(self):
        """空 override 返回 base 的副本。"""
        base = {"a": 1}
        result = _deep_merge(base, {})
        assert result == {"a": 1}


class TestTechStackTemplates:
    """测试技术栈模板相关函数。"""

    def test_get_available_templates_returns_five(self):
        """至少返回 5 种模板。"""
        templates = get_available_templates()
        assert len(templates) >= 5
        assert "vue3-ts" in templates
        assert "react-ts" in templates
        assert "python" in templates
        assert "node-express" in templates
        assert "android-kotlin" in templates

    def test_get_nonexistent_template_returns_none(self):
        """不存在的模板返回 None。"""
        assert get_tech_stack_template("nonexistent") is None

    def test_generate_default_config_custom(self):
        """custom 技术栈生成最小化配置。"""
        d = generate_default_config("custom")
        assert d["techStack"] == "custom"
        assert d["layers"] == []
        assert d["naming"] == {}

    def test_generate_default_config_vue3(self):
        """vue3-ts 技术栈生成包含模板字段的配置。"""
        d = generate_default_config("vue3-ts")
        assert d["techStack"] == "vue3-ts"
        assert d["srcDir"] == "src"
        assert "views" in d["layers"]
        assert d["i18n"]["enabled"] is True

    def test_generate_default_config_unknown_falls_to_custom(self):
        """未知技术栈回退到 custom。"""
        d = generate_default_config("unknown-stack")
        assert d["techStack"] == "custom"
