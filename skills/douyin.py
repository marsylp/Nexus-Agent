"""Skill: 抖音热搜（多源聚合，无需 API Key）

数据源:
1. 聚合热榜 API — 抖音热搜词
2. 抖音网页版 — 热点榜/种草榜
3. 头条热榜 API — 综合热点（备用）

Token 优化:
- URL 清理跟踪参数，避免 log_pb 等巨长参数浪费 token
- 输出只保留标题 + 热度值，不输出 URL（LLM 不需要转发 URL）
"""
from agent_core.tools import tool
import re, json

try:
    import requests as _req
except ImportError:
    _req = None

# SSL 验证: 默认开启，可通过环境变量 DOUYIN_SSL_VERIFY=0 关闭
import os as _os
_SSL_VERIFY = _os.environ.get("DOUYIN_SSL_VERIFY", "1") != "0"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.douyin.com/",
}


def _clean_url(url):
    """清理 URL 中的跟踪参数，大幅减少 token 消耗"""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
        parsed = urlparse(url)
        # 移除跟踪参数
        skip_params = {
            "log_pb", "utm_source", "utm_medium", "utm_campaign",
            "utm_content", "utm_term", "spm", "scm", "from",
            "enter_from", "event_type", "hot_board_impr_id",
        }
        params = parse_qs(parsed.query)
        clean = {k: v[0] for k, v in params.items() if k not in skip_params}
        new_query = urlencode(clean) if clean else ""
        return urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url


def _format_hot(value):
    """格式化热度值为可读形式: 22793981 → 2279万"""
    if not value:
        return ""
    try:
        n = int(value)
        if n >= 100000000:
            return "{:.1f}亿".format(n / 100000000)
        if n >= 10000:
            return "{}万".format(n // 10000)
        return str(n)
    except (ValueError, TypeError):
        return str(value)


# ── 数据源 1: 聚合热榜 API ──────────────────────────────────

def _fetch_hotlist_api(limit=20):
    """通过聚合热榜 API 获取抖音热搜"""
    urls = [
        "https://blog.chrison.cn/hotlist.php?type=douyin",
    ]
    for url in urls:
        try:
            resp = _req.get(url, headers=_HEADERS, timeout=8, verify=_SSL_VERIFY)
            data = resp.json()
            if not data.get("success"):
                continue
            results = []
            for item in data.get("data", [])[:limit]:
                title = item.get("title", "")
                hot = item.get("hot", item.get("hotScore", ""))
                if title:
                    results.append({"title": title, "hot": str(hot) if hot else ""})
            if results:
                return results
        except Exception:
            continue
    return []


# ── 数据源 2: 抖音网页版 SSR 数据 ───────────────────────────

def _fetch_douyin_web(limit=20):
    """从抖音网页版提取热搜数据（SSR 渲染数据）"""
    try:
        resp = _req.get(
            "https://www.douyin.com/discover",
            headers=_HEADERS,
            timeout=10,
            verify=_SSL_VERIFY,
        )
        html = resp.text

        # 尝试从 RENDER_DATA 中提取
        match = re.search(
            r'<script\s+id="RENDER_DATA"[^>]*>(.*?)</script>', html, re.S
        )
        if match:
            import urllib.parse
            raw = urllib.parse.unquote(match.group(1))
            data = json.loads(raw)
            results = _extract_from_render_data(data, limit)
            if results:
                return results

        # 尝试从 SSR_HYDRATED_DATA 中提取
        match = re.search(
            r'window\._SSR_HYDRATED_DATA\s*=\s*(\{.*?\})\s*;?\s*</script>',
            html, re.S,
        )
        if match:
            data = json.loads(match.group(1))
            results = _extract_from_render_data(data, limit)
            if results:
                return results

        # 尝试正则直接提取热搜词
        titles = re.findall(r'"word"\s*:\s*"([^"]+)"', html)
        if titles:
            return [{"title": t, "hot": ""} for t in titles[:limit]]

    except Exception:
        pass
    return []


def _extract_from_render_data(data, limit):
    """递归从渲染数据中提取热搜列表"""
    results = []

    def _walk(obj):
        if isinstance(obj, dict):
            if "word" in obj and isinstance(obj["word"], str) and len(obj["word"]) > 1:
                hot_val = obj.get("hot_value", obj.get("hotValue",
                                  obj.get("score", "")))
                results.append({
                    "title": obj["word"],
                    "hot": str(hot_val) if hot_val else "",
                })
            else:
                for v in obj.values():
                    _walk(v)
                    if len(results) >= limit:
                        return
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)
                if len(results) >= limit:
                    return

    _walk(data)
    return results[:limit]


# ── 数据源 3: 头条热榜（备用）──────────────────────────────

def _fetch_toutiao_douyin(limit=20):
    """从头条热榜获取综合热点（备用源）"""
    try:
        url = "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"
        resp = _req.get(url, headers=_HEADERS, timeout=8)
        data = resp.json()
        results = []
        for item in data.get("data", [])[:limit]:
            title = item.get("Title", "")
            hot = item.get("HotValue", "")
            if title:
                results.append({"title": title, "hot": str(hot)})
        return results
    except Exception:
        return []


# ── 工具注册 ────────────────────────────────────────────────

@tool("douyin_hot", "获取抖音热搜榜/热点内容", {
    "type": "object",
    "properties": {
        "limit": {
            "type": "integer",
            "description": "返回条数（默认20）",
        },
    },
    "required": [],
})
def douyin_hot(limit: int = 20) -> str:
    if _req is None:
        return "需要安装 requests 库: pip install requests"

    # 依次尝试多个数据源
    sources = [
        ("抖音热搜榜", _fetch_hotlist_api),
        ("抖音热点", _fetch_douyin_web),
        ("综合热榜", _fetch_toutiao_douyin),
    ]

    for src_name, fetch_fn in sources:
        try:
            items = fetch_fn(limit)
            if items:
                # 精简输出格式 — 只有标题和热度，不带 URL，节省 token
                lines = ["🔥 {} (共{}条)".format(src_name, len(items))]
                for i, item in enumerate(items):
                    hot_str = _format_hot(item.get("hot"))
                    hot_part = "  🔥{}".format(hot_str) if hot_str else ""
                    lines.append("{}. {}{}".format(i + 1, item["title"], hot_part))
                return "\n".join(lines)
        except Exception:
            continue

    return "抖音热搜获取失败，请稍后重试"
