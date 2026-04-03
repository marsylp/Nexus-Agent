"""Skill: 系统信息与环境"""
from agent_core.tools import tool
import platform, os, shutil


@tool("system_info", "获取当前系统信息（OS、CPU、内存、Python 版本等）", {
    "type": "object",
    "properties": {},
    "required": [],
})
def system_info() -> str:
    info = [
        f"系统: {platform.system()} {platform.release()}",
        f"架构: {platform.machine()}",
        f"Python: {platform.python_version()}",
        f"主机名: {platform.node()}",
        f"工作目录: {os.getcwd()}",
    ]
    # 磁盘
    try:
        usage = shutil.disk_usage("/")
        info.append(f"磁盘: {usage.used // (1024**3)}GB / {usage.total // (1024**3)}GB")
    except Exception:
        pass
    # 环境变量数量
    info.append(f"环境变量: {len(os.environ)} 个")
    return "\n".join(info)


@tool("get_env", "获取环境变量的值", {
    "type": "object",
    "properties": {"name": {"type": "string", "description": "环境变量名"}},
    "required": ["name"],
})
def get_env(name: str) -> str:
    # 安全：不暴露敏感 key
    sensitive = ["key", "secret", "token", "password", "passwd"]
    if any(s in name.lower() for s in sensitive):
        val = os.environ.get(name, "")
        return f"{name}={'***已隐藏***' if val else '(未设置)'}"
    return os.environ.get(name, f"(未设置: {name})")
