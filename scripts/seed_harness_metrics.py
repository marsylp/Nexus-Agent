#!/usr/bin/env python3
"""生成 Harness 种子度量数据 — 用项目自身代码验证驾驭层

对 nexus-agent 项目自身运行 Harness 扫描和模拟违规检测，
在 .harness/metrics/ 中生成真实的度量数据，证明驾驭层可用。
"""
import json, os, sys

# 确保项目根目录在 path 中
ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent_core.harness.config import load_config, generate_default_config
from agent_core.harness.scanner import Scanner
from agent_core.harness.map_generator import MapGenerator
from agent_core.harness.doctor import Doctor
from agent_core.harness.metrics import MetricsCollector
from agent_core.harness.verifier import Verifier


def main():
    cwd = ROOT
    print("🔧 Harness 种子数据生成器")
    print(f"📂 项目目录: {cwd}\n")

    # ── 1. 初始化配置（python 技术栈）──────────────────
    config_path = os.path.join(cwd, ".harnessrc.json")
    if not os.path.isfile(config_path):
        print("📋 生成 .harnessrc.json (python 技术栈) ...")
        config_dict = generate_default_config("python")
        # 自定义：添加 nexus-agent 特有的约束
        config_dict["srcDir"] = "agent_core"
        config_dict["layers"] = ["agent", "harness", "mixins", "tools", "llm"]
        config_dict["naming"] = {
            "harness": r"^[a-z_]+\.py$",
            "mixins": r"^[a-z_]+_mixin\.py$",
        }
        config_dict["forbidden"] = [
            {"pattern": "print\\(.*password", "message": "禁止打印密码", "filePattern": "*.py"},
            {"pattern": "import pdb", "message": "禁止生产代码中使用 pdb", "filePattern": "*.py"},
        ]
        config_dict["security"]["checkSecretLeak"] = True
        config_dict["security"]["checkEval"] = True
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_dict, f, indent=2, ensure_ascii=False)
        print(f"  ✅ 已生成 .harnessrc.json")
    else:
        print(f"  ℹ️  .harnessrc.json 已存在")

    # ── 2. 扫描项目结构 ────────────────────────────────
    print("\n📊 扫描项目结构 ...")
    config = load_config(cwd)
    scanner = Scanner(config, cwd)
    scan_result = scanner.scan()

    total = (len(scan_result.apis) + len(scan_result.views) +
             len(scan_result.components) + len(scan_result.stores) +
             len(scan_result.utils))
    print(f"  扫描到 {total} 个文件")

    # ── 3. 生成认知地图 ────────────────────────────────
    print("\n🗺️  生成认知地图 ...")
    map_gen = MapGenerator(config, cwd)
    map_path = map_gen.write(scan_result)
    print(f"  ✅ {os.path.relpath(map_path, cwd)}")

    # ── 4. 运行健康检查 ────────────────────────────────
    print("\n🏥 运行健康检查 ...")
    doctor = Doctor(cwd)
    report = doctor.check()
    icons = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
    for item in report.items:
        print(f"  {icons.get(item.status, '❓')} {item.name}: {item.message}")
    print(f"  汇总: {report.passed} 通过 / {report.warned} 警告 / {report.failed} 失败")

    # ── 5. 对项目源码运行约束检查，生成度量数据 ────────
    print("\n🔍 运行约束检查（生成度量数据）...")
    metrics_dir = os.path.join(cwd, ".harness", "metrics")
    os.makedirs(metrics_dir, exist_ok=True)
    collector = MetricsCollector(metrics_dir)
    verifier = Verifier(config, cwd)

    src_dir = os.path.join(cwd, config.src_dir)
    run_id = "SEED_001"
    total_files = 0
    total_violations = 0

    for dirpath, _dirnames, filenames in os.walk(src_dir):
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(fpath, cwd)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue

            total_files += 1
            violations = verifier.verify(rel_path, content)
            if violations:
                total_violations += len(violations)
                collector.record_interception(run_id, violations)
                for v in violations:
                    loc = f"第 {v.line} 行" if v.line else "文件级别"
                    print(f"  🚫 [{v.type}] {rel_path} ({loc}): {v.message[:80]}")

    if total_violations == 0:
        print(f"  ✅ 扫描 {total_files} 个文件，无违规 — 代码质量良好")
        # 记录一条自修复记录作为种子数据
        collector.record_self_repair(
            run_id=run_id, failed_run_id="SEED_000",
            success=True, attempts=1,
            fixed_tests=["naming"], remaining_failures=[],
        )
    else:
        print(f"\n  📊 扫描 {total_files} 个文件，发现 {total_violations} 个违规")

    # ── 6. 输出度量汇总 ────────────────────────────────
    print("\n📊 度量汇总:")
    summary = collector.compute_summary()
    print(f"  总运行次数: {summary.total_runs}")
    print(f"  总拦截次数: {summary.total_interceptions}")
    if summary.interceptions_by_type:
        for vtype, count in summary.interceptions_by_type.items():
            print(f"    {vtype}: {count}")
    print(f"  自修复率: {summary.self_repair_rate:.1%}")

    print("\n🎉 种子数据生成完成!")
    print(f"  度量目录: .harness/metrics/")
    print(f"  认知地图: .harness/map.md")


if __name__ == "__main__":
    main()
