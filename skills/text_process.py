"""Skill: 文本与 JSON 处理"""
from agent_core.tools import tool
import json, re, hashlib


@tool("json_parse", "解析 JSON 字符串，支持 JSONPath 提取", {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "JSON 字符串"},
        "path": {"type": "string", "description": "提取路径，如 data.items[0].name（可选）"},
    },
    "required": ["text"],
})
def json_parse(text: str, path: str = "") -> str:
    try:
        data = json.loads(text)
        if not path:
            return json.dumps(data, ensure_ascii=False, indent=2)[:2000]
        # 简易 JSONPath
        for key in path.split("."):
            m = re.match(r"(\w+)\[(\d+)\]", key)
            if m:
                data = data[m.group(1)][int(m.group(2))]
            else:
                data = data[key]
        return json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, (dict, list)) else str(data)
    except Exception as e:
        return f"解析失败: {e}"


@tool("regex_match", "用正则表达式匹配文本", {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "待匹配文本"},
        "pattern": {"type": "string", "description": "正则表达式"},
    },
    "required": ["text", "pattern"],
})
def regex_match(text: str, pattern: str) -> str:
    try:
        matches = re.findall(pattern, text)
        if not matches:
            return "无匹配"
        return json.dumps(matches[:20], ensure_ascii=False)
    except Exception as e:
        return f"正则错误: {e}"


@tool("text_stats", "统计文本信息（字数、行数、词频等）", {
    "type": "object",
    "properties": {"text": {"type": "string", "description": "待统计文本"}},
    "required": ["text"],
})
def text_stats(text: str) -> str:
    lines = text.split("\n")
    words = text.split()
    chars = len(text)
    md5 = hashlib.md5(text.encode()).hexdigest()
    return (f"字符数: {chars}\n行数: {len(lines)}\n词数: {len(words)}\n"
            f"MD5: {md5}")


@tool("encode_decode", "文本编码/解码（base64、url、unicode）", {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "待处理文本"},
        "action": {"type": "string", "enum": ["base64_encode", "base64_decode", "url_encode", "url_decode"],
                   "description": "操作类型"},
    },
    "required": ["text", "action"],
})
def encode_decode(text: str, action: str) -> str:
    import base64, urllib.parse
    try:
        if action == "base64_encode":
            return base64.b64encode(text.encode()).decode()
        elif action == "base64_decode":
            return base64.b64decode(text).decode()
        elif action == "url_encode":
            return urllib.parse.quote(text)
        elif action == "url_decode":
            return urllib.parse.unquote(text)
        return f"未知操作: {action}"
    except Exception as e:
        return f"处理失败: {e}"
