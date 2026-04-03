"""长任务相关命令: /task"""
from __future__ import annotations
import os
from agent_core import Agent
from agent_core.task_harness import TaskHarness, TaskConfig
from agent_core.ui import (
    section, item, success, error, warn, info, hint,
    bold, dim, cyan, green, yellow, gray,
    kv_table, cmd_table, progress_bar,
)

# 全局 TaskHarness 实例
_active_harness: TaskHarness | None = None


def get_active_harness() -> TaskHarness | None:
    return _active_harness


def handle_task(agent: Agent, cmd: str):
    global _active_harness
    parts = cmd.strip().split(maxsplit=1)
    sub = parts[1].strip() if len(parts) > 1 else ""

    if not sub:
        section("长任务控制")
        print()
        cmd_table([
            ("/task <需求>", "初始化长任务"),
            ("/task session", "运行下一个 session"),
            ("/task run [N]", "连续运行直到完成"),
            ("/task status", "查看进度"),
            ("/task features", "查看 feature 列表"),
            ("/task verify <id>", "手动验证 feature"),
            ("/task finding <内容>", "记录关键发现"),
            ("/task findings", "查看所有 findings"),
        ])
        print()
        return

    if sub == "status":
        _cmd_status()
        return
    if sub.startswith("finding "):
        _cmd_finding(sub)
        return
    if sub == "findings":
        _cmd_findings()
        return
    if sub == "session":
        _cmd_session()
        return
    if sub.startswith("run"):
        _cmd_run(sub)
        return
    if sub == "features":
        _cmd_features()
        return
    if sub.startswith("verify"):
        _cmd_verify(sub)
        return
    if sub == "config":
        _cmd_config()
        return

    # /task <需求> — 初始化新的长任务
    _cmd_init(agent, sub)


def _cmd_status():
    if not _active_harness:
        info("无活跃长任务")
        hint("/task <需求>")
        print()
        return
    status = _active_harness.status()
    stats = status["stats"]

    section("长任务进度")
    print()
    bar = progress_bar(stats["passed"], stats["total"])
    print(f"    {bar}  {bold('{}/{}'.format(stats['passed'], stats['total']))} features")

    if status["next_feature"]:
        nf = status["next_feature"]
        print(f"    {cyan('下一个:')} #{nf['id']} {nf['description']}")
    if status["findings_count"]:
        print(f"    {dim('Findings:')} {status['findings_count']} 条")
    if status["recent_progress"]:
        print()
        print(f"    {bold('最近进度:')}")
        for p in status["recent_progress"][-3:]:
            icon = green("✓") if p["action"] == "feature_done" else dim("·")
            print(f"      {icon} {gray(p['time'])} {p['action']}: {p['summary'][:50]}")
    if status["git_log"]:
        print()
        print(f"    {bold('Git:')}")
        for line in status["git_log"].strip().split("\n")[:3]:
            print(f"      {gray(line)}")
    print()


def _cmd_finding(sub: str):
    if not _active_harness:
        warn("无活跃长任务")
        print()
        return
    content = sub[8:].strip()
    if not content:
        hint("/task finding <发现内容>")
        print()
        return
    feature_id = 0
    if content.startswith("#") and " " in content:
        tag, rest = content.split(" ", 1)
        if tag[1:].isdigit():
            feature_id = int(tag[1:])
            content = rest
    _active_harness.record_finding(content, feature_id=feature_id)
    success(f"已记录 Finding: {content[:60]}")
    print()


def _cmd_findings():
    if not _active_harness:
        info("无活跃长任务")
        print()
        return
    _active_harness.progress.load()
    findings = _active_harness.progress.get_findings()
    if not findings:
        info("暂无 Findings")
        print()
        return
    section("Findings")
    print()
    for i, f in enumerate(findings, 1):
        feature_tag = " " + cyan("[#{}]".format(f.feature_id)) if f.feature_id else ""
        print(f"    {dim(str(i) + '.')} {gray(f.timestamp)}{feature_tag} {f.summary}")
    print("\n    " + dim("共 {} 条".format(len(findings))))
    print()


def _cmd_session():
    if not _active_harness:
        warn("无活跃长任务")
        hint("/task <需求>")
        print()
        return
    _active_harness.run_session()


def _cmd_run(sub: str):
    if not _active_harness:
        warn("无活跃长任务")
        hint("/task <需求>")
        print()
        return
    max_s = 50
    run_parts = sub.split()
    if len(run_parts) > 1 and run_parts[1].isdigit():
        max_s = int(run_parts[1])
    _active_harness.run_all(max_sessions=max_s)


def _cmd_features():
    if not _active_harness:
        info("无活跃长任务")
        print()
        return
    _active_harness.feature_mgr.load()

    section("Feature 列表")
    print()
    for ft in _active_harness.feature_mgr.features:
        icon = green("✓") if ft.passes else gray("○")
        priority = dim(f"P{ft.priority}")
        print(f"    {icon} {cyan('#{}'.format(ft.id))} {priority} {ft.description}")
        if ft.passes and ft.verified_at:
            print("      " + gray("验证于 {} (Session {})".format(ft.verified_at, ft.session_id)))

    stats = _active_harness.feature_mgr.get_stats()
    print()
    bar = progress_bar(stats["passed"], stats["total"])
    print(f"    {bar}  {stats['passed']}/{stats['total']}")
    print()


def _cmd_verify(sub: str):
    if not _active_harness:
        info("无活跃长任务")
        print()
        return
    verify_parts = sub.split()
    if len(verify_parts) < 2 or not verify_parts[1].isdigit():
        hint("/task verify <feature_id>")
        print()
        return
    fid = int(verify_parts[1])
    _active_harness.feature_mgr.load()
    feature = None
    for ft in _active_harness.feature_mgr.features:
        if ft.id == fid:
            feature = ft
            break
    if not feature:
        error(f"Feature #{fid} 不存在")
        print()
        return
    info(f"验证 Feature #{fid}: {feature.description}")
    passed, detail = _active_harness.verifier.verify_feature(feature)
    if passed:
        _active_harness.feature_mgr.mark_passed(fid, "manual")
        success("验证通过")
    else:
        error(f"验证失败: {detail[:200]}")
    print()


def _cmd_config():
    if not _active_harness:
        info("无活跃长任务")
        print()
        return
    cfg = _active_harness.config
    section("长任务配置")
    print()
    kv_table([
        ("项目目录", cfg.project_dir),
        ("需求", cfg.requirement[:80]),
        ("启动脚本", cfg.init_script),
        ("Feature 文件", cfg.feature_file),
        ("进度文件", cfg.progress_file),
        ("验证命令", cfg.verify_command or dim("(Agent 自验证)")),
        ("健康检查", cfg.smoke_test_command or dim("(无)")),
        ("自动 Git", green("是") if cfg.auto_commit else gray("否")),
    ])
    print()


def _cmd_init(agent: Agent, requirement: str):
    global _active_harness
    project_dir = os.getcwd()

    print(f"\n  🏗️  初始化长任务")
    print(f"  📋 需求: {requirement[:80]}")
    print(f"  📂 目录: {project_dir}")

    verify_cmd = input("  验证命令 (留空=Agent自验证): ").strip()
    smoke_cmd = input("  健康检查命令 (留空=跳过): ").strip()

    config = TaskConfig(
        project_dir=project_dir,
        requirement=requirement,
        verify_command=verify_cmd,
        smoke_test_command=smoke_cmd,
    )

    _active_harness = TaskHarness(agent, config)

    if _active_harness.is_initialized():
        print("  ℹ️  项目已初始化，加载现有状态")
        _active_harness.feature_mgr.load()
        _active_harness.progress.load()
        stats = _active_harness.feature_mgr.get_stats()
        print(f"  📊 进度: {stats['passed']}/{stats['total']} ({stats['progress_pct']}%)")
        choice = input("  继续 session? [y/N]: ").strip().lower()
        if choice in ("y", "yes"):
            _active_harness.run_session()
    else:
        confirm = input("  开始初始化? [y/N]: ").strip().lower()
        if confirm in ("y", "yes"):
            _active_harness.initialize()
            start = input("  开始第一个 session? [y/N]: ").strip().lower()
            if start in ("y", "yes"):
                _active_harness.run_session()
    print()
