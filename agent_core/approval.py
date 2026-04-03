"""工具审批系统 
- 工具分类: read/write/shell/web/spec
- preToolUse Hook 集成（Hook 可阻止工具执行）
- 分级审批: safe -> confirm -> dangerous
- 审批记录（审计日志）
- 批量放行模式（Autopilot）
- MCP autoApprove 集成
- Per-provider 工具策略（弱模型限制危险工具）
"""
from __future__ import annotations
import os, json, time
from typing import Optional

# -- 工具分类--
TOOL_CATEGORIES = {
    "read": ["calculator", "current_time", "system_info", "get_env",
             "text_stats", "json_parse", "regex_match", "encode_decode",
             "sort_data", "deduplicate", "csv_parse", "web_search",
             "read_file", "list_dir", "http_get"],
    "write": ["write_file", "shell", "run_python", "http_post"],
    "shell": ["shell"],
    "web": ["web_search", "http_get", "http_post"],
}


def get_tool_categories(tool_name):
    """获取工具所属分类"""
    cats = set()
    for cat, tools in TOOL_CATEGORIES.items():
        if tool_name in tools:
            cats.add(cat)
    if tool_name.startswith("mcp_"):
        cats.add("read")
    return cats or {"read"}


# -- 安全等级 --
TOOL_SAFETY = {
    "calculator": "safe", "current_time": "safe", "system_info": "safe",
    "get_env": "safe", "text_stats": "safe", "json_parse": "safe",
    "regex_match": "safe", "encode_decode": "safe", "sort_data": "safe",
    "deduplicate": "safe", "csv_parse": "safe", "web_search": "safe",
    "read_file": "safe", "list_dir": "safe", "http_get": "safe",
    "shell": "confirm", "write_file": "confirm",
    "run_python": "confirm", "http_post": "confirm",
}

_DANGEROUS_SHELL_KEYWORDS = [
    "rm ", "sudo ", "kill ", "pkill ", "chmod ", "chown ",
    "mv /", "cp /", "> /", ">> /", "pip install", "pip uninstall",
    "npm install", "apt ", "brew install", "brew uninstall",
]

# -- 审批日志 --
_audit_log = []
MAX_AUDIT_LOG = 200


def _log_audit(tool_name, args, decision, reason):
    """记录审批决策到审计日志"""
    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool": tool_name,
        "args_summary": json.dumps(args, ensure_ascii=False)[:100],
        "decision": decision,
        "reason": reason,
    }
    _audit_log.append(entry)
    if len(_audit_log) > MAX_AUDIT_LOG:
        _audit_log.pop(0)


def get_audit_log(last_n=20):
    """获取最近的审批日志"""
    return _audit_log[-last_n:]


def get_safety_level(tool_name, args=None):
    """获取工具的安全等级"""
    base_level = TOOL_SAFETY.get(tool_name, "safe")

    if tool_name == "shell" and args:
        cmd = args.get("command", "")
        for kw in _DANGEROUS_SHELL_KEYWORDS:
            if kw in cmd:
                return "dangerous"

    if tool_name.startswith("mcp_"):
        from agent_core.mcp_manager import is_mcp_auto_approved
        if is_mcp_auto_approved(tool_name):
            return "safe"
        return "confirm"

    return base_level


def is_auto_approved(tool_name):
    """检查工具是否在自动放行列表中"""
    auto_list = os.environ.get("AUTO_APPROVE", "").strip()
    if not auto_list:
        return False
    if auto_list == "*":
        return True
    approved = [t.strip() for t in auto_list.split(",")]
    # 支持分类放行: AUTO_APPROVE=read,web
    cats = get_tool_categories(tool_name)
    for a in approved:
        if a in cats:
            return True
        if a == tool_name:
            return True
    return False


def format_approval_prompt(tool_name, args, level):
    """格式化确认提示"""
    args_str = json.dumps(args, ensure_ascii=False, indent=2)
    cats = ",".join(sorted(get_tool_categories(tool_name)))
    sep = "=" * 50

    if level == "dangerous":
        return (
            "\n  ⚠️  {sep}\n"
            "  ⚠️  高风险操作！请仔细确认：\n"
            "  ⚠️  {sep}\n"
            "  工具: {tool} [{cats}]\n"
            "  参数: {args}\n"
            "  ⚠️  {sep}\n"
            "  确认执行? [y/N]: "
        ).format(sep=sep, tool=tool_name, cats=cats, args=args_str)
    else:
        return (
            "\n  🔒 需要确认:\n"
            "  工具: {tool} [{cats}]\n"
            "  参数: {args}\n"
            "  确认执行? [y/N]: "
        ).format(tool=tool_name, cats=cats, args=args_str)


def request_approval(tool_name, args):
    """请求用户确认

    Returns:
        (True, "approved") or (False, "reason")
    """
    level = get_safety_level(tool_name, args)

    if level == "safe":
        _log_audit(tool_name, args, "approved", "safe")
        return True, "safe"

    if is_auto_approved(tool_name):
        _log_audit(tool_name, args, "approved", "auto-approved")
        return True, "auto-approved"

    prompt = format_approval_prompt(tool_name, args, level)
    try:
        answer = input(prompt).strip().lower()
        if answer in ("y", "yes", "是"):
            _log_audit(tool_name, args, "approved", "user-approved")
            return True, "user-approved"
        _log_audit(tool_name, args, "denied", "user-denied")
        return False, "用户拒绝执行"
    except (EOFError, KeyboardInterrupt):
        _log_audit(tool_name, args, "denied", "user-cancel")
        return False, "用户取消"


# ── 多层工具安全策略管线 ─────────────────────────────────────
#
# 工具调用经过以下 9 层检查（任一层拒绝即终止）:
# L1: 命令级黑名单拦截 (shell _BLOCKED_PATTERNS)
# L2: 命令级风险检测 (shell _RISK_PATTERNS)
# L3: Per-provider 工具策略 (PROVIDER_TOOL_POLICY)
# L4: Tier 级隐式限制 (_TIER1_BLOCKED)
# L5: 工具安全等级 (TOOL_SAFETY: safe/confirm/dangerous)
# L6: MCP autoApprove 策略
# L7: AUTO_APPROVE 环境变量 / 分类放行
# L8: preToolUse Hook 拦截
# L9: 用户交互确认 (input prompt)
#
# L1-L2 在 tools.py shell() 内部执行
# L3-L4 在 check_provider_tool_policy() 执行
# L5-L7 在 request_approval() 执行
# L8 在 Agent._execute_tools_parallel() 的 _emit("preToolUse") 执行
# L9 在 request_approval() 的 input() 执行

PROVIDER_TOOL_POLICY: dict[str, dict] = {
    # "full": 所有工具可用
    # "restricted": 只允许 read 类工具
    # "no_shell": 禁止 shell/write，允许其他
    "ollama": {"profile": "restricted", "blocked": {"shell", "write_file", "run_python", "http_post"}},
    "silicon": {"profile": "no_shell", "blocked": {"shell", "run_python"}},
    # 其他 provider 默认 full
}

# Tier 1 模型自动限制（即使 provider 没有显式配置）
_TIER1_BLOCKED = {"shell", "run_python"}


def check_provider_tool_policy(tool_name: str, provider: str = "",
                                model: str = "", tier: int = 0) -> tuple[bool, str]:
    """L3+L4: 检查 provider 和 tier 级工具策略

    Returns:
        (allowed, reason)
    """
    # L3: 显式 provider 策略
    policy = PROVIDER_TOOL_POLICY.get(provider)
    if policy:
        blocked = policy.get("blocked", set())
        if tool_name in blocked:
            return False, "提供商 {} 的安全策略禁止使用 {}".format(provider, tool_name)

    # L4: Tier 1 隐式限制
    if tier == 1 and tool_name in _TIER1_BLOCKED:
        return False, "Tier 1 模型不允许使用 {} (安全限制)".format(tool_name)

    return True, "allowed"


def filter_tools_by_policy(tools: list[dict], provider: str = "",
                            tier: int = 0) -> list[dict]:
    """根据 provider 策略过滤工具列表（用于 Agent 初始化时裁剪可用工具）"""
    policy = PROVIDER_TOOL_POLICY.get(provider)
    blocked = set()
    if policy:
        blocked = policy.get("blocked", set())
    if tier == 1:
        blocked = blocked | _TIER1_BLOCKED

    if not blocked:
        return tools
    return [t for t in tools if t["function"]["name"] not in blocked]


def full_tool_check(tool_name: str, args: dict, provider: str = "",
                    tier: int = 0) -> tuple[bool, str, str]:
    """完整的 9 层工具安全检查（L3-L7，L1-L2 和 L8-L9 在各自位置执行）

    Returns:
        (allowed, reason, layer) — layer 标识哪一层拒绝
    """
    # L3+L4: provider/tier 策略
    allowed, reason = check_provider_tool_policy(tool_name, provider=provider, tier=tier)
    if not allowed:
        return False, reason, "L3/L4"

    # L5: 安全等级
    level = get_safety_level(tool_name, args)

    # L6: MCP autoApprove
    if tool_name.startswith("mcp_") and level == "safe":
        return True, "mcp-auto-approved", "L6"

    # L7: AUTO_APPROVE
    if level == "safe":
        return True, "safe", "L5"
    if is_auto_approved(tool_name):
        return True, "auto-approved", "L7"

    # 需要用户确认（L9 在调用方执行）
    return True, "needs-confirmation:{}".format(level), "L5"
