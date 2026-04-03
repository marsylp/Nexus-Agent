"""MetricsCollector 度量采集器 — 单元测试

测试 JSONL 追加写入、记录字段完整性、度量汇总计算、
逃逸 Bug 解决标记、往返一致性、文件大小警告等功能。
"""
import json
import os

import pytest

from agent_core.harness.metrics import (
    MetricsCollector,
    InterceptionRecord,
    SelfRepairRecord,
    EscapedBugRecord,
    MetricsSummary,
)
from agent_core.harness.verifier import Violation


# ══════════════════════════════════════════════════════════
# JSONL 追加写入
# ══════════════════════════════════════════════════════════


class TestJSONLAppend:
    """Property 24: 度量 JSONL 追加写入 — 文件行数等于记录数，每行为有效 JSON。
    **Validates: Requirements 8.1**
    """

    def test_interception_lines_equal_records(self, tmp_path):
        """写入 N 条拦截记录后，JSONL 文件应有 N 行有效 JSON。"""
        mc = MetricsCollector(str(tmp_path))
        violations = [
            {"file_path": "a.py", "violation_type": "security", "message": "密钥泄露"},
            {"file_path": "b.py", "violation_type": "naming", "message": "命名不规范"},
        ]
        mc.record_interception("R001", violations)

        fpath = os.path.join(str(tmp_path), "interceptions.jsonl")
        with open(fpath, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]

        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert isinstance(obj, dict)

    def test_self_repair_lines_equal_records(self, tmp_path):
        """写入自修复记录后，JSONL 文件行数正确。"""
        mc = MetricsCollector(str(tmp_path))
        mc.record_self_repair("R002", "R001", True, 1, ["test_a"], [])
        mc.record_self_repair("R003", "R002", False, 2, [], ["test_b"])

        fpath = os.path.join(str(tmp_path), "self_repairs.jsonl")
        with open(fpath, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]

        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert isinstance(obj, dict)

    def test_escaped_bug_lines_equal_records(self, tmp_path):
        """写入逃逸 Bug 记录后，JSONL 文件行数正确。"""
        mc = MetricsCollector(str(tmp_path))
        mc.record_escaped_bug("Bug 描述 1")
        mc.record_escaped_bug("Bug 描述 2")
        mc.record_escaped_bug("Bug 描述 3")

        fpath = os.path.join(str(tmp_path), "escaped_bugs.jsonl")
        with open(fpath, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]

        assert len(lines) == 3
        for line in lines:
            obj = json.loads(line)
            assert isinstance(obj, dict)

    def test_append_preserves_existing(self, tmp_path):
        """多次写入应追加而非覆盖。"""
        mc = MetricsCollector(str(tmp_path))
        mc.record_interception("R001", [{"file_path": "a.py", "violation_type": "security", "message": "m1"}])
        mc.record_interception("R002", [{"file_path": "b.py", "violation_type": "naming", "message": "m2"}])

        fpath = os.path.join(str(tmp_path), "interceptions.jsonl")
        with open(fpath, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]

        assert len(lines) == 2


# ══════════════════════════════════════════════════════════
# 记录字段完整性
# ══════════════════════════════════════════════════════════


class TestRecordFieldCompleteness:
    """Property 25: 度量记录字段完整性 — 拦截记录和自修复记录包含所有必需字段。
    **Validates: Requirements 8.3, 8.4**
    """

    def test_interception_record_fields(self, tmp_path):
        """拦截记录应包含 timestamp/run_id/file_path/violation_type/message。"""
        mc = MetricsCollector(str(tmp_path))
        mc.record_interception("R001", [
            {"file_path": "src/a.vue", "violation_type": "forbidden", "message": "禁止原生 button"}
        ])

        fpath = os.path.join(str(tmp_path), "interceptions.jsonl")
        with open(fpath, "r", encoding="utf-8") as f:
            rec = json.loads(f.readline().strip())

        required_fields = {"timestamp", "run_id", "file_path", "violation_type", "message"}
        assert required_fields.issubset(rec.keys())
        assert rec["run_id"] == "R001"
        assert rec["file_path"] == "src/a.vue"
        assert rec["violation_type"] == "forbidden"
        assert rec["message"] == "禁止原生 button"
        assert len(rec["timestamp"]) > 0

    def test_self_repair_record_fields(self, tmp_path):
        """自修复记录应包含 timestamp/run_id/failed_run_id/success/attempts/fixed_tests/remaining_failures。"""
        mc = MetricsCollector(str(tmp_path))
        mc.record_self_repair("R002", "R001", True, 2, ["test_x"], ["test_y"])

        fpath = os.path.join(str(tmp_path), "self_repairs.jsonl")
        with open(fpath, "r", encoding="utf-8") as f:
            rec = json.loads(f.readline().strip())

        required_fields = {
            "timestamp", "run_id", "failed_run_id",
            "success", "attempts", "fixed_tests", "remaining_failures",
        }
        assert required_fields.issubset(rec.keys())
        assert rec["run_id"] == "R002"
        assert rec["failed_run_id"] == "R001"
        assert rec["success"] is True
        assert rec["attempts"] == 2
        assert rec["fixed_tests"] == ["test_x"]
        assert rec["remaining_failures"] == ["test_y"]

    def test_interception_with_violation_object(self, tmp_path):
        """使用 Violation 数据类实例记录拦截，字段应完整。"""
        mc = MetricsCollector(str(tmp_path))
        v = Violation(file="src/b.vue", line=10, type="security", message="eval 使用")
        mc.record_interception("R003", [v])

        fpath = os.path.join(str(tmp_path), "interceptions.jsonl")
        with open(fpath, "r", encoding="utf-8") as f:
            rec = json.loads(f.readline().strip())

        assert rec["file_path"] == "src/b.vue"
        assert rec["violation_type"] == "security"
        assert rec["message"] == "eval 使用"


# ══════════════════════════════════════════════════════════
# 度量汇总计算正确性
# ══════════════════════════════════════════════════════════


class TestMetricsSummary:
    """Property 26: 度量汇总计算正确性 — total_interceptions 等于记录总数，按类型计数之和一致。
    **Validates: Requirements 8.5**
    """

    def test_summary_total_interceptions(self, tmp_path):
        """total_interceptions 应等于拦截记录总数。"""
        mc = MetricsCollector(str(tmp_path))
        mc.record_interception("R001", [
            {"file_path": "a.py", "violation_type": "security", "message": "m1"},
            {"file_path": "b.py", "violation_type": "naming", "message": "m2"},
        ])
        mc.record_interception("R002", [
            {"file_path": "c.py", "violation_type": "security", "message": "m3"},
        ])

        summary = mc.compute_summary()
        assert summary.total_interceptions == 3

    def test_summary_by_type_sum_equals_total(self, tmp_path):
        """按类型计数之和应等于 total_interceptions。"""
        mc = MetricsCollector(str(tmp_path))
        mc.record_interception("R001", [
            {"file_path": "a.py", "violation_type": "security", "message": "m1"},
            {"file_path": "b.py", "violation_type": "naming", "message": "m2"},
            {"file_path": "c.py", "violation_type": "security", "message": "m3"},
        ])

        summary = mc.compute_summary()
        type_sum = sum(summary.interceptions_by_type.values())
        assert type_sum == summary.total_interceptions

    def test_summary_self_repair_rate(self, tmp_path):
        """自修复率应等于成功数 / 总数。"""
        mc = MetricsCollector(str(tmp_path))
        mc.record_self_repair("R001", "R000", True, 1, ["t1"], [])
        mc.record_self_repair("R002", "R000", False, 2, [], ["t2"])
        mc.record_self_repair("R003", "R000", True, 1, ["t3"], [])

        summary = mc.compute_summary()
        # 2 成功 / 3 总数
        assert abs(summary.self_repair_rate - 2 / 3) < 0.01

    def test_summary_empty_metrics(self, tmp_path):
        """无度量数据时汇总应为零值。"""
        mc = MetricsCollector(str(tmp_path))
        summary = mc.compute_summary()

        assert summary.total_interceptions == 0
        assert summary.total_runs == 0
        assert summary.interceptions_by_type == {}
        assert summary.self_repair_rate == 0.0
        assert summary.escaped_bugs == 0

    def test_summary_escaped_bugs_count(self, tmp_path):
        """escaped_bugs 应为未解决的逃逸 Bug 数量。"""
        mc = MetricsCollector(str(tmp_path))
        bug1 = mc.record_escaped_bug("Bug 1")
        mc.record_escaped_bug("Bug 2")
        mc.resolve_escaped_bug(bug1, "新增规则 X")

        summary = mc.compute_summary()
        assert summary.escaped_bugs == 1  # 只有 Bug 2 未解决


# ══════════════════════════════════════════════════════════
# 逃逸 Bug 解决标记
# ══════════════════════════════════════════════════════════


class TestEscapedBugResolve:
    """Property 27: 逃逸 Bug 解决标记 — resolve 后 resolved=True 且 added_rule 正确。
    **Validates: Requirements 8.6**
    """

    def test_resolve_marks_resolved(self, tmp_path):
        """resolve_escaped_bug 后 resolved 应为 True。"""
        mc = MetricsCollector(str(tmp_path))
        bug_id = mc.record_escaped_bug("XSS 未检测")
        result = mc.resolve_escaped_bug(bug_id, "新增 v-html 检测规则")

        assert result is True

        # 读取文件验证
        fpath = os.path.join(str(tmp_path), "escaped_bugs.jsonl")
        with open(fpath, "r", encoding="utf-8") as f:
            rec = json.loads(f.readline().strip())

        assert rec["resolved"] is True
        assert rec["added_rule"] == "新增 v-html 检测规则"

    def test_resolve_nonexistent_bug(self, tmp_path):
        """解决不存在的 bug_id 应返回 False。"""
        mc = MetricsCollector(str(tmp_path))
        mc.record_escaped_bug("Some bug")
        result = mc.resolve_escaped_bug("NONEXISTENT", "rule")
        assert result is False

    def test_resolve_preserves_other_bugs(self, tmp_path):
        """解决一个 Bug 不应影响其他 Bug 的状态。"""
        mc = MetricsCollector(str(tmp_path))
        bug1 = mc.record_escaped_bug("Bug 1")
        bug2 = mc.record_escaped_bug("Bug 2")

        mc.resolve_escaped_bug(bug1, "规则 A")

        fpath = os.path.join(str(tmp_path), "escaped_bugs.jsonl")
        with open(fpath, "r", encoding="utf-8") as f:
            records = [json.loads(l.strip()) for l in f if l.strip()]

        resolved_rec = [r for r in records if r["bug_id"] == bug1][0]
        unresolved_rec = [r for r in records if r["bug_id"] == bug2][0]

        assert resolved_rec["resolved"] is True
        assert unresolved_rec["resolved"] is False


# ══════════════════════════════════════════════════════════
# 度量记录往返一致性
# ══════════════════════════════════════════════════════════


class TestRoundTrip:
    """Property 28: 度量记录往返一致性 — 写入后读取产生等价数据。
    **Validates: Requirements 8.8**
    """

    def test_interception_roundtrip(self, tmp_path):
        """拦截记录写入后读取应产生等价数据。"""
        mc = MetricsCollector(str(tmp_path))
        mc.record_interception("R001", [
            {"file_path": "src/x.vue", "violation_type": "forbidden", "message": "禁止 button"},
        ])

        records = mc._read_records(mc._interceptions_file)
        assert len(records) == 1
        rec = records[0]
        assert rec["run_id"] == "R001"
        assert rec["file_path"] == "src/x.vue"
        assert rec["violation_type"] == "forbidden"
        assert rec["message"] == "禁止 button"

    def test_self_repair_roundtrip(self, tmp_path):
        """自修复记录写入后读取应产生等价数据。"""
        mc = MetricsCollector(str(tmp_path))
        mc.record_self_repair("R002", "R001", True, 3, ["t1", "t2"], ["t3"])

        records = mc._read_records(mc._self_repairs_file)
        assert len(records) == 1
        rec = records[0]
        assert rec["run_id"] == "R002"
        assert rec["failed_run_id"] == "R001"
        assert rec["success"] is True
        assert rec["attempts"] == 3
        assert rec["fixed_tests"] == ["t1", "t2"]
        assert rec["remaining_failures"] == ["t3"]

    def test_escaped_bug_roundtrip(self, tmp_path):
        """逃逸 Bug 记录写入后读取应产生等价数据。"""
        mc = MetricsCollector(str(tmp_path))
        bug_id = mc.record_escaped_bug("某个 Bug 描述")

        records = mc._read_records(mc._escaped_bugs_file)
        assert len(records) == 1
        rec = records[0]
        assert rec["bug_id"] == bug_id
        assert rec["description"] == "某个 Bug 描述"
        assert rec["resolved"] is False
        assert rec["added_rule"] == ""


# ══════════════════════════════════════════════════════════
# 度量文件超过 50MB 时输出警告
# ══════════════════════════════════════════════════════════


class TestFileSizeWarning:
    """度量文件超过 50MB 时应输出警告。
    **Validates: Requirements 8.7**
    """

    def test_large_file_warning(self, tmp_path, capsys):
        """超过 50MB 的文件应触发 stderr 警告。"""
        mc = MetricsCollector(str(tmp_path))
        # 创建一个超过 50MB 的假文件
        fpath = os.path.join(str(tmp_path), "interceptions.jsonl")
        with open(fpath, "w") as f:
            # 写入 ~51MB 的数据
            chunk = "x" * (1024 * 1024)  # 1MB
            for _ in range(51):
                f.write(chunk)

        # 触发大小检查
        mc.record_interception("R001", [
            {"file_path": "a.py", "violation_type": "security", "message": "m"}
        ])

        captured = capsys.readouterr()
        assert "50MB" in captured.err
