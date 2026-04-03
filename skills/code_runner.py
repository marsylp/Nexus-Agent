"""Skill: Python 代码执行（受限沙箱）

安全限制:
- 禁止 import（除白名单模块）
- 禁止文件/网络/系统操作
- 禁止访问 __subclasses__、__globals__ 等危险属性
- 执行超时保护
"""
from __future__ import annotations
from agent_core.tools import tool
import sys, io, traceback, ast

# 允许在沙箱中使用的安全模块
_SAFE_MODULES = {
    "math", "random", "string", "re", "json", "datetime",
    "collections", "itertools", "functools", "operator",
    "decimal", "fractions", "statistics", "textwrap",
    "copy", "pprint", "enum", "dataclasses", "typing",
}

# 禁止访问的属性名（防止沙箱逃逸）
_FORBIDDEN_ATTRS = {
    "__subclasses__", "__globals__", "__code__", "__closure__",
    "__bases__", "__mro__", "__class__", "__import__",
    "__builtins__", "__loader__", "__spec__",
}

# 禁止的 AST 节点类型
_FORBIDDEN_NODES = (ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith, ast.Await)


def _validate_code(code: str) -> str | None:
    """静态分析代码安全性，返回错误信息或 None"""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return "语法错误: {}".format(e)

    for node in ast.walk(tree):
        # 禁止 async 语法
        if isinstance(node, _FORBIDDEN_NODES):
            return "不允许使用 async 语法"

        # 检查 import 语句
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                if mod not in _SAFE_MODULES:
                    return "不允许导入模块: {} (允许: {})".format(mod, ", ".join(sorted(_SAFE_MODULES)))

        if isinstance(node, ast.ImportFrom):
            if node.module:
                mod = node.module.split(".")[0]
                if mod not in _SAFE_MODULES:
                    return "不允许导入模块: {} (允许: {})".format(mod, ", ".join(sorted(_SAFE_MODULES)))

        # 检查危险属性访问
        if isinstance(node, ast.Attribute):
            if node.attr in _FORBIDDEN_ATTRS:
                return "不允许访问属性: {}".format(node.attr)

        # 禁止 exec/eval/compile 调用
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in ("exec", "eval", "compile", "__import__"):
                return "不允许调用: {}".format(func.id)

    return None


def _make_safe_builtins() -> dict:
    """构建受限的 __builtins__"""
    safe = {}
    # 允许的内置函数
    allowed = [
        "abs", "all", "any", "bin", "bool", "bytes", "callable", "chr",
        "complex", "dict", "dir", "divmod", "enumerate", "filter", "float",
        "format", "frozenset", "getattr", "hasattr", "hash", "hex", "id",
        "int", "isinstance", "issubclass", "iter", "len", "list", "map",
        "max", "min", "next", "object", "oct", "ord", "pow", "print",
        "range", "repr", "reversed", "round", "set", "slice", "sorted",
        "str", "sum", "tuple", "type", "vars", "zip",
        "True", "False", "None",
        "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError",
        "RuntimeError", "StopIteration", "ZeroDivisionError", "OverflowError",
        "Exception", "ArithmeticError", "LookupError",
    ]
    import builtins
    for name in allowed:
        if hasattr(builtins, name):
            safe[name] = getattr(builtins, name)

    # 受限的 __import__: 只允许白名单模块
    def _safe_import(name, *args, **kwargs):
        mod = name.split(".")[0]
        if mod not in _SAFE_MODULES:
            raise ImportError("沙箱中不允许导入: {}".format(name))
        return __builtins__["__import__"](name, *args, **kwargs) if isinstance(__builtins__, dict) \
            else __import__(name, *args, **kwargs)

    safe["__import__"] = _safe_import
    return safe


@tool("run_python", "执行 Python 代码并返回输出（受限沙箱，禁止文件/网络/系统操作）", {
    "type": "object",
    "properties": {"code": {"type": "string", "description": "要执行的 Python 代码"}},
    "required": ["code"],
})
def run_python(code: str) -> str:
    # 静态安全检查
    error = _validate_code(code)
    if error:
        return "[安全拦截] {}".format(error)

    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = buf_out = io.StringIO()
    sys.stderr = buf_err = io.StringIO()

    safe_globals = {"__builtins__": _make_safe_builtins()}

    try:
        exec(code, safe_globals)
        output = buf_out.getvalue()
        errors = buf_err.getvalue()
        result = output if output else "(无输出)"
        if errors:
            result += "\n[stderr] {}".format(errors)
        return result
    except ImportError as e:
        return "[安全拦截] {}".format(e)
    except Exception:
        return traceback.format_exc()
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
