"""Skill: HTTP 请求"""
from agent_core.tools import tool
import urllib.request, urllib.parse, json as _json


@tool("http_get", "发送 HTTP GET 请求", {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "请求 URL"},
        "headers": {"type": "object", "description": "请求头（可选）"},
    },
    "required": ["url"],
})
def http_get(url: str, headers: dict = None) -> str:
    try:
        req = urllib.request.Request(url, headers=headers or {"User-Agent": "MiniAgent/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read().decode("utf-8", errors="ignore")
        return data[:3000] if len(data) > 3000 else data
    except Exception as e:
        return f"请求失败: {e}"


@tool("http_post", "发送 HTTP POST 请求", {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "请求 URL"},
        "body": {"type": "object", "description": "JSON 请求体"},
        "headers": {"type": "object", "description": "请求头（可选）"},
    },
    "required": ["url", "body"],
})
def http_post(url: str, body: dict, headers: dict = None) -> str:
    try:
        data = _json.dumps(body).encode("utf-8")
        h = {"Content-Type": "application/json", "User-Agent": "MiniAgent/1.0"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=data, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = resp.read().decode("utf-8", errors="ignore")
        return result[:3000] if len(result) > 3000 else result
    except Exception as e:
        return f"请求失败: {e}"
