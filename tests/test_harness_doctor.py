"""Doctor 单元测试

测试 doctor.py 的健康检查功能，包括：
- map.md 存在性检查
- .harnessrc.json 存在性检查
- 死链接检测
- 未声明目录检测
- 未引用 Spec 检测
- 健康报告汇总一致性
"""
import json
import os

import pytest

from agent_core.harness.doctor import Doctor, CheckItem, HealthReport


# ── 辅助函数 ────────────────────────────────────────────

def _make_files(root: str, paths: list[str]) -> None:
    """在 root 下批量创建文件。"""
    for p in paths:
        full = os.path.join(root, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write("")


def _write_map(root: str, content: str) -> None:
    """写入 .harness/map.md。"""
    harness_dir = os.path.join(root, ".harness")
    os.makedirs(harness_dir, exist_ok=True)
    with open(os.path.join(harness_dir, "map.md"), "w", encoding="utf-8") as f:
        f.write(content)


def _write_config(root: str, config: dict | None = None) -> None:
    """写入 .harnessrc.json。"""
    with open(os.path.join(root, ".harnessrc.json"), "w", encoding="utf-8") as f:
        json.dump(config or {"techStack": "vue3-ts", "srcDir": "src"}, f)


# ── 测试：map.md 存在性 ─────────────────────────────────

class TestDoctorMapExists:
    """测试 map.md 存在性检查。"""

    def test_map_exists_pass(self, tmp_path):
        """map.md 存在时应返回 pass。"""
        _write_map(str(tmp_path), "# 项目认知地图\n")
        _write_config(str(tmp_path))
        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        map_item = next(i for i in report.items if "map.md" in i.name and "存在性" in i.name)
        assert map_item.status == "pass"

    def test_map_missing_fail(self, tmp_path):
        """map.md 不存在时应返回 fail。"""
        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        map_item = next(i for i in report.items if "map.md" in i.name and "存在性" in i.name)
        assert map_item.status == "fail"

    def test_map_missing_cascades_to_related_checks(self, tmp_path):
        """map.md 不存在时，Map 相关检查应全部标记为 fail。"""
        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        # 路径有效性、死链接、未声明目录、未引用 Spec 都应为 fail
        related_names = ["路径有效性", "死链接", "未声明目录", "未引用 Spec"]
        for name_part in related_names:
            items = [i for i in report.items if name_part in i.name]
            assert len(items) > 0, f"未找到包含 '{name_part}' 的检查项"
            for item in items:
                assert item.status == "fail", f"'{item.name}' 应为 fail，实际为 {item.status}"


# ── 测试：.harnessrc.json 存在性 ────────────────────────

class TestDoctorConfigExists:
    """测试配置文件存在性检查。"""

    def test_config_exists_pass(self, tmp_path):
        _write_config(str(tmp_path))
        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        config_item = next(i for i in report.items if "harnessrc" in i.name)
        assert config_item.status == "pass"

    def test_config_missing_warn(self, tmp_path):
        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        config_item = next(i for i in report.items if "harnessrc" in i.name)
        assert config_item.status == "warn"


# ── 测试：死链接检测 ────────────────────────────────────

class TestDoctorDeadLinks:
    """测试认知地图中的死链接检测。"""

    def test_no_dead_links(self, tmp_path):
        """所有引用的文件都存在时，无死链接。"""
        _make_files(str(tmp_path), ["src/api/user.ts"])
        _write_config(str(tmp_path))
        map_content = "# 项目认知地图\n\n## 模块清单\n\n### API 模块\n- src/api/user.ts\n"
        _write_map(str(tmp_path), map_content)

        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        dead_item = next(i for i in report.items if "死链接" in i.name)
        assert dead_item.status == "pass"

    def test_dead_links_detected(self, tmp_path):
        """引用的文件不存在时，应检测到死链接。"""
        _write_config(str(tmp_path))
        map_content = "# 项目认知地图\n\n## 模块清单\n\n### API 模块\n- src/api/nonexistent.ts\n"
        _write_map(str(tmp_path), map_content)

        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        dead_item = next(i for i in report.items if "死链接" in i.name)
        assert dead_item.status == "warn"
        assert "nonexistent" in dead_item.message


# ── 测试：未声明目录检测 ────────────────────────────────

class TestDoctorUndeclaredDirs:
    """测试源码目录下未在认知地图中声明的目录。"""

    def test_all_dirs_declared(self, tmp_path):
        """所有目录都在 map 中声明时，应通过。"""
        _make_files(str(tmp_path), ["src/api/user.ts", "src/utils/helpers.ts"])
        _write_config(str(tmp_path))
        map_content = (
            "# 项目认知地图\n\n"
            "## 目录结构\n\n```\nsrc/\n├── api/\n├── utils/\n```\n"
        )
        _write_map(str(tmp_path), map_content)

        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        undeclared_item = next(i for i in report.items if "未声明目录" in i.name)
        assert undeclared_item.status == "pass"

    def test_undeclared_dir_detected(self, tmp_path):
        """存在未声明的目录时，应产生 warn。"""
        _make_files(str(tmp_path), [
            "src/api/user.ts",
            "src/secret/hidden.ts",  # 未在 map 中声明
        ])
        _write_config(str(tmp_path))
        map_content = (
            "# 项目认知地图\n\n"
            "## 目录结构\n\n```\nsrc/\n├── api/\n```\n"
        )
        _write_map(str(tmp_path), map_content)

        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        undeclared_item = next(i for i in report.items if "未声明目录" in i.name)
        assert undeclared_item.status == "warn"
        assert "secret" in undeclared_item.message


# ── 测试：未引用 Spec 检测 ──────────────────────────────

class TestDoctorUnreferencedSpecs:
    """测试 .harness/specs/ 下未在认知地图中引用的 Spec。"""

    def test_no_specs_dir(self, tmp_path):
        """specs 目录不存在时，跳过检测。"""
        _write_config(str(tmp_path))
        _write_map(str(tmp_path), "# 项目认知地图\n")

        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        spec_item = next(i for i in report.items if "Spec" in i.name and "引用" in i.name)
        assert spec_item.status == "pass"

    def test_all_specs_referenced(self, tmp_path):
        """所有 Spec 都在 map 中引用时，应通过。"""
        _make_files(str(tmp_path), [".harness/specs/api-spec.md"])
        _write_config(str(tmp_path))
        _write_map(str(tmp_path), "# 项目认知地图\n\n引用了 api-spec.md\n")

        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        spec_item = next(i for i in report.items if "Spec" in i.name and "引用" in i.name)
        assert spec_item.status == "pass"

    def test_unreferenced_spec_detected(self, tmp_path):
        """存在未引用的 Spec 时，应产生 warn。"""
        _make_files(str(tmp_path), [".harness/specs/orphan-spec.md"])
        _write_config(str(tmp_path))
        _write_map(str(tmp_path), "# 项目认知地图\n\n没有引用任何 spec\n")

        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        spec_item = next(i for i in report.items if "Spec" in i.name and "引用" in i.name)
        assert spec_item.status == "warn"
        assert "orphan-spec.md" in spec_item.message


# ── 测试：健康报告汇总一致性 ────────────────────────────

class TestHealthReportConsistency:
    """测试 HealthReport 的汇总数据一致性。"""

    def test_summary_counts_match(self, tmp_path):
        """passed + warned + failed == total == len(items)。"""
        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        assert report.passed + report.warned + report.failed == report.total
        assert report.total == len(report.items)

    def test_summary_with_map_exists(self, tmp_path):
        """有 map.md 时的汇总也应一致。"""
        _make_files(str(tmp_path), ["src/api/user.ts"])
        _write_config(str(tmp_path))
        _write_map(str(tmp_path), "# 项目认知地图\n\n- src/api/user.ts\n")

        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        assert report.passed + report.warned + report.failed == report.total
        assert report.total == len(report.items)

    def test_all_statuses_valid(self, tmp_path):
        """每个 CheckItem 的 status 应为 pass/warn/fail 之一。"""
        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        for item in report.items:
            assert item.status in ("pass", "warn", "fail"), \
                f"'{item.name}' 的 status '{item.status}' 不合法"

    def test_total_equals_item_count(self, tmp_path):
        """total 应等于 items 列表长度。"""
        _write_config(str(tmp_path))
        _write_map(str(tmp_path), "# 项目认知地图\n")

        doctor = Doctor(str(tmp_path))
        report = doctor.check()

        assert report.total == len(report.items)
        assert report.total >= 7  # 至少 7 个检查项


# ── 测试：CheckItem 数据类 ──────────────────────────────

class TestCheckItem:
    """CheckItem 数据类基本测试。"""

    def test_create_check_item(self):
        item = CheckItem(name="测试项", status="pass", message="通过")
        assert item.name == "测试项"
        assert item.status == "pass"
        assert item.message == "通过"


class TestHealthReport:
    """HealthReport 数据类基本测试。"""

    def test_default_values(self):
        report = HealthReport()
        assert report.items == []
        assert report.passed == 0
        assert report.warned == 0
        assert report.failed == 0
        assert report.total == 0
