"""Skill: 网页搜索（多后端，自动 fallback）

搜索顺序:
1. Bing 国内版（cn.bing.com）— 国内可直接访问
2. DuckDuckGo — 备用
3. 百度 — 最终 fallback

HTML 解析: 优先使用 BeautifulSoup（如已安装），回退到正则。
"""
from agent_core.tools import tool
import re

try:
    import requests as _req
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False
    import urllib.request, urllib.parse

try:
    from bs4 import BeautifulSoup as _BS
    _HAS_BS4 = True
except ImportError:
    _BS4 = None
    _HAS_BS4 = False


def _http_get(url, headers=None, timeout=10):
    """统一 HTTP GET，优先用 requests（自带 SSL 证书）"""
    if _HAS_REQUESTS:
        resp = _req.get(url, headers=headers or {}, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    else:
        req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")


_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _search_bing(query):
    """Bing 国内版搜索"""
    import html as _html
    if _HAS_REQUESTS:
        url = "https://cn.bing.com/search?q=" + _req.utils.quote(query)
    else:
        url = "https://cn.bing.com/search?" + urllib.parse.urlencode({"q": query})
    raw = _http_get(url, headers=_HEADERS)

    if _HAS_BS4:
        soup = _BS(raw, "html.parser")
        clean = []
        for p in soup.find_all("p"):
            text = p.get_text(strip=True)
            if len(text) > 20 and "bing" not in text.lower() and "cookie" not in text.lower():
                clean.append(text)
            if len(clean) >= 5:
                break
        return clean

    # 回退: 正则解析
    results = re.findall(r'<p[^>]*>(.*?)</p>', raw, re.S)
    clean = []
    for r in results:
        text = re.sub(r'<.*?>', '', r).strip()
        text = _html.unescape(text)
        if len(text) > 20 and "bing" not in text.lower() and "cookie" not in text.lower():
            clean.append(text)
        if len(clean) >= 5:
            break
    return clean


def _search_duckduckgo(query):
    """DuckDuckGo 搜索"""
    if _HAS_REQUESTS:
        url = "https://html.duckduckgo.com/html/?q=" + _req.utils.quote(query)
    else:
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    html = _http_get(url, headers=_HEADERS)

    if _HAS_BS4:
        soup = _BS(html, "html.parser")
        results = []
        for el in soup.select(".result__snippet"):
            text = el.get_text(strip=True)
            if text:
                results.append(text)
            if len(results) >= 5:
                break
        return results

    results = re.findall(r'class="result__snippet">(.*?)</a>', html, re.S)
    return [re.sub(r'<.*?>', '', r).strip() for r in results[:5]]


def _search_baidu(query):
    """百度搜索 — 最终 fallback"""
    if _HAS_REQUESTS:
        url = "https://www.baidu.com/s?wd=" + _req.utils.quote(query)
    else:
        url = "https://www.baidu.com/s?" + urllib.parse.urlencode({"wd": query})
    html = _http_get(url, headers=_HEADERS)

    if _HAS_BS4:
        soup = _BS(html, "html.parser")
        results = []
        for el in soup.select(".c-abstract, [class*='content-right']"):
            text = el.get_text(strip=True)
            if len(text) > 10:
                results.append(text)
            if len(results) >= 5:
                break
        return results

    # 回退: 正则
    results = re.findall(r'<span class="content-right_[^"]*">(.*?)</span>', html, re.S)
    if not results:
        results = re.findall(r'<div class="c-abstract[^"]*"[^>]*>(.*?)</div>', html, re.S)
    return [re.sub(r'<.*?>', '', r).strip() for r in results[:5] if len(re.sub(r'<.*?>', '', r).strip()) > 10]


@tool("web_search", "搜索互联网获取实时信息（天气、新闻、价格等）", {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "搜索关键词"}},
    "required": ["query"],
})
def web_search(query: str) -> str:
    engines = [
        ("Bing", _search_bing),
        ("DuckDuckGo", _search_duckduckgo),
        ("Baidu", _search_baidu),
    ]
    last_err = ""
    for name, fn in engines:
        try:
            results = fn(query)
            if results:
                header = "[来源: {}]\n".format(name)
                return header + "\n".join("{}. {}".format(i + 1, r) for i, r in enumerate(results))
        except Exception as e:
            last_err = "{}: {}".format(name, str(e)[:80])
            continue
    return "搜索失败 ({}). 请检查网络连接".format(last_err)
