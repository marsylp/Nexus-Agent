"""Skill: 日期时间"""
from agent_core.tools import tool
from datetime import datetime


@tool("current_time", "获取当前日期和时间", {
    "type": "object",
    "properties": {},
    "required": [],
})
def current_time() -> str:
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S (%A)")
