"""Skill: 文件操作"""
from __future__ import annotations
from agent_core.tools import tool
import os


def _emit_file_event(event_type: str, file_path: str):
    """尝试通过 HookManager 发出文件事件"""
    try:
        from agent_core.hooks import get_hook_manager
        mgr = get_hook_manager()
        mgr.emit(event_type, file_path=file_path)
    except Exception:
        pass


@tool("read_file", "读取文件内容", {
    "type": "object",
    "properties": {"path": {"type": "string", "description": "文件路径"}},
    "required": ["path"],
})
def read_file(path: str) -> str:
    try:
        with open(os.path.expanduser(path), "r", encoding="utf-8") as f:
            content = f.read()
        if len(content) > 5000:
            return content[:5000] + f"\n... (截断，共 {len(content)} 字符)"
        return content
    except Exception as e:
        return f"读取失败: {e}"


@tool("write_file", "写入内容到文件", {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "文件路径"},
        "content": {"type": "string", "description": "要写入的内容"},
    },
    "required": ["path", "content"],
})
def write_file(path: str, content: str) -> str:
    try:
        path = os.path.expanduser(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        is_new = not os.path.exists(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        _emit_file_event("fileCreated" if is_new else "fileEdited", path)
        return f"已写入 {len(content)} 字符到 {path}"
    except Exception as e:
        return f"写入失败: {e}"


@tool("list_dir", "列出目录内容", {
    "type": "object",
    "properties": {"path": {"type": "string", "description": "目录路径，默认当前目录"}},
    "required": [],
})
def list_dir(path: str = ".") -> str:
    try:
        entries = os.listdir(os.path.expanduser(path))
        return "\n".join(sorted(entries)) if entries else "(空目录)"
    except Exception as e:
        return f"列出失败: {e}"


@tool("delete_file", "删除文件", {
    "type": "object",
    "properties": {"path": {"type": "string", "description": "文件路径"}},
    "required": ["path"],
})
def delete_file(path: str) -> str:
    try:
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            return f"文件不存在: {path}"
        os.remove(path)
        _emit_file_event("fileDeleted", path)
        return f"已删除: {path}"
    except Exception as e:
        return f"删除失败: {e}"
