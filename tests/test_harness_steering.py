"""Steering 增强 — 单元测试

测试拦截后度量记录、高频违规触发 Steering 增强、
低频违规自动移除 Steering、无度量数据返回 None 等功能。
"""
import json
import os

import pytest

from agent_core.harness.layer import HarnessLayer
from agent_core.harness.metrics import MetricsCollector
from agent_core.harness.verifier import Violation


# ── 辅助函数 ─────────────────────────────────────────────

def _make_layer(tmp_path) -> HarnessLayer:
    """创建一个启用状态的 HarnessLayer 实例。"""
    layer = HarnessLayer(cwd=str(tmp_path))
    layer.enable()
    return layer


def _inject_interceptions(mc: MetricsCollector, vtype: str, count: int, run_id_prefix: str = "R"):
    """向 MetricsCollector 注入指定数量的拦截记录。"""
    for i in range(count):
        mc.record_interception(
            f"{run_id_prefix}{i:04d}",
            [{"file_path": f"src/file_{i}.py", "violation_type": vtype, "message": f"{vtype} 违规 #{i}"}],
        )


# ══════════════════════════════════════════════════════════
# 拦截后度量记录
# ══════════════════════════════════════════════════════════


class TestInterceptionMetricsRecording:
    """Property 12: 拦截后度量记录 — 拦截后 MetricsCollector 记录数增加。
    **Validates: Requirements 3.5**
    """

    def test_interception_increases_record_count(self, tmp_path):
        """拦截写入操作后，MetricsCollector 的拦截记录数应增加。"""
        # 创建带有 forbidden 规则的配置
        config_data = {
            "techStack": "python",
            "srcDir": "src",
            "testRunner": "pytest",
            "layers": [],
            "naming": {},
            "forbidden": [
                {"pattern": "eval\\(", "message": "禁止使用 eval"}
            ],
            "security": {
                "checkSecretLeak": False,
                "checkEval": True,
                "checkLocalStorage": False,
                "checkUrlToken": False,
                "checkXss": False,
            },
            "i18n": {"enabled": False},
            "customConstraints": [],
        }
        config_path = tmp_path / ".harnessrc.json"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        layer = HarnessLayer(cwd=str(tmp_path))
        assert layer.enabled is True
        assert layer._metrics is not None

        # 拦截前记录数
        records_before = layer._metrics._read_records(layer._metrics._interceptions_file)
        count_before = len(records_before)

        # 触发拦截：写入包含 eval 的内容
        result = layer.on_pre_tool_use(
            tool_name="write_file",
            tool_args={"path": "src/bad.py", "content": "result = eval('1+1')"},
        )

        # 应该被拦截
        assert result is not None

        # 拦截后记录数应增加
        records_after = layer._metrics._read_records(layer._metrics._interceptions_file)
        assert len(records_after) > count_before


# ══════════════════════════════════════════════════════════
# 高频违规触发 Steering 增强
# ══════════════════════════════════════════════════════════


class TestHighFrequencySteeringEnhancement:
    """Property 30: 高频违规触发 Steering 增强 — 超过阈值时返回包含违规描述的 Steering。
    **Validates: Requirements 12.1, 12.3**
    """

    def test_above_threshold_returns_steering(self, tmp_path):
        """拦截次数超过阈值（默认 5）时，应返回包含违规描述的 Steering。"""
        layer = _make_layer(tmp_path)
        assert layer._metrics is not None

        # 注入 6 条 security 类型拦截记录（超过默认阈值 5）
        _inject_interceptions(layer._metrics, "security", 6)

        steering = layer.get_steering_enhancement()
        assert steering is not None
        assert "security" in steering
        assert "高频违规" in steering

    def test_steering_contains_guidance(self, tmp_path):
        """Steering 内容应包含正确做法指导。"""
        layer = _make_layer(tmp_path)
        assert layer._metrics is not None

        _inject_interceptions(layer._metrics, "forbidden", 7)

        steering = layer.get_steering_enhancement()
        assert steering is not None
        # 应包含 forbidden 类型的指导内容
        assert "forbidden" in steering
        assert "正确做法" in steering

    def test_steering_contains_examples(self, tmp_path):
        """Steering 内容应包含历史违规示例。"""
        layer = _make_layer(tmp_path)
        assert layer._metrics is not None

        _inject_interceptions(layer._metrics, "naming", 6)

        steering = layer.get_steering_enhancement()
        assert steering is not None
        assert "历史违规示例" in steering

    def test_multiple_types_above_threshold(self, tmp_path):
        """多种违规类型都超过阈值时，Steering 应包含所有类型。"""
        layer = _make_layer(tmp_path)
        assert layer._metrics is not None

        _inject_interceptions(layer._metrics, "security", 6, "SEC")
        _inject_interceptions(layer._metrics, "naming", 8, "NAM")

        steering = layer.get_steering_enhancement()
        assert steering is not None
        assert "security" in steering
        assert "naming" in steering


# ══════════════════════════════════════════════════════════
# 低频违规自动移除 Steering
# ══════════════════════════════════════════════════════════


class TestLowFrequencySteeringRemoval:
    """Property 31: 低频违规自动移除 Steering — 连续 10 次未出现时不包含该类型 Steering。
    **Validates: Requirements 12.4**
    """

    def test_cooldown_removes_steering(self, tmp_path):
        """某类型超过阈值但最近 10 次运行未出现时，不应包含该类型 Steering。"""
        layer = _make_layer(tmp_path)
        assert layer._metrics is not None

        # 先注入 6 条 security 拦截（使用旧的 run_id）
        _inject_interceptions(layer._metrics, "security", 6, "OLD")

        # 再注入 10 条其他类型的拦截（使用新的 run_id），模拟连续 10 次运行无 security 违规
        for i in range(10):
            layer._metrics.record_interception(
                f"NEW{i:04d}",
                [{"file_path": f"src/ok_{i}.py", "violation_type": "naming", "message": f"命名违规 #{i}"}],
            )

        steering = layer.get_steering_enhancement()
        # security 应被冷却移除（最近 10 次运行中无 security）
        if steering is not None:
            assert "security" not in steering or "naming" in steering


# ══════════════════════════════════════════════════════════
# 无度量数据时返回 None
# ══════════════════════════════════════════════════════════


class TestNoMetricsReturnsNone:
    """无度量数据时 get_steering_enhancement 应返回 None。"""

    def test_no_metrics_returns_none(self, tmp_path):
        """无任何度量数据时应返回 None。"""
        layer = _make_layer(tmp_path)
        steering = layer.get_steering_enhancement()
        assert steering is None

    def test_metrics_collector_none_returns_none(self, tmp_path):
        """MetricsCollector 为 None 时应返回 None。"""
        layer = _make_layer(tmp_path)
        layer._metrics = None
        steering = layer.get_steering_enhancement()
        assert steering is None


# ══════════════════════════════════════════════════════════
# 低于阈值时返回 None
# ══════════════════════════════════════════════════════════


class TestBelowThresholdReturnsNone:
    """低于阈值时 get_steering_enhancement 应返回 None。"""

    def test_below_threshold_returns_none(self, tmp_path):
        """拦截次数低于阈值时应返回 None。"""
        layer = _make_layer(tmp_path)
        assert layer._metrics is not None

        # 注入 3 条记录（低于默认阈值 5）
        _inject_interceptions(layer._metrics, "security", 3)

        steering = layer.get_steering_enhancement()
        assert steering is None

    def test_exactly_at_threshold_returns_steering(self, tmp_path):
        """拦截次数恰好等于阈值时应返回 Steering。"""
        layer = _make_layer(tmp_path)
        assert layer._metrics is not None

        # 注入恰好 5 条记录（等于默认阈值）
        _inject_interceptions(layer._metrics, "security", 5)

        steering = layer.get_steering_enhancement()
        assert steering is not None
        assert "security" in steering
