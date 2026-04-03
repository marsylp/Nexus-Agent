"""Agency Agents 命令 — /agency

子命令:
  /agency           — 查看当前匹配的专家角色
  /agency list      — 列出所有可用角色
  /agency list <分类> — 列出指定分类的角色
  /agency use <名称> — 手动切换到指定角色
  /agency reset     — 重置角色匹配（恢复自动匹配）
  /agency reload    — 重新扫描 agency-agents 目录
"""
from __future__ import annotations
from agent_core.agency_agents import get_agency_loader, get_agency_matcher
from agent_core.ui import section, item, success, info, warn, hint, cyan, gray, dim


def handle_agency(user_input: str, agent=None):
    """处理 /agency 命令"""
    parts = user_input.strip().split(maxsplit=2)
    sub = parts[1] if len(parts) > 1 else ""

    loader = get_agency_loader()
    matcher = get_agency_matcher()

    if not loader.available:
        warn("未找到 agency-agents 目录")
        hint("设置环境变量 AGENCY_AGENTS_DIR 指向 agency-agents 仓库路径")
        print()
        return

    if sub == "list":
        category_filter = parts[2] if len(parts) > 2 else None
        _list_roles(loader, category_filter)
    elif sub == "use" and len(parts) > 2:
        _use_role(parts[2], loader, matcher, agent)
    elif sub == "reset":
        matcher.reset()
        if agent:
            agent._agency_role_content = ""
            agent._rebuild_system_prompt()
        success("已重置角色匹配，恢复自动匹配模式")
        print()
    elif sub == "reload":
        loader.reload()
        success(f"已重新扫描，共 {len(loader.roles)} 个角色")
        print()
    else:
        _show_current(matcher)


def _show_current(matcher: "AgencyMatcher"):
    """显示当前匹配的角色"""
    section("Agency Agents 当前状态")
    role = matcher.current_role
    if role:
        item("当前角色", f"{role.emoji} {role.name}", icon="🎯")
        item("分类", role.category)
        item("描述", role.description[:80])
    else:
        info("当前无匹配角色（自动匹配模式，输入相关问题后自动切换）")
    print()


def _list_roles(loader: "AgencyAgentsLoader", category_filter: str | None):
    """列出所有角色"""
    roles = loader.roles
    if category_filter:
        roles = [r for r in roles if r.category.startswith(category_filter)]
        if not roles:
            warn(f"未找到分类: {category_filter}")
            # 列出可用分类
            categories = sorted(set(r.category.split("/")[0] for r in loader.roles))
            hint(f"可用分类: {', '.join(categories)}")
            print()
            return

    section(f"Agency Agents ({len(roles)} 个角色)")

    current_cat = ""
    for r in roles:
        cat = r.category
        if cat != current_cat:
            current_cat = cat
            print(f"\n    {cyan(cat)}")
        print(f"      {r.emoji} {r.name:30s} {gray(r.description[:50])}")

    print()
    hint("使用 /agency use <名称> 手动切换角色")
    print()


def _use_role(name: str, loader, matcher, agent):
    """手动切换到指定角色"""
    role = loader.get_role(name)
    if not role:
        # 模糊搜索
        candidates = [r for r in loader.roles if name.lower() in r.name.lower()
                      or name.lower() in r.filename.lower()]
        if candidates:
            warn(f"未找到精确匹配: {name}")
            hint("你是否想找:")
            for c in candidates[:5]:
                print(f"      {c.emoji} {c.name} ({c.category})")
        else:
            warn(f"未找到角色: {name}")
        print()
        return

    # 手动设置角色
    matcher._current_role = role
    matcher._current_score = 1.0  # 手动设置最高分，防止被自动匹配覆盖

    if agent:
        role_body = role.body
        if len(role_body) > 2000:
            role_body = role_body[:2000] + "\n\n[角色指南已截断，聚焦核心规则]"
        agent._agency_role_content = "[专家角色: {} {}]\n{}".format(
            role.emoji, role.name, role_body)
        agent._rebuild_system_prompt()

    success(f"已切换到: {role.emoji} {role.name}")
    info(f"分类: {role.category}")
    info(f"描述: {role.description[:80]}")
    print()
