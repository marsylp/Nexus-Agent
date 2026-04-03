"""工具注册表 — Agent 的工具系统核心"""
from __future__ import annotations
import json, subprocess, math
from typing import Callable, Any

# ── 工具注册表 ──────────────────────────────────────────────
_REGISTRY: dict[str, dict] = {}


def tool(name: str, description: str, parameters: dict):
    """装饰器：将函数注册为 Agent 可调用的工具

    用法:
        @tool("my_tool", "工具描述", {"type": "object", "properties": {...}})
        def my_tool(arg1: str) -> str:
            return "result"
    """
    def decorator(fn: Callable[..., str]):
        _REGISTRY[name] = {
            "function": fn,
            "spec": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            },
        }
        return fn
    return decorator


def get_tool_specs() -> list[dict]:
    """获取所有已注册工具的 OpenAI function calling 格式定义"""
    return [v["spec"] for v in _REGISTRY.values()]


def call_tool(name: str, arguments: dict[str, Any]) -> str:
    """按名称调用工具"""
    entry = _REGISTRY.get(name)
    if not entry:
        return f"[错误] 未知工具: {name}"
    try:
        return str(entry["function"](**arguments))
    except Exception as e:
        return f"[工具执行错误] {e}"


def get_registry() -> dict[str, dict]:
    """获取工具注册表引用（供 MCP 管理器等外部模块注册工具）"""
    return _REGISTRY


# ── 内置工具 ────────────────────────────────────────────────

@tool("calculator", "计算数学表达式，支持 +、-、*、/、**、sqrt 等", {
    "type": "object",
    "properties": {"expression": {"type": "string", "description": "Python 数学表达式"}},
    "required": ["expression"],
})
def calculator(expression: str) -> str:
    return str(_safe_eval(expression))


def _safe_eval(expr: str):
    """AST 安全求值 — 只允许数字、运算符和白名单函数调用"""
    import ast as _ast

    _SAFE_NAMES = {
        "pi": math.pi, "e": math.e, "inf": math.inf, "nan": math.nan,
        "True": True, "False": False,
    }
    _SAFE_FUNCS = {
        "sqrt": math.sqrt, "abs": abs, "round": round, "int": int, "float": float,
        "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "log": math.log, "log2": math.log2, "log10": math.log10,
        "ceil": math.ceil, "floor": math.floor, "pow": pow,
        "min": min, "max": max, "sum": sum,
        "factorial": math.factorial, "gcd": math.gcd,
    }

    tree = _ast.parse(expr, mode="eval")

    def _eval_node(node):
        if isinstance(node, _ast.Expression):
            return _eval_node(node.body)
        if isinstance(node, _ast.Constant):
            if isinstance(node.value, (int, float, complex, bool)):
                return node.value
            raise ValueError("不允许的常量类型: {}".format(type(node.value).__name__))
        if isinstance(node, _ast.Name):
            if node.id in _SAFE_NAMES:
                return _SAFE_NAMES[node.id]
            raise ValueError("未知变量: {}".format(node.id))
        if isinstance(node, _ast.UnaryOp):
            operand = _eval_node(node.operand)
            if isinstance(node.op, _ast.UAdd):
                return +operand
            if isinstance(node.op, _ast.USub):
                return -operand
            raise ValueError("不支持的一元运算符")
        if isinstance(node, _ast.BinOp):
            left = _eval_node(node.left)
            right = _eval_node(node.right)
            ops = {
                _ast.Add: lambda a, b: a + b,
                _ast.Sub: lambda a, b: a - b,
                _ast.Mult: lambda a, b: a * b,
                _ast.Div: lambda a, b: a / b,
                _ast.FloorDiv: lambda a, b: a // b,
                _ast.Mod: lambda a, b: a % b,
                _ast.Pow: lambda a, b: a ** b,
            }
            op_fn = ops.get(type(node.op))
            if op_fn is None:
                raise ValueError("不支持的运算符: {}".format(type(node.op).__name__))
            return op_fn(left, right)
        if isinstance(node, _ast.Call):
            if not isinstance(node.func, _ast.Name):
                raise ValueError("只允许直接函数调用")
            fn = _SAFE_FUNCS.get(node.func.id)
            if fn is None:
                raise ValueError("不允许的函数: {}".format(node.func.id))
            args = [_eval_node(a) for a in node.args]
            return fn(*args)
        if isinstance(node, _ast.List):
            return [_eval_node(e) for e in node.elts]
        if isinstance(node, _ast.Tuple):
            return tuple(_eval_node(e) for e in node.elts)
        raise ValueError("不支持的表达式类型: {}".format(type(node).__name__))

    return _eval_node(tree)

import re as _re, os as _os

# ── Shell 安全策略 ──────────────────────────────────────────

# 绝对禁止的命令模式（直接拦截，不可绕过）
_BLOCKED_PATTERNS = [
    (r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?/\s*$", "禁止删除根目录"),
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/", "禁止递归删除系统目录"),
    (r"\bmkfs\b", "禁止格式化磁盘"),
    (r"\bdd\s+.*of=/dev/", "禁止直接写入设备"),
    (r">\s*/dev/sd[a-z]", "禁止写入磁盘设备"),
    (r"\b:(){ :\|:& };:", "禁止 fork 炸弹"),
    (r"\bchmod\s+(-[a-zA-Z]*\s+)?777\s+/", "禁止对系统目录设置 777 权限"),
    (r"\bchown\s+.*\s+/", "禁止修改系统目录所有者"),
    (r"\bcurl\b.*\|\s*(ba)?sh", "禁止从网络管道执行脚本"),
    (r"\bwget\b.*\|\s*(ba)?sh", "禁止从网络管道执行脚本"),
    (r"\bshutdown\b|\breboot\b|\binit\s+[06]", "禁止关机/重启"),
    (r"\bnc\s+-[a-zA-Z]*l", "禁止监听端口（反弹 shell 风险）"),
]

# 高风险但可能合理的命令（警告，让用户确认）
_RISK_PATTERNS = [
    (r"\brm\s+-[a-zA-Z]*r", "递归删除文件"),
    (r"\brm\s+.*\*", "通配符删除"),
    (r"\bsudo\b", "需要 sudo 权限"),
    (r"\bkill\s+-9", "强制杀进程"),
    (r"\bpkill\b|\bkillall\b", "批量杀进程"),
    (r"\bchmod\b", "修改文件权限"),
    (r"\bchown\b", "修改文件所有者"),
    (r"\b>\s*/etc/", "写入系统配置"),
    (r"\biptables\b|\bufw\b", "修改防火墙规则"),
    (r"\bsystemctl\s+(stop|disable|mask)", "停止系统服务"),
]

# 允许的安全目录（shell 只能在这些目录下操作删除等危险命令）
_SAFE_DIRS = [_os.getcwd()]


def _check_command_safety(command: str) -> str | None:
    """检查命令是否被绝对禁止，返回拦截原因或 None"""
    for pattern, reason in _BLOCKED_PATTERNS:
        if _re.search(pattern, command):
            return reason
    return None


def _check_command_risk(command: str) -> str | None:
    """检查命令是否高风险，返回风险描述或 None"""
    for pattern, reason in _RISK_PATTERNS:
        if _re.search(pattern, command):
            return reason
    return None



@tool("shell", "在本地执行 shell 命令并返回输出（有安全限制，支持 Docker 沙箱）", {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "要执行的 shell 命令"},
        "cwd": {"type": "string", "description": "工作目录（可选，默认当前目录）"},
        "timeout": {"type": "integer", "description": "超时秒数（可选，默认30）"},
    },
    "required": ["command"],
})
def shell(command: str, cwd: str = None, timeout: int = 30) -> str:
    # -- 安全检查 --
    blocked = _check_command_safety(command)
    if blocked:
        _shell_audit("BLOCKED", command, blocked)
        return "[安全拦截] {}".format(blocked)

    risk = _check_command_risk(command)
    if risk:
        _shell_audit("RISK", command, risk)
        return "[需要确认] 该命令存在风险: {}。如确需执行，请明确告知。".format(risk)

    # Docker 沙箱模式
    sandbox = _os.environ.get("SHELL_SANDBOX", "").lower()
    if sandbox == "docker":
        return _run_in_docker(command, cwd, timeout)

    # 工作目录验证
    work_dir = cwd or _os.getcwd()
    if not _os.path.isdir(work_dir):
        return "[错误] 工作目录不存在: {}".format(work_dir)

    # 超时限制
    max_timeout = int(_os.environ.get("SHELL_MAX_TIMEOUT", "120"))
    timeout = min(timeout, max_timeout)

    # 环境变量隔离（移除敏感变量）
    safe_env = dict(_os.environ)
    for key in list(safe_env.keys()):
        if any(s in key.upper() for s in ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")):
            safe_env[key] = "***"

    try:
        r = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=work_dir, env=safe_env,
        )
        out = r.stdout.strip()
        err = r.stderr.strip()
        result = out if out else (err if err else "(无输出)")

        # 审计日志
        _shell_audit("OK" if r.returncode == 0 else "ERR:{}".format(r.returncode),
                     command, result[:100])

        # 返回码非零时附加错误信息
        if r.returncode != 0 and err and out:
            result = "{}\n[stderr] {}".format(out, err[:200])

        return result
    except subprocess.TimeoutExpired:
        _shell_audit("TIMEOUT", command, "{}s".format(timeout))
        return "[超时] 命令执行超过 {}s".format(timeout)
    except Exception as e:
        _shell_audit("ERROR", command, str(e))
        return "[执行错误] {}".format(e)


# -- Shell 审计日志 --
_shell_log = []

def _shell_audit(status, command, detail=""):
    import time as _time
    _shell_log.append({
        "time": _time.strftime("%H:%M:%S"),
        "status": status,
        "command": command[:100],
        "detail": str(detail)[:100],
    })
    if len(_shell_log) > 100:
        _shell_log.pop(0)

def get_shell_audit_log(last_n=20):
    """获取 Shell 审计日志"""
    return _shell_log[-last_n:]


# ── Docker 沙箱执行 ─────────────────────────────────────────

_DOCKER_IMAGE = _os.environ.get("SHELL_SANDBOX_IMAGE", "python:3.11-slim")


def _run_in_docker(command: str, cwd: str = None, timeout: int = 30) -> str:
    """在 Docker 容器中执行命令（隔离环境）"""
    work_dir = cwd or _os.getcwd()
    docker_cmd = [
        "docker", "run", "--rm",
        "--network=none",           # 禁止网络
        "--memory=256m",            # 内存限制
        "--cpus=0.5",               # CPU 限制
        "-v", "{}:/workspace:ro".format(work_dir),  # 只读挂载
        "-w", "/workspace",
        _DOCKER_IMAGE,
        "sh", "-c", command,
    ]
    try:
        r = subprocess.run(
            docker_cmd, capture_output=True, text=True, timeout=timeout,
        )
        out = r.stdout.strip()
        err = r.stderr.strip()
        result = out if out else (err if err else "(无输出)")
        _shell_audit("DOCKER:{}".format(r.returncode), command, result[:100])
        if r.returncode != 0 and err and out:
            result = "{}\n[stderr] {}".format(out, err[:200])
        return result
    except FileNotFoundError:
        return "[错误] Docker 未安装，无法使用沙箱模式"
    except subprocess.TimeoutExpired:
        _shell_audit("DOCKER:TIMEOUT", command, "{}s".format(timeout))
        return "[超时] Docker 命令执行超过 {}s".format(timeout)
    except Exception as e:
        _shell_audit("DOCKER:ERROR", command, str(e))
        return "[Docker 执行错误] {}".format(e)
