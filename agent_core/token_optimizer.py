"""Token 优化器 

增强:
- tool_calls 消息精确计数（function name + arguments 分别计数）
- image token 估算（基于分辨率）
- 动态预算调整（根据模型上下文窗口）
- 工具描述压缩（长描述截断）
- 更智能的分层记忆
"""
from __future__ import annotations
import os, json, hashlib

_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        import tiktoken
        try:
            _encoder = tiktoken.encoding_for_model("gpt-4o")
        except Exception:
            _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def count_tokens(text):
    if not text:
        return 0
    return len(_get_encoder().encode(text))


def count_image_tokens(width=1024, height=1024, detail="auto"):
    """估算图片 token 数（对标 OpenAI vision token 计算）"""
    if detail == "low":
        return 85
    # high detail: 按 512x512 tile 计算
    tiles_w = (width + 511) // 512
    tiles_h = (height + 511) // 512
    return 85 + 170 * tiles_w * tiles_h


def count_messages_tokens(messages, tools=None):
    """精确计算消息列表的 token 数

    增强: 正确处理 tool_calls 消息格式
    """
    total = 0
    for m in messages:
        total += 4  # message overhead

        # content
        content = m.get("content", "")
        if isinstance(content, str) and content:
            total += count_tokens(content)
        elif isinstance(content, list):
            # multimodal content (text + image)
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        total += count_tokens(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        total += count_image_tokens()
                elif isinstance(part, str):
                    total += count_tokens(part)

        # role
        total += 1

        # tool_calls (assistant message with function calls)
        tool_calls = m.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                total += 3  # tool_call overhead
                if isinstance(tc, dict):
                    fn = tc.get("function", {})
                    total += count_tokens(fn.get("name", ""))
                    total += count_tokens(fn.get("arguments", ""))
                else:
                    # OpenAI object format
                    if hasattr(tc, "function"):
                        total += count_tokens(getattr(tc.function, "name", ""))
                        total += count_tokens(getattr(tc.function, "arguments", ""))

        # tool_call_id (tool response message)
        if m.get("tool_call_id"):
            total += count_tokens(m["tool_call_id"])

        # name field
        if m.get("name"):
            total += count_tokens(m["name"]) + 1

    if tools:
        total += count_tokens(json.dumps(tools, ensure_ascii=False))

    return total + 2  # conversation overhead


# -- 配置 --
def _int_env(key, default):
    return int(os.environ.get(key, default))

# 模型上下文窗口大小
_MODEL_CONTEXT_WINDOWS = {
    "glm-4-flash": 8000,
    "glm-4-plus": 128000,
    "deepseek-chat": 64000,
    "gpt-4o-mini": 128000,
    "gpt-4o": 128000,
    "qwen2.5:7b": 8000,
}


def max_context_tokens():
    """动态预算: 优先用环境变量，否则按模型窗口的 80%"""
    env_val = os.environ.get("MAX_CONTEXT_TOKENS", "")
    if env_val:
        return int(env_val)
    # 尝试根据当前模型动态调整
    model = os.environ.get("AGENT_MODEL", "")
    if model in _MODEL_CONTEXT_WINDOWS:
        return int(_MODEL_CONTEXT_WINDOWS[model] * 0.8)
    return 4000


def max_tool_result_chars():
    return _int_env("MAX_TOOL_RESULT_CHARS", 2000)

def short_term_turns():
    return _int_env("SHORT_TERM_TURNS", 4)


# -- 工具结果压缩 --

# -- URL 清理（token 优化核心）--
# 本质问题: URL 中 percent-encoding 让 JSON 等内容体积膨胀数倍
# {"a":"b"} → %7B%22a%22%3A%22b%22%7D，每个特殊字符 1→3 字节，token 翻倍
# 策略: decode → 识别膨胀参数值（encoded JSON/长字符串）→ 移除

import re as _re_mod
from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs, \
    urlencode as _urlencode, urlunparse as _urlunparse, unquote as _unquote

_URL_PATTERN = _re_mod.compile(r'https?://[^\s\)\]\"\'<>]+')

# 单个参数值超过此长度视为"膨胀值"（通常是 encoded JSON、签名、trace id）
_PARAM_VALUE_MAX_LEN = 60


def _clean_url(url):
    """清理 URL: decode percent-encoding，移除膨胀参数值"""
    try:
        # 先 decode 整个 URL，让 %22 %7B 等还原
        decoded = _unquote(url)
        parsed = _urlparse(decoded)

        if not parsed.query:
            # 无参数，只要不超长就原样返回
            if len(decoded) <= 200:
                return decoded
            return "{}://{}{}".format(parsed.scheme, parsed.netloc,
                                      parsed.path[:80])

        # 逐个参数检查，移除值过长的（JSON blob、签名、trace 等）
        params = _parse_qs(parsed.query, keep_blank_values=False)
        clean = {}
        for k, v in params.items():
            val = v[0] if v else ""
            if len(val) <= _PARAM_VALUE_MAX_LEN:
                clean[k] = val
            # 超长值直接丢弃 — 这些几乎都是跟踪/签名数据

        if clean:
            new_query = _urlencode(clean)
            result = _urlunparse(parsed._replace(query=new_query))
        else:
            # 所有参数都是膨胀的，只保留路径
            result = _urlunparse(parsed._replace(query=""))

        # 最终长度兜底
        if len(result) > 200:
            result = "{}://{}{}".format(parsed.scheme, parsed.netloc,
                                        parsed.path[:80])
        return result
    except Exception:
        if len(url) > 200:
            return url[:120] + "..."
        return url


def _clean_urls_in_text(text):
    """清理文本中所有 URL — decode percent-encoding，移除膨胀参数"""
    if "http" not in text:
        return text
    return _URL_PATTERN.sub(lambda m: _clean_url(m.group(0)), text)


def compress_tool_result(result, tool_name=""):
    """压缩工具结果 — 结构化数据保留完整，其他按预算裁剪

    统一优化:
    1. URL decode + 移除膨胀参数（解决 percent-encoding token 翻倍问题）
    2. 结构化工具保留完整内容
    3. 其他工具按预算裁剪
    """
    # 第一步: 统一清理 URL（对所有工具生效）
    result = _clean_urls_in_text(result)

    limit = max_tool_result_chars()
    if len(result) <= limit:
        return result

    # 结构化信息工具 — 保留完整内容（这些结果需要 LLM 完整呈现给用户）
    if tool_name in ("news", "weather", "web_search", "system_info", "douyin_hot"):
        lines = result.strip().split("\n")
        if len(lines) <= 50:
            return result  # 50行以内不截断
        return "\n".join(lines[:40]) + "\n...(共{}条结果)".format(len(lines))

    if tool_name in ("shell", "list_dir"):
        lines = result.strip().split("\n")
        if len(lines) > 20:
            return "\n".join(lines[:15]) + "\n...(共{}行)".format(len(lines))
        return result[:limit]

    if tool_name == "read_file":
        lines = result.split("\n")
        head = "\n".join(lines[:20])
        tail = "\n".join(lines[-5:]) if len(lines) > 25 else ""
        r = head + ("\n...(共{}行)...\n{}".format(len(lines), tail) if tail else "")
        return r[:limit]

    # JSON 结果: 尝试保留结构
    if tool_name.startswith("mcp_") or tool_name in ("http_get", "http_post"):
        try:
            parsed = json.loads(result)
            compact = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            if len(compact) <= limit:
                return compact
        except (json.JSONDecodeError, TypeError):
            pass

    head = limit * 2 // 3
    tail = limit // 3
    return result[:head] + "\n...(省略{}字符)...\n".format(len(result) - head - tail) + result[-tail:]


# -- 工具描述压缩 --
def compress_tool_descriptions(tools, max_desc_tokens=100):
    """压缩过长的工具描述"""
    compressed = []
    for spec in tools:
        fn = spec.get("function", {})
        desc = fn.get("description", "")
        if count_tokens(desc) > max_desc_tokens:
            # 截断到 max_desc_tokens
            words = desc.split()
            truncated = []
            tokens = 0
            for w in words:
                tokens += count_tokens(w) + 1
                if tokens > max_desc_tokens:
                    break
                truncated.append(w)
            fn = dict(fn)
            fn["description"] = " ".join(truncated) + "..."
            spec = dict(spec)
            spec["function"] = fn
        compressed.append(spec)
    return compressed


# -- 分层记忆 --
def build_context(memory, llm_summarize_fn=None, hygiene=None, user_input=""):
    """构建上下文 — 集成上下文卫生系统

    参数:
    - hygiene: ContextHygiene 实例（可选，启用四大痛点防护）
    - user_input: 当前用户输入（用于动态调整短期记忆轮数）
    """
    if len(memory) <= 2:
        return memory[:]

    system = memory[0] if memory[0]["role"] == "system" else None
    rest = memory[1:] if system else memory[:]

    # 痛点二: 动态短期记忆轮数（替代固定值）
    turns = short_term_turns()
    if hygiene and user_input:
        turns = hygiene.get_adaptive_turns(memory, user_input)

    user_indices = [i for i, m in enumerate(rest) if m["role"] == "user"]
    if len(user_indices) <= turns:
        return memory[:]

    split_at = user_indices[-turns]
    long_term = rest[:split_at]
    short_term = rest[split_at:]

    # 痛点一: 有毒消息在摘要时被替代而非原文保留
    if hygiene:
        long_term = _detox_messages(long_term, hygiene, system_offset=1 if system else 0)

    # P3: 预压缩记忆冲刷 — 提取重要信息再丢弃
    important = _extract_important_info(long_term)

    if llm_summarize_fn and len(long_term) > 6:
        summary = _llm_summary(long_term, llm_summarize_fn)
    else:
        summary = _local_summary(long_term)

    # 将重要信息附加到摘要
    if important:
        summary = summary + "\n\n[重要信息]\n" + important if summary else "[重要信息]\n" + important

    # 痛点四: 冲突解决提示
    if hygiene:
        report = hygiene.scan_and_clean(memory)
        conflict_prompt = report.get("conflict_prompt")
        if conflict_prompt:
            summary = summary + "\n\n" + conflict_prompt if summary else conflict_prompt

    result = []
    if system:
        result.append(system)
    if summary:
        result.append({"role": "system", "content": summary})
    result.extend(short_term)
    return _enforce_token_budget(result, hygiene=hygiene)


def _extract_important_info(messages: list[dict]) -> str:
    """预压缩记忆冲刷 — 从即将丢弃的消息中提取重要信息

    提取规则:
    - 用户明确的偏好/指令 (如 "以后都用中文回答")
    - 关键决策 (如 "我们选择方案 A")
    - 文件路径、URL 等结构化信息
    - 错误和解决方案

    痛点一增强: 被纠正的决策不再提取
    """
    import re
    important = []

    # 偏好/指令模式
    preference_patterns = [
        r"以后.*(?:都|总是|一直|始终)",
        r"(?:记住|注意|请记得|别忘了).*",
        r"(?:不要|禁止|避免).*",
        r"(?:我的|我们的).*(?:是|为|叫)",
        r"(?:选择|决定|采用|使用).*(?:方案|方式|方法)",
    ]

    # 纠正模式 — 被纠正的内容不应提取
    correction_patterns = [
        re.compile(r"不对|错了|不是这样|你说错了|纠正|更正"),
        re.compile(r"其实|实际上|应该是|正确的是"),
    ]

    # 先标记哪些 assistant 消息被纠正了
    corrected_indices = set()
    for i, m in enumerate(messages):
        if m.get("role") == "user" and i > 0:
            content = m.get("content", "")
            if isinstance(content, str):
                for pat in correction_patterns:
                    if pat.search(content):
                        corrected_indices.add(i - 1)  # 标记前一条 assistant 消息
                        break

    for i, m in enumerate(messages):
        content = m.get("content", "")
        if not content or not isinstance(content, str):
            continue

        # 跳过被纠正的 assistant 消息
        if i in corrected_indices:
            continue

        if m["role"] == "user":
            for pat in preference_patterns:
                match = re.search(pat, content)
                if match:
                    # 提取匹配行
                    line = content[max(0, match.start() - 20):match.end() + 40].strip()
                    important.append("偏好: {}".format(line[:100]))
                    break

        elif m["role"] == "assistant":
            # 提取关键文件路径
            paths = re.findall(r'[\w./\\-]+\.(?:py|js|ts|json|yaml|yml|toml|md)', content)
            if paths:
                unique_paths = list(dict.fromkeys(paths))[:5]
                important.append("涉及文件: {}".format(", ".join(unique_paths)))

    # 去重并限制长度
    seen = set()
    deduped = []
    for item in important:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return "\n".join(deduped[:10])


def _llm_summary(messages, summarize_fn):
    lines = []
    for m in messages:
        role, content = m.get("role", ""), m.get("content", "")
        if role == "user" and content:
            lines.append("用户: {}".format(content[:100]))
        elif role == "assistant" and content:
            lines.append("助手: {}".format(content[:100]))
        elif role == "tool":
            lines.append("工具: {}".format(content[:60]))
    if not lines:
        return ""
    try:
        return "[对话历史摘要] {}".format(summarize_fn(chr(10).join(lines[-10:])))
    except Exception:
        return _local_summary(messages)


def _detox_messages(messages: list[dict], hygiene, system_offset: int = 0) -> list[dict]:
    """痛点一: 对有毒消息做消毒处理

    策略:
    - 工具失败的结果: 替换为简短的失败摘要
    - 被用户纠正的 assistant 回复: 替换为 "[此回复已被用户纠正，以用户后续指令为准]"
    - 其他有毒消息: 内容截断并标记
    """
    cleaned = []
    for i, msg in enumerate(messages):
        real_idx = i + system_offset
        if hygiene.detox.is_toxic(real_idx):
            marker = hygiene.detox.get_marker(real_idx)
            if marker and marker.severity == "high":
                # 高毒性: 替换内容
                cleaned.append({
                    **msg,
                    "content": "[已过滤: {}]".format(marker.reason),
                })
            elif marker and marker.severity == "medium":
                # 中毒性: 截断内容
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > 100:
                    cleaned.append({
                        **msg,
                        "content": content[:80] + "... [注: {}]".format(marker.reason),
                    })
                else:
                    cleaned.append(msg)
            else:
                cleaned.append(msg)
        else:
            cleaned.append(msg)
    return cleaned


def _local_summary(messages):
    exchanges, q = [], None
    for m in messages:
        if m["role"] == "user":
            q = (m.get("content") or "")[:50].replace("\n", " ")
        elif m["role"] == "assistant" and q:
            a = (m.get("content") or "").strip().split("\n")[0][:60]
            if a:
                exchanges.append("Q:{} -> A:{}".format(q, a))
            q = None
    return "[历史摘要]\n" + "\n".join(exchanges[-5:]) if exchanges else ""


def _enforce_token_budget(messages, hygiene=None):
    """强制 token 预算 — 支持基于重要性评分的智能裁剪

    痛点二增强: 不再简单地从前往后删，而是优先删除低分消息
    """
    budget = max_context_tokens()
    total = count_messages_tokens(messages)
    result = messages[:]

    if total <= budget:
        return result

    if hygiene:
        # 智能裁剪: 按重要性评分排序，优先删除低分消息
        scores = hygiene.score_messages(result)
        # 构建 (index, score) 列表，排除 system 消息
        candidates = [
            (i, scores[i]) for i in range(len(result))
            if result[i]["role"] != "system"
        ]
        # 按分数升序排列（低分优先删除）
        candidates.sort(key=lambda x: x[1])

        removed = set()
        for idx, _score in candidates:
            if total <= budget:
                break
            total -= count_tokens(json.dumps(result[idx], ensure_ascii=False))
            removed.add(idx)

        result = [m for i, m in enumerate(result) if i not in removed]
    else:
        # 回退: 原始策略
        while total > budget and len(result) > 2:
            for i, m in enumerate(result):
                if m["role"] != "system":
                    total -= count_tokens(json.dumps(m, ensure_ascii=False))
                    result.pop(i)
                    break
            else:
                break

    return result


# -- 工具缓存 + 去重 --
_tool_cache = {}
_tool_hash_map = {}


def cache_tools(tools):
    for spec in tools:
        name = spec["function"]["name"]
        h = hashlib.md5(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:8]
        _tool_cache[h] = spec
        _tool_hash_map[name] = h


def get_deduplicated_tools(names=None):
    seen, result = set(), []
    items = ((h, s) for h, s in _tool_cache.items()) if names is None else \
            ((_tool_hash_map[n], _tool_cache[_tool_hash_map[n]]) for n in names if n in _tool_hash_map)
    for h, spec in items:
        if h not in seen:
            seen.add(h)
            result.append(spec)
    return result


# -- 按需工具筛选 --
TOOL_KEYWORDS = {
    "calculator": ["算", "计算", "数学", "math", "calc", "加", "减", "乘", "除"],
    "shell": ["命令", "终端", "运行", "执行", "shell", "bash", "pip", "npm", "git"],
    "web_search": ["搜索", "查找", "search", "百度", "网上",
                   "最新", "查询", "哪里", "谁是",
                   "价格", "股票", "汇率", "推荐"],
    "weather": ["天气", "气温", "温度", "下雨", "下雪", "刮风", "weather",
                "预报", "穿什么", "带伞"],
    "news": ["新闻", "资讯", "热点", "热搜", "头条", "快讯", "热榜",
             "发生了什么", "最近", "今日"],
    "douyin_hot": ["抖音", "douyin", "短视频", "热搜", "热榜", "tiktok"],
    "read_file": ["读取", "打开", "查看文件", "read", "看看", "内容"],
    "write_file": ["写入", "保存", "创建文件", "write", "写到", "存到"],
    "list_dir": ["目录", "文件列表", "ls", "列出", "有哪些文件"],
    "current_time": ["时间", "日期", "几点", "今天", "time", "现在", "星期"],
    "run_python": ["python", "代码", "脚本", "执行代码", "运行代码"],
    "http_get": ["http", "get请求", "api", "接口", "url", "网页", "网址"],
    "http_post": ["post", "post请求", "提交", "发送请求"],
    "json_parse": ["json", "解析json", "提取"],
    "regex_match": ["正则", "匹配", "regex", "提取文本"],
    "text_stats": ["统计", "字数", "行数", "词频"],
    "encode_decode": ["编码", "解码", "base64", "url编码"],
    "system_info": ["系统", "系统信息", "环境", "版本"],
    "csv_parse": ["csv", "表格", "解析csv"],
    "sort_data": ["排序", "sort"],
    "deduplicate": ["去重", "去除重复", "unique"],
}


def select_tools(user_input, all_tools):
    if len(all_tools) <= 6:
        return all_tools
    matched = set()
    lower = user_input.lower()
    for name, kws in TOOL_KEYWORDS.items():
        if any(kw in lower for kw in kws):
            matched.add(name)
    if not matched:
        # 无关键词命中：只给基础工具，不给 web_search/news 等可能干扰回答的工具
        safe_defaults = {"shell", "read_file", "write_file", "list_dir", "calculator",
                         "run_python", "current_time"}
        selected = [s for s in all_tools
                    if s["function"]["name"] in safe_defaults
                    or s["function"]["name"].startswith("mcp_")]
        return selected if len(selected) >= 3 else all_tools
    # 始终保留通用工具（文件操作和 shell 是基础能力）
    always_keep = {"shell", "read_file"}
    matched |= always_keep
    selected = [s for s in all_tools if s["function"]["name"] in matched or s["function"]["name"].startswith("mcp_")]
    # 匹配太少时返回全部，避免过度裁剪
    if len(selected) < 2:
        return all_tools
    return selected