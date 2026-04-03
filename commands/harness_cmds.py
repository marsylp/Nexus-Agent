"""Harness 驾驭层命令: /harness"""
from __future__ import annotations
import os
from agent_core import Agent
from agent_core.ui import (
    section, item, success, error, warn, info, hint,
    bold, dim, cyan, green, yellow, red, gray,
    kv_table, cmd_table, progress_bar, separator,
)


def handle_harness(cmd: str, agent: Agent):
    from agent_core.harness.config import (
        load_config, generate_default_config, get_available_templates,
    )
    from agent_core.harness.scanner import Scanner
    from agent_core.harness.map_generator import MapGenerator
    from agent_core.harness.doctor import Doctor
    from agent_core.harness.metrics import MetricsCollector
    from agent_core.harness.plan_generator import PlanGenerator

    parts = cmd.strip().split()
    sub = parts[1] if len(parts) > 1 else ""
    cwd = os.getcwd()

    if not sub:
        section("Harness 驾驭层")
        print()
        cmd_table([
            ("/harness init [技术栈]", "初始化 Harness 体系"),
            ("/harness scan", "扫描项目并更新认知地图"),
            ("/harness doctor", "运行健康检查"),
            ("/harness metrics", "查看度量汇总"),
            ("/harness plan <名称>", "生成执行计划"),
        ])
        print()
        return

    if sub == "init":
        _harness_init(parts, cwd, generate_default_config, get_available_templates,
                       load_config, Scanner, MapGenerator)
        return

    config_path = os.path.join(cwd, ".harnessrc.json")

    if sub == "scan":
        _harness_scan(config_path, cwd, load_config, Scanner, MapGenerator)
        return
    if sub == "doctor":
        _harness_doctor(config_path, cwd, Doctor)
        return
    if sub == "metrics":
        _harness_metrics(config_path, cwd, MetricsCollector)
        return
    if sub == "plan":
        _harness_plan(parts, config_path, cwd, load_config, PlanGenerator)
        return

    error(f"未知子命令: {sub}")
    hint("/harness init|scan|doctor|metrics|plan")
    print()


def _harness_init(parts, cwd, generate_default_config, get_available_templates,
                   load_config, Scanner, MapGenerator):
    tech_stack = parts[2] if len(parts) > 2 else ""
    templates = get_available_templates()

    if not tech_stack:
        section("可用技术栈模板")
        print()
        for i, t in enumerate(templates, 1):
            print(f"    {cyan(str(i))}. {t}")
        print(f"    {cyan(str(len(templates) + 1))}. custom")
        print()
        choice = input(f"    {bold('选择编号或名称:')} ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            tech_stack = templates[idx] if 0 <= idx < len(templates) else "custom"
        elif choice in templates:
            tech_stack = choice
        else:
            tech_stack = "custom"

    try:
        config_dict = generate_default_config(tech_stack)
        import json
        config_path = os.path.join(cwd, ".harnessrc.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_dict, f, indent=2, ensure_ascii=False)
        success(f"已生成 .harnessrc.json ({tech_stack})")

        harness_dir = os.path.join(cwd, ".harness")
        os.makedirs(harness_dir, exist_ok=True)
        success("已创建 .harness/ 目录")

        config = load_config(cwd)
        scanner = Scanner(config, cwd)
        scan_result = scanner.scan()
        map_gen = MapGenerator(config, cwd)
        map_path = map_gen.write(scan_result)
        success(f"已生成认知地图: {os.path.relpath(map_path, cwd)}")
        print()
        print(f"    {green('Harness 体系初始化完成')}")
        print()
    except Exception as e:
        error(f"初始化失败: {e}")
        print()


def _harness_scan(config_path, cwd, load_config, Scanner, MapGenerator):
    if not os.path.isfile(config_path):
        warn("未找到 .harnessrc.json")
        hint("/harness init")
        print()
        return
    try:
        config = load_config(cwd)
        scanner = Scanner(config, cwd)
        scan_result = scanner.scan()
        map_gen = MapGenerator(config, cwd)
        map_path = map_gen.write(scan_result)

        section("扫描结果")
        print()
        kv_table([
            ("API", f"{len(scan_result.apis)} 个文件"),
            ("Views", f"{len(scan_result.views)} 个文件"),
            ("Components", f"{len(scan_result.components)} 个文件"),
            ("Stores", f"{len(scan_result.stores)} 个文件"),
            ("Utils", f"{len(scan_result.utils)} 个文件"),
        ])
        total = (len(scan_result.apis) + len(scan_result.views) +
                 len(scan_result.components) + len(scan_result.stores) +
                 len(scan_result.utils))
        print()
        success(f"共 {total} 个文件，认知地图已更新")
        print()
    except Exception as e:
        error(f"扫描失败: {e}")
        print()


def _harness_doctor(config_path, cwd, Doctor):
    if not os.path.isfile(config_path):
        warn("未找到 .harnessrc.json")
        hint("/harness init")
        print()
        return
    try:
        doctor = Doctor(cwd)
        report = doctor.check()

        section("健康检查")
        print()
        for check_item in report.items:
            if check_item.status == "pass":
                icon = green("✓")
            elif check_item.status == "warn":
                icon = yellow("!")
            else:
                icon = red("✗")
            print(f"    {icon} {check_item.name}")
            if check_item.status != "pass":
                print(f"      {gray(check_item.message[:80])}")

        print()
        parts = []
        if report.passed:
            parts.append(green(f"{report.passed} 通过"))
        if report.warned:
            parts.append(yellow(f"{report.warned} 警告"))
        if report.failed:
            parts.append(red(f"{report.failed} 失败"))
        print(f"    {' / '.join(parts)}  " + dim("(共 {} 项)".format(report.total)))
        print()
    except Exception as e:
        error(f"健康检查失败: {e}")
        print()


def _harness_metrics(config_path, cwd, MetricsCollector):
    if not os.path.isfile(config_path):
        warn("未找到 .harnessrc.json")
        hint("/harness init")
        print()
        return
    try:
        metrics_dir = os.path.join(cwd, ".harness", "metrics")
        collector = MetricsCollector(metrics_dir)
        summary = collector.compute_summary()

        section("度量汇总")
        print()
        kv_table([
            ("总运行次数", str(summary.total_runs)),
            ("总拦截次数", str(summary.total_interceptions)),
            ("自修复率", f"{summary.self_repair_rate:.1%}"),
            ("未解决逃逸 Bug", str(summary.escaped_bugs)),
        ])

        if summary.interceptions_by_type:
            print()
            print(f"    {bold('按类型统计:')}")
            for vtype, count in summary.interceptions_by_type.items():
                bar = progress_bar(count, summary.total_interceptions, width=15)
                print(f"      {vtype:20s} {bar} {dim(str(count))}")

        if summary.weekly_trend:
            print()
            print(f"    {bold('最近 4 周趋势:')}")
            for week in summary.weekly_trend:
                period = f"{week['week_start']} ~ {week['week_end']}"
                print(f"      {dim(period)}  拦截 {cyan(str(week['interceptions']))}  修复 {green(str(week['self_repairs']))}")

        print()
    except Exception as e:
        error(f"度量查看失败: {e}")
        print()


def _harness_plan(parts, config_path, cwd, load_config, PlanGenerator):
    if not os.path.isfile(config_path):
        warn("未找到 .harnessrc.json")
        hint("/harness init")
        print()
        return

    plan_args = parts[2:]
    name = ""
    task_type = "feature"
    mode = "standard"

    i = 0
    positional_collected = False
    while i < len(plan_args):
        arg = plan_args[i]
        if arg == "--type" and i + 1 < len(plan_args):
            val = plan_args[i + 1]
            if val in ("feature", "bugfix", "refactor"):
                task_type = val
            i += 2
        elif arg == "--mode" and i + 1 < len(plan_args):
            val = plan_args[i + 1]
            if val in ("standard", "fast", "strict"):
                mode = val
            i += 2
        else:
            if not positional_collected:
                name = arg
                positional_collected = True
            i += 1

    if not name:
        hint("/harness plan <名称> [--type feature|bugfix|refactor] [--mode standard|fast|strict]")
        print()
        return

    try:
        config = load_config(cwd)
        planner = PlanGenerator(config, cwd)
        file_path = planner.generate(name, task_type=task_type, mode=mode)
        rel_path = os.path.relpath(file_path, cwd)
        success(f"执行计划已生成: {rel_path}")
        print()
    except Exception as e:
        error(f"计划生成失败: {e}")
        print()
