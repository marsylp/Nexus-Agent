"""Skill 自动发现与加载 — 支持资格检查"""
from __future__ import annotations
import importlib, os, glob, shutil, platform


# ── Skill 元数据 ────────────────────────────────────────────
# 每个 skill 可以在模块级定义 SKILL_META 字典
# SKILL_META = {
#     "requires_bins": ["ffmpeg"],       # 需要的外部命令
#     "requires_env": ["DOUYIN_COOKIE"], # 需要的环境变量
#     "os": ["linux", "darwin"],         # 支持的操作系统
# }

def _check_skill_eligible(module) -> tuple[bool, str]:
    """检查 skill 是否满足运行条件"""
    meta = getattr(module, "SKILL_META", None)
    if not meta:
        return True, ""

    # 检查操作系统
    required_os = meta.get("os")
    if required_os:
        current_os = platform.system().lower()
        if current_os not in [o.lower() for o in required_os]:
            return False, "不支持当前操作系统 {} (需要 {})".format(current_os, required_os)

    # 检查外部命令
    for bin_name in meta.get("requires_bins", []):
        if not shutil.which(bin_name):
            return False, "缺少外部命令: {}".format(bin_name)

    # 检查环境变量
    for env_name in meta.get("requires_env", []):
        val = os.environ.get(env_name, "")
        if not val or val in ("xxx", "sk-xxx", ""):
            return False, "缺少环境变量: {}".format(env_name)

    return True, ""


def load_all_skills():
    """扫描 skills/ 目录，自动导入所有 skill 模块（带资格检查）"""
    skill_dir = os.path.dirname(__file__)
    loaded = []
    skipped = []
    for path in sorted(glob.glob(os.path.join(skill_dir, "*.py"))):
        name = os.path.basename(path)[:-3]
        if name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"skills.{name}")
            eligible, reason = _check_skill_eligible(mod)
            if eligible:
                loaded.append(name)
            else:
                skipped.append((name, reason))
                # 卸载不合格的 skill（防止注册无效工具）
                _unregister_skill_tools(mod)
        except Exception as e:
            print(f"  ⚠️  skill [{name}] 加载失败: {e}")
    if skipped:
        for name, reason in skipped:
            print(f"  ⏭️  skill [{name}] 跳过: {reason}")
    return loaded


def _unregister_skill_tools(module):
    """卸载 skill 注册的工具"""
    try:
        from agent_core.tools import get_registry
        registry = get_registry()
        # 查找该模块注册的工具并移除
        to_remove = []
        for tool_name, entry in registry.items():
            fn = entry.get("function")
            if fn and getattr(fn, "__module__", "") == module.__name__:
                to_remove.append(tool_name)
        for name in to_remove:
            del registry[name]
    except Exception:
        pass
