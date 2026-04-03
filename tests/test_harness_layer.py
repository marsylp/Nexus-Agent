"""HarnessLayer 核心中间件 — 单元测试

测试 layer.py 的 preToolUse/postToolUse 回调、enable/disable 切换、
初始化失败优雅降级等功能。
"""
import os
import json

import pytest

from agent_core.harness.config import HarnessConfig
from agent_core.harness.verifier import Verifier, Violation
from agent_core.harness.layer import HarnessLayer


# ── 辅助：模拟 HookManager ──────────────────────────────

class FakeHookManager:
    """模拟 HookManager，记录注册的回调。"""

    def __init__(self):
        self.callbacks: dict[str, list] = {}

    def on(self, event: str, callback):
        self.callbacks.setdefault(event, []).append(callback)


# ══════════════════════════════════════════════════════════
# 初始化与启用/禁用
# ══════════════════════════════════════════════════════════


class TestHarnessLayerInit:
    """测试 HarnessLayer 初始化。"""

    def test_init_with_valid_cwd(self, tmp_path):
        """有效目录初始化后应为启用状态。"""
        layer = HarnessLayer(cwd=str(tmp_path))
        assert layer.enabled is True

    def test_init_creates_metrics_dir(self, tmp_path):
        """初始化应创建 .harness/metrics/ 目录。"""
        layer = HarnessLayer(cwd=str(tmp_path))
        metrics_dir = tmp_path / ".harness" / "metrics"
        assert metrics_dir.is_dir()

    def test_enable_disable_toggle(self, tmp_path):
        """enable/disable 应正确切换状态。"""
        layer = HarnessLayer(cwd=str(tmp_path))
        assert layer.enabled is True

        layer.disable()
        assert layer.enabled is False

        layer.enable()
        assert layer.enabled is True


class TestHarnessLayerHookRegistration:
    """测试 Hook 注册。"""

    def test_register_hooks(self, tmp_path):
        """register_hooks 应注册 preToolUse 和 postToolUse 回调。"""
        layer = HarnessLayer(cwd=str(tmp_path))
        hm = FakeHookManager()
        layer.register_hooks(hm)
        assert "preToolUse" in hm.callbacks
        assert "postToolUse" in hm.callbacks
        assert len(hm.callbacks["preToolUse"]) == 1
        assert len(hm.callbacks["postToolUse"]) == 1


# ══════════════════════════════════════════════════════════
# preToolUse 回调
# ══════════════════════════════════════════════════════════


class TestOnPreToolUse:
    """测试 preToolUse 回调。"""

    def test_non_write_tool_allowed(self, tmp_path):
        """非 write 类工具应直接放行。"""
        layer = HarnessLayer(cwd=str(tmp_path))
        result = layer.on_pre_tool_use(tool_name="read_file", tool_args={"path": "file.ts"})
        assert result is None

    def test_write_tool_clean_content_allowed(self, tmp_path):
        """write 类工具写入干净内容应放行。"""
        layer = HarnessLayer(cwd=str(tmp_path))
        result = layer.on_pre_tool_use(
            tool_name="write_file",
            tool_args={"path": "src/utils/helper.ts", "content": "const x = 1"},
        )
        assert result is None

    def test_write_tool_violation_blocked(self, tmp_path):
        """write 类工具写入违规内容应被拦截。"""
        # 创建带禁止项配置的环境
        config = {
            "forbidden": [{"pattern": "<button>", "message": "禁止原生 button", "filePattern": "*"}],
        }
        config_file = tmp_path / ".harnessrc.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        layer = HarnessLayer(cwd=str(tmp_path))
        result = layer.on_pre_tool_use(
            tool_name="write_file",
            tool_args={"path": "src/views/Login.vue", "content": "<button>Click</button>"},
        )
        assert result is not None
        assert isinstance(result, str)
        assert "违规" in result or "forbidden" in result.lower()

    def test_disabled_layer_allows_all(self, tmp_path):
        """禁用状态下所有操作应放行。"""
        config = {
            "forbidden": [{"pattern": "<button>", "message": "禁止", "filePattern": "*"}],
        }
        config_file = tmp_path / ".harnessrc.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        layer = HarnessLayer(cwd=str(tmp_path))
        layer.disable()
        result = layer.on_pre_tool_use(
            tool_name="write_file",
            tool_args={"path": "file.vue", "content": "<button>Click</button>"},
        )
        assert result is None

    def test_empty_content_allowed(self, tmp_path):
        """空内容应放行。"""
        layer = HarnessLayer(cwd=str(tmp_path))
        result = layer.on_pre_tool_use(
            tool_name="write_file",
            tool_args={"path": "file.ts", "content": ""},
        )
        assert result is None

    def test_no_path_allowed(self, tmp_path):
        """无文件路径应放行。"""
        layer = HarnessLayer(cwd=str(tmp_path))
        result = layer.on_pre_tool_use(
            tool_name="write_file",
            tool_args={"content": "some content"},
        )
        assert result is None


# ══════════════════════════════════════════════════════════
# postToolUse 回调
# ══════════════════════════════════════════════════════════


class TestOnPostToolUse:
    """测试 postToolUse 回调。"""

    def test_post_tool_use_no_error(self, tmp_path):
        """postToolUse 正常调用不应抛出异常。"""
        layer = HarnessLayer(cwd=str(tmp_path))
        # 不应抛出异常
        layer.on_post_tool_use(tool_name="write_file", tool_args={"path": "file.ts"})

    def test_disabled_post_tool_use_noop(self, tmp_path):
        """禁用状态下 postToolUse 应为空操作。"""
        layer = HarnessLayer(cwd=str(tmp_path))
        layer.disable()
        layer.on_post_tool_use(tool_name="write_file", tool_args={"path": "file.ts"})


# ══════════════════════════════════════════════════════════
# 拦截后度量记录
# ══════════════════════════════════════════════════════════


class TestInterceptionMetrics:
    """测试拦截后度量记录。"""

    def test_interception_creates_metrics_record(self, tmp_path):
        """拦截操作后应在 metrics 目录创建记录。"""
        config = {
            "forbidden": [{"pattern": "eval\\(", "message": "禁止 eval", "filePattern": "*"}],
        }
        config_file = tmp_path / ".harnessrc.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        layer = HarnessLayer(cwd=str(tmp_path))
        layer.on_pre_tool_use(
            tool_name="write_file",
            tool_args={"path": "file.ts", "content": "eval(x)"},
        )

        # 检查度量文件是否存在且有内容
        interceptions_file = tmp_path / ".harness" / "metrics" / "interceptions.jsonl"
        assert interceptions_file.is_file()
        lines = interceptions_file.read_text().strip().split("\n")
        assert len(lines) >= 1
        record = json.loads(lines[0])
        assert "violation_type" in record
        assert "file_path" in record


# ══════════════════════════════════════════════════════════
# Steering 增强
# ══════════════════════════════════════════════════════════


class TestSteeringEnhancement:
    """测试 Steering 增强功能。"""

    def test_no_metrics_returns_none(self, tmp_path):
        """无度量数据时返回 None。"""
        layer = HarnessLayer(cwd=str(tmp_path))
        result = layer.get_steering_enhancement()
        assert result is None

    def test_below_threshold_returns_none(self, tmp_path):
        """低于阈值时返回 None。"""
        layer = HarnessLayer(cwd=str(tmp_path))
        # 记录少量拦截（低于默认阈值 5）
        violations = [Violation(file="f.ts", line=1, type="security", message="test")]
        for _ in range(3):
            layer._metrics.record_interception("R001", violations)
        result = layer.get_steering_enhancement()
        assert result is None

    def test_above_threshold_returns_steering(self, tmp_path):
        """超过阈值时应返回 Steering 内容。"""
        layer = HarnessLayer(cwd=str(tmp_path))
        violations = [Violation(file="f.ts", line=1, type="security", message="eval 违规")]
        for i in range(6):
            layer._metrics.record_interception(f"R{i:03d}", violations)
        result = layer.get_steering_enhancement()
        assert result is not None
        assert "security" in result


# ══════════════════════════════════════════════════════════
# 属性测试
# ══════════════════════════════════════════════════════════

import tempfile
from hypothesis import given, settings, HealthCheck, strategies as st


# ── Hypothesis 策略 ─────────────────────────────────────

# 违规类型
_VIOLATION_TYPES = ["dependency", "naming", "forbidden", "security", "i18n", "hallucination"]

# 安全文本策略
_safe_text = st.text(
    min_size=1,
    max_size=30,
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S"), blacklist_characters="\x00"),
)

# 生成非空 Violation 列表的策略
_violations_strategy = st.lists(
    st.builds(
        Violation,
        file=_safe_text,
        line=st.one_of(st.none(), st.integers(min_value=1, max_value=9999)),
        type=st.sampled_from(_VIOLATION_TYPES),
        message=_safe_text,
    ),
    min_size=1,
    max_size=5,
)

# 生成禁止项配置的策略（用于触发 forbidden 违规）
_forbidden_pattern_strategy = st.fixed_dictionaries({
    "pattern": st.sampled_from(["<button>", "<input>", "eval\\(", "console\\.log"]),
    "message": _safe_text,
    "filePattern": st.just("*"),
})


class TestPropertyInterceptionConsistency:
    """Property 11: 拦截与放行的一致性

    Violation 非空时阻止，为空时放行。
    """

    # Feature: harness-engineering-layer, Property 11: 拦截与放行的一致性
    # **Validates: Requirements 3.3, 3.4**
    @given(
        violations=_violations_strategy,
    )
    @settings(max_examples=100)
    def test_non_empty_violations_block(self, violations):
        """当 Verifier 返回非空 Violation 列表时，on_pre_tool_use 应阻止执行。"""
        td = tempfile.mkdtemp()
        layer = HarnessLayer(cwd=td)

        # 用 mock Verifier 注入指定的违规列表
        original_verify = layer._verifier.verify
        layer._verifier.verify = lambda fp, c: violations

        result = layer.on_pre_tool_use(
            tool_name="write_file",
            tool_args={"path": "src/test.ts", "content": "some content"},
        )

        # 非空违规 → 应返回非 None 的字符串（阻止执行）
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

        # 恢复
        layer._verifier.verify = original_verify

    # Feature: harness-engineering-layer, Property 11: 拦截与放行的一致性
    # **Validates: Requirements 3.3, 3.4**
    @given(
        file_path=_safe_text,
        content=_safe_text,
    )
    @settings(max_examples=100)
    def test_empty_violations_allow(self, file_path, content):
        """当 Verifier 返回空 Violation 列表时，on_pre_tool_use 应允许执行。"""
        td = tempfile.mkdtemp()
        layer = HarnessLayer(cwd=td)

        # 用 mock Verifier 注入空违规列表
        layer._verifier.verify = lambda fp, c: []

        result = layer.on_pre_tool_use(
            tool_name="write_file",
            tool_args={"path": file_path, "content": content},
        )

        # 空违规 → 应返回 None（允许执行）
        assert result is None


class TestPropertyEnableDisableToggle:
    """Property 13: 启用/禁用切换

    disable 后不拦截，enable 后恢复。
    """

    # Feature: harness-engineering-layer, Property 13: 启用/禁用切换
    # **Validates: Requirements 3.7**
    @given(
        violations=_violations_strategy,
    )
    @settings(max_examples=100)
    def test_disable_then_enable_toggle(self, violations):
        """disable 后即使有违规也放行，enable 后恢复拦截。"""
        td = tempfile.mkdtemp()
        layer = HarnessLayer(cwd=td)

        # 注入会产生违规的 mock Verifier
        layer._verifier.verify = lambda fp, c: violations

        tool_ctx = {
            "tool_name": "write_file",
            "tool_args": {"path": "src/test.ts", "content": "some content"},
        }

        # 1. 启用状态 → 应拦截
        layer.enable()
        result_enabled = layer.on_pre_tool_use(**tool_ctx)
        assert result_enabled is not None, "启用状态下有违规应拦截"

        # 2. 禁用状态 → 应放行
        layer.disable()
        result_disabled = layer.on_pre_tool_use(**tool_ctx)
        assert result_disabled is None, "禁用状态下应放行所有操作"

        # 3. 重新启用 → 应恢复拦截
        layer.enable()
        result_re_enabled = layer.on_pre_tool_use(**tool_ctx)
        assert result_re_enabled is not None, "重新启用后有违规应恢复拦截"

    # Feature: harness-engineering-layer, Property 13: 启用/禁用切换
    # **Validates: Requirements 3.7**
    @given(
        toggle_sequence=st.lists(st.booleans(), min_size=1, max_size=20),
    )
    @settings(max_examples=100)
    def test_arbitrary_toggle_sequence(self, toggle_sequence):
        """任意 enable/disable 切换序列后，行为应与最终状态一致。"""
        td = tempfile.mkdtemp()
        layer = HarnessLayer(cwd=td)

        # 注入固定违规
        fixed_violations = [Violation(file="f.ts", line=1, type="forbidden", message="test")]
        layer._verifier.verify = lambda fp, c: fixed_violations

        tool_ctx = {
            "tool_name": "write_file",
            "tool_args": {"path": "src/test.ts", "content": "some content"},
        }

        # 执行切换序列
        for should_enable in toggle_sequence:
            if should_enable:
                layer.enable()
            else:
                layer.disable()

        # 最终状态决定行为
        result = layer.on_pre_tool_use(**tool_ctx)
        final_enabled = toggle_sequence[-1]  # 最后一次操作决定状态

        if final_enabled:
            assert result is not None, "最终启用状态下有违规应拦截"
        else:
            assert result is None, "最终禁用状态下应放行"
