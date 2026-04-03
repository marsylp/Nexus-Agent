"""Skill: 热点资讯（多源聚合，无需 API Key）

数据源:
1. 今日头条热榜 — 综合热点
2. 36氪快讯 — 科技/商业
3. Bing 新闻搜索 — 关键词搜索
4. 抖音热搜 — 短视频热点（通过 douyin_hot skill）

HTML 解析: 优先使用 BeautifulSoup（如已安装），回退到正则。
"""
from agent_core.tools import tool
import re

try:
    import requests as _req
except ImportError:
    _req = None

try:
    from bs4 import BeautifulSoup as _BS
    _HAS_BS4 = True
except ImportError:
    _BS = None
    _HAS_BS4 = False

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


def _fetch_toutiao(limit=10):
    """今日头条热榜"""
    url = "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"
    resp = _req.get(url, headers=_HEADERS, timeout=8)
    data = resp.json()
    results = []
    for item in data.get("data", [])[:limit]:
        title = item.get("Title", "")
        hot = item.get("HotValue", "")
        if title:
            results.append({"title": title, "url": "", "hot": hot})
    return results


def _fetch_36kr(limit=10):
    """36氪快讯"""
    url = "https://36kr.com/newsflashes"
    resp = _req.get(url, headers=_HEADERS, timeout=8)
    html = resp.text

    if _HAS_BS4:
        soup = _BS(html, "html.parser")
        results = []
        for el in soup.select("[class*='newsflash-item-title']"):
            text = el.get_text(strip=True)
            if text:
                results.append({"title": text, "url": "", "hot": ""})
            if len(results) >= limit:
                break
        return results

    # 回退: 正则
    titles = re.findall(r'class="newsflash-item-title[^"]*"[^>]*>(.*?)</a>', html, re.S)
    results = []
    for t in titles[:limit]:
        text = re.sub(r'<.*?>', '', t).strip()
        if text:
            results.append({"title": text, "url": "", "hot": ""})
    return results


def _search_bing_news(query, limit=10):
    """Bing 新闻搜索"""
    import html as _html
    url = "https://cn.bing.com/news/search?q=" + _req.utils.quote(query)
    resp = _req.get(url, headers=_HEADERS, timeout=8)
    html_text = resp.text

    if _HAS_BS4:
        soup = _BS(html_text, "html.parser")
        results = []
        for el in soup.select(".title"):
            text = el.get_text(strip=True)
            if text and len(text) > 5:
                results.append({"title": text, "url": "", "hot": ""})
            if len(results) >= limit:
                break
        return results

    # 回退: 正则
    titles = re.findall(r'class="title"[^>]*>(.*?)</a>', html_text, re.S)
    results = []
    for t in titles[:limit]:
        text = re.sub(r'<.*?>', '', t).strip()
        text = _html.unescape(text)
        if text and len(text) > 5:
            results.append({"title": text, "url": "", "hot": ""})
    return results


@tool("news", "获取热点资讯或搜索新闻", {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "搜索关键词（留空则返回热榜）",
        },
        "source": {
            "type": "string",
            "description": "数据源: toutiao(默认), 36kr, bing, douyin(抖音热搜)",
            "enum": ["toutiao", "36kr", "bing", "douyin"],
        },
        "limit": {
            "type": "integer",
            "description": "返回条数（默认10）",
        },
    },
    "required": [],
})
def news(query: str = "", source: str = "toutiao", limit: int = 10) -> str:
    if _req is None:
        return "需要安装 requests 库: pip install requests"

    try:
        if source == "douyin":
            # 委托给 douyin_hot skill
            from skills.douyin import douyin_hot
            return douyin_hot(limit)
        elif query and source != "toutiao":
            # 有搜索词时用 Bing 新闻
            items = _search_bing_news(query, limit)
            src_name = "Bing新闻"
        elif source == "36kr":
            items = _fetch_36kr(limit)
            src_name = "36氪快讯"
        else:
            # 默认头条热榜
            if query:
                items = _search_bing_news(query, limit)
                src_name = "Bing新闻"
            else:
                items = _fetch_toutiao(limit)
                src_name = "今日头条热榜"

        if not items:
            return "未获取到资讯，请稍后重试"

        lines = ["📰 {} (共{}条)".format(src_name, len(items)), ""]
        for i, item in enumerate(items):
            hot_str = "  🔥{}".format(item["hot"]) if item.get("hot") else ""
            lines.append("{}. {}{}".format(i + 1, item["title"], hot_str))

        return "\n".join(lines)

    except Exception as e:
        # fallback: 尝试其他源
        try:
            if source == "toutiao":
                items = _fetch_36kr(limit)
                src_name = "36氪快讯(备用)"
            else:
                items = _fetch_toutiao(limit)
                src_name = "今日头条热榜(备用)"
            if items:
                lines = ["📰 {} (共{}条)".format(src_name, len(items)), ""]
                for i, item in enumerate(items):
                    lines.append("{}. {}".format(i + 1, item["title"]))
                return "\n".join(lines)
        except Exception:
            pass
        return "资讯获取失败: {}".format(e)
