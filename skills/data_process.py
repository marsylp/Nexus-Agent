"""Skill: 数据处理（CSV、排序、去重、统计）"""
from agent_core.tools import tool
import json, csv, io


@tool("csv_parse", "解析 CSV 文本，返回结构化数据", {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "CSV 文本内容"},
        "limit": {"type": "integer", "description": "返回行数限制（默认20）"},
    },
    "required": ["text"],
})
def csv_parse(text: str, limit: int = 20) -> str:
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = []
        for i, row in enumerate(reader):
            if i >= limit:
                break
            rows.append(dict(row))
        result = json.dumps(rows, ensure_ascii=False, indent=2)
        total = text.count("\n")
        if total > limit:
            result += f"\n...(共约 {total} 行，显示前 {limit} 行)"
        return result
    except Exception as e:
        return f"CSV 解析失败: {e}"


@tool("sort_data", "对列表数据排序", {
    "type": "object",
    "properties": {
        "data": {"type": "string", "description": "JSON 数组字符串"},
        "key": {"type": "string", "description": "排序字段（对象数组时使用）"},
        "reverse": {"type": "boolean", "description": "是否降序（默认升序）"},
    },
    "required": ["data"],
})
def sort_data(data: str, key: str = "", reverse: bool = False) -> str:
    try:
        items = json.loads(data)
        if key and isinstance(items[0], dict):
            items.sort(key=lambda x: x.get(key, ""), reverse=reverse)
        else:
            items.sort(reverse=reverse)
        return json.dumps(items, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"排序失败: {e}"


@tool("deduplicate", "列表去重", {
    "type": "object",
    "properties": {"data": {"type": "string", "description": "JSON 数组字符串"}},
    "required": ["data"],
})
def deduplicate(data: str) -> str:
    try:
        items = json.loads(data)
        seen = set()
        result = []
        for item in items:
            key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                result.append(item)
        return json.dumps(result, ensure_ascii=False, indent=2) + f"\n(原 {len(items)} 项 → 去重后 {len(result)} 项)"
    except Exception as e:
        return f"去重失败: {e}"
