"""MetricsCollector — 度量采集器

记录拦截率、自修复率、逃逸 Bug 等度量数据，
使用 JSONL 格式追加写入 `.harness/metrics/` 目录。
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from typing import Any


# ── 数据类 ──────────────────────────────────────────────

@dataclass
class InterceptionRecord:
    """拦截记录"""
    timestamp: str
    run_id: str
    file_path: str
    violation_type: str
    message: str


@dataclass
class SelfRepairRecord:
    """自修复记录"""
    timestamp: str
    run_id: str
    failed_run_id: str
    success: bool
    attempts: int
    fixed_tests: list[str]
    remaining_failures: list[str]


@dataclass
class EscapedBugRecord:
    """逃逸 Bug 记录"""
    timestamp: str
    bug_id: str
    description: str
    resolved: bool = False
    added_rule: str = ""


@dataclass
class MetricsSummary:
    """度量汇总"""
    total_runs: int
    total_interceptions: int
    interceptions_by_type: dict[str, int]
    self_repair_rate: float
    escaped_bugs: int
    weekly_trend: list[dict]


# 文件大小警告阈值：50MB
_SIZE_WARN_THRESHOLD = 50 * 1024 * 1024


class MetricsCollector:
    """度量采集器。

    将度量数据以 JSONL 格式追加写入 `.harness/metrics/` 目录下的文件。
    """

    def __init__(self, metrics_dir: str):
        self._metrics_dir = metrics_dir
        self._interceptions_file = os.path.join(metrics_dir, "interceptions.jsonl")
        self._self_repairs_file = os.path.join(metrics_dir, "self_repairs.jsonl")
        self._escaped_bugs_file = os.path.join(metrics_dir, "escaped_bugs.jsonl")
        # 自动创建度量目录
        os.makedirs(metrics_dir, exist_ok=True)

    # ── 写入工具 ────────────────────────────────────────

    def _append_record(self, file_path: str, record: dict) -> None:
        """向 JSONL 文件追加一条记录，超过 50MB 时输出警告。"""
        self._check_file_size(file_path)
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read_records(self, file_path: str) -> list[dict]:
        """读取 JSONL 文件中的所有记录。"""
        if not os.path.isfile(file_path):
            return []
        records: list[dict] = []
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records

    def _check_file_size(self, file_path: str) -> None:
        """检查文件大小，超过 50MB 时输出警告。"""
        if os.path.isfile(file_path):
            size = os.path.getsize(file_path)
            if size >= _SIZE_WARN_THRESHOLD:
                print(
                    f"⚠️  度量文件 {file_path} 已超过 50MB（{size / 1024 / 1024:.1f}MB），"
                    f"建议归档旧数据",
                    file=sys.stderr,
                )

    # ── 记录拦截事件 ────────────────────────────────────

    def record_interception(self, run_id: str, violations: list) -> None:
        """记录拦截事件到 interceptions.jsonl。

        violations 列表中的每个元素应包含 file/file_path、type/violation_type、message 字段，
        或者是 Violation 数据类实例。
        """
        timestamp = datetime.utcnow().isoformat() + "Z"
        for v in violations:
            if hasattr(v, "file"):
                # Violation 数据类实例
                record = InterceptionRecord(
                    timestamp=timestamp,
                    run_id=run_id,
                    file_path=getattr(v, "file", ""),
                    violation_type=getattr(v, "type", ""),
                    message=getattr(v, "message", ""),
                )
            elif isinstance(v, dict):
                record = InterceptionRecord(
                    timestamp=timestamp,
                    run_id=run_id,
                    file_path=v.get("file_path") or v.get("file", ""),
                    violation_type=v.get("violation_type") or v.get("type", ""),
                    message=v.get("message", ""),
                )
            else:
                continue
            self._append_record(self._interceptions_file, asdict(record))

    # ── 记录自修复事件 ──────────────────────────────────

    def record_self_repair(
        self,
        run_id: str,
        failed_run_id: str,
        success: bool,
        attempts: int,
        fixed_tests: list[str],
        remaining_failures: list[str],
    ) -> None:
        """记录自修复事件到 self_repairs.jsonl。"""
        timestamp = datetime.utcnow().isoformat() + "Z"
        record = SelfRepairRecord(
            timestamp=timestamp,
            run_id=run_id,
            failed_run_id=failed_run_id,
            success=success,
            attempts=attempts,
            fixed_tests=list(fixed_tests),
            remaining_failures=list(remaining_failures),
        )
        self._append_record(self._self_repairs_file, asdict(record))

    # ── 记录逃逸 Bug ───────────────────────────────────

    def record_escaped_bug(self, description: str) -> str:
        """记录逃逸 Bug 到 escaped_bugs.jsonl，返回 bug_id。"""
        timestamp = datetime.utcnow().isoformat() + "Z"
        bug_id = f"BUG-{uuid.uuid4().hex[:8]}"
        record = EscapedBugRecord(
            timestamp=timestamp,
            bug_id=bug_id,
            description=description,
        )
        self._append_record(self._escaped_bugs_file, asdict(record))
        return bug_id

    # ── 解决逃逸 Bug ───────────────────────────────────

    def resolve_escaped_bug(self, bug_id: str, added_rule: str) -> bool:
        """标记逃逸 Bug 为已解决。

        读取所有记录，找到匹配的 bug_id 并更新 resolved 和 added_rule，
        然后重写整个文件。返回是否找到并更新了记录。
        """
        records = self._read_records(self._escaped_bugs_file)
        found = False
        for rec in records:
            if rec.get("bug_id") == bug_id:
                rec["resolved"] = True
                rec["added_rule"] = added_rule
                found = True

        if not found:
            return False

        # 重写文件
        with open(self._escaped_bugs_file, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True

    # ── 计算度量汇总 ────────────────────────────────────

    def compute_summary(self) -> MetricsSummary:
        """计算度量汇总。"""
        interceptions = self._read_records(self._interceptions_file)
        self_repairs = self._read_records(self._self_repairs_file)
        escaped_bugs = self._read_records(self._escaped_bugs_file)

        # 总拦截次数
        total_interceptions = len(interceptions)

        # 按类型统计拦截次数
        interceptions_by_type: dict[str, int] = {}
        for rec in interceptions:
            vtype = rec.get("violation_type", "unknown")
            interceptions_by_type[vtype] = interceptions_by_type.get(vtype, 0) + 1

        # 总运行次数：拦截记录中不同 run_id 的数量 + 自修复记录中不同 run_id 的数量
        run_ids: set[str] = set()
        for rec in interceptions:
            run_ids.add(rec.get("run_id", ""))
        for rec in self_repairs:
            run_ids.add(rec.get("run_id", ""))
        run_ids.discard("")
        total_runs = len(run_ids)

        # 自修复率
        total_repairs = len(self_repairs)
        successful_repairs = sum(1 for r in self_repairs if r.get("success"))
        self_repair_rate = (
            successful_repairs / total_repairs if total_repairs > 0 else 0.0
        )

        # 未解决的逃逸 Bug 数量
        unresolved_bugs = sum(
            1 for b in escaped_bugs if not b.get("resolved", False)
        )

        # 按周趋势（最近 4 周）
        weekly_trend = self._compute_weekly_trend(interceptions, self_repairs)

        return MetricsSummary(
            total_runs=total_runs,
            total_interceptions=total_interceptions,
            interceptions_by_type=interceptions_by_type,
            self_repair_rate=round(self_repair_rate, 4),
            escaped_bugs=unresolved_bugs,
            weekly_trend=weekly_trend,
        )

    def _compute_weekly_trend(
        self,
        interceptions: list[dict],
        self_repairs: list[dict],
    ) -> list[dict]:
        """计算最近 4 周的趋势数据。"""
        now = datetime.utcnow()
        weeks: list[dict] = []

        for week_offset in range(3, -1, -1):
            week_start = now - timedelta(weeks=week_offset + 1)
            week_end = now - timedelta(weeks=week_offset)

            week_interceptions = 0
            week_repairs = 0
            week_successful = 0

            for rec in interceptions:
                ts = self._parse_timestamp(rec.get("timestamp", ""))
                if ts and week_start <= ts < week_end:
                    week_interceptions += 1

            for rec in self_repairs:
                ts = self._parse_timestamp(rec.get("timestamp", ""))
                if ts and week_start <= ts < week_end:
                    week_repairs += 1
                    if rec.get("success"):
                        week_successful += 1

            weeks.append({
                "week_start": week_start.strftime("%Y-%m-%d"),
                "week_end": week_end.strftime("%Y-%m-%d"),
                "interceptions": week_interceptions,
                "self_repairs": week_repairs,
                "repair_rate": (
                    round(week_successful / week_repairs, 4)
                    if week_repairs > 0
                    else 0.0
                ),
            })

        return weeks

    @staticmethod
    def _parse_timestamp(ts: str) -> datetime | None:
        """解析 ISO 格式时间戳。"""
        if not ts:
            return None
        try:
            # 移除尾部 Z 后解析
            clean = ts.rstrip("Z")
            return datetime.fromisoformat(clean)
        except (ValueError, TypeError):
            return None
