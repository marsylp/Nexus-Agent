"""LLM 抽象层 — 多模型 + 流式输出 + 错误恢复

错误恢复策略:
- 指数退避重试 (exponential backoff)
- 错误分类: rate_limit→等待, auth→跳过, timeout→重试, balance→跳过
- 熔断器: 连续失败自动跳过该提供商（指数退避恢复）
- 多 Key 轮换: 同一 provider 多个 API Key 自动轮换
- 清晰的用户提示

v0.5 重构:
- 多 Key 轮换 (AuthManager)
- 熔断器指数退避恢复 (1min→5min→25min→1h cap)
- 计费错误长冷却 (5h→24h)
- 熔断状态持久化
"""
from __future__ import annotations
import os, time, json
from dataclasses import dataclass, field
from typing import Any, Generator
from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat import ChatCompletionMessage

load_dotenv()
load_dotenv(".env.local", override=True)


# ── 状态输出（可被 ui 模块替换）────────────────────────────

import sys as _sys


def _status_print(msg: str):
    """LLM 层的状态输出，统一走 stderr 避免和 Agent 流式输出混淆"""
    try:
        from agent_core.ui import gray, yellow, cyan, red, green, dim
        # 替换旧的 emoji 为统一格式
        msg = msg.replace("⏳", dim("↻")).replace("🔄", cyan("→"))
        msg = msg.replace("⚠️", yellow("!")).replace("❌", red("✗"))
        msg = msg.replace("🔑", cyan("⚿"))
        _sys.stderr.write("    {}\n".format(msg.strip()))
    except ImportError:
        _sys.stderr.write("    {}\n".format(msg.strip()))
    _sys.stderr.flush()


# ── 多 Key 轮换 (AuthManager) ──────────────────────────────

@dataclass
class AuthProfile:
    """单个 API Key 配置"""
    key: str
    cooldown_until: float = 0.0  # 冷却截止时间戳
    fail_count: int = 0

    @property
    def available(self) -> bool:
        return time.time() >= self.cooldown_until

    def mark_failed(self, cooldown: float = 60.0):
        self.fail_count += 1
        self.cooldown_until = time.time() + cooldown

    def mark_success(self):
        self.fail_count = 0
        self.cooldown_until = 0.0


class AuthManager:
    """多 Key 轮换管理器 — 同一 provider 支持多个 API Key"""

    _SAVE_PATH = os.path.expanduser("~/.nexus-agent/auth_profiles.json")

    def __init__(self):
        self._profiles: dict[str, list[AuthProfile]] = {}  # provider → [AuthProfile]
        self._index: dict[str, int] = {}  # provider → 当前 round-robin 索引
        self._load()

    def _load(self):
        """从磁盘加载额外的 key 配置"""
        try:
            if os.path.exists(self._SAVE_PATH):
                with open(self._SAVE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for provider, keys in data.items():
                    if isinstance(keys, list):
                        self._profiles[provider] = [AuthProfile(key=k) for k in keys if k]
        except Exception:
            pass

    def _save(self):
        """持久化 key 列表（不保存冷却状态）"""
        try:
            os.makedirs(os.path.dirname(self._SAVE_PATH), exist_ok=True)
            data = {p: [ap.key for ap in profiles] for p, profiles in self._profiles.items()}
            with open(self._SAVE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def add_key(self, provider: str, key: str):
        """添加一个 API Key"""
        if provider not in self._profiles:
            self._profiles[provider] = []
        # 去重
        if not any(ap.key == key for ap in self._profiles[provider]):
            self._profiles[provider].append(AuthProfile(key=key))
            self._save()

    def get_key(self, provider: str) -> str | None:
        """Round-robin 获取下一个可用 key，所有 key 冷却中则返回 None"""
        profiles = self._profiles.get(provider, [])
        if not profiles:
            return None
        start = self._index.get(provider, 0) % len(profiles)
        for offset in range(len(profiles)):
            idx = (start + offset) % len(profiles)
            if profiles[idx].available:
                self._index[provider] = (idx + 1) % len(profiles)
                return profiles[idx].key
        return None  # 全部冷却中

    def mark_key_failed(self, provider: str, key: str, cooldown: float = 60.0):
        """标记某个 key 失败"""
        for ap in self._profiles.get(provider, []):
            if ap.key == key:
                ap.mark_failed(cooldown)
                break

    def mark_key_success(self, provider: str, key: str):
        """标记某个 key 成功"""
        for ap in self._profiles.get(provider, []):
            if ap.key == key:
                ap.mark_success()
                break

    def has_extra_keys(self, provider: str) -> bool:
        """是否有额外配置的 key"""
        return bool(self._profiles.get(provider))

    def get_all_keys(self, provider: str) -> list[str]:
        """获取所有可用 key（含 env 中的主 key）"""
        keys = []
        # 主 key（来自环境变量）
        cfg = PROVIDERS.get(provider, {})
        key_env = cfg.get("key_env")
        if key_env:
            main_key = os.environ.get(key_env, "")
            if main_key and main_key not in ("sk-xxx", "xxx", ""):
                keys.append(main_key)
        # 额外 key
        for ap in self._profiles.get(provider, []):
            if ap.key not in keys:
                keys.append(ap.key)
        return keys


_auth_manager = AuthManager()


def get_auth_manager() -> AuthManager:
    return _auth_manager

# ── 提供商配置 ──────────────────────────────────────────────
PROVIDERS: dict[str, dict] = {
    # ── 国际主流 ──
    "openai": {"base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY", "model": "gpt-4o-mini"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1", "key_env": "ANTHROPIC_API_KEY", "model": "claude-sonnet-4-20250514"},
    "google": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "key_env": "GOOGLE_API_KEY", "model": "gemini-2.0-flash"},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "key_env": "MISTRAL_API_KEY", "model": "mistral-small-latest"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY", "model": "llama-3.3-70b-versatile"},
    "xai": {"base_url": "https://api.x.ai/v1", "key_env": "XAI_API_KEY", "model": "grok-3-mini"},
    "cohere": {"base_url": "https://api.cohere.com/v2", "key_env": "COHERE_API_KEY", "model": "command-r-plus"},
    # ── 国内主流 ──
    "deepseek": {"base_url": "https://api.deepseek.com", "key_env": "DEEPSEEK_API_KEY", "model": "deepseek-chat"},
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "key_env": "ZHIPU_API_KEY", "model": "glm-4-flash"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "key_env": "QWEN_API_KEY", "model": "qwen-plus"},
    "doubao": {"base_url": "https://ark.cn-beijing.volces.com/api/v3", "key_env": "DOUBAO_API_KEY", "model": "doubao-1.5-pro-32k"},
    "moonshot": {"base_url": "https://api.moonshot.cn/v1", "key_env": "MOONSHOT_API_KEY", "model": "moonshot-v1-8k"},
    "baichuan": {"base_url": "https://api.baichuan-ai.com/v1", "key_env": "BAICHUAN_API_KEY", "model": "Baichuan4"},
    "minimax": {"base_url": "https://api.minimax.chat/v1", "key_env": "MINIMAX_API_KEY", "model": "MiniMax-Text-01"},
    "stepfun": {"base_url": "https://api.stepfun.com/v1", "key_env": "STEPFUN_API_KEY", "model": "step-1-8k"},
    "yi": {"base_url": "https://api.lingyiwanwu.com/v1", "key_env": "YI_API_KEY", "model": "yi-lightning"},
    # ── 聚合平台 ──
    "silicon": {"base_url": "https://api.siliconflow.cn/v1", "key_env": "SILICON_API_KEY", "model": "Qwen/Qwen2.5-7B-Instruct"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY", "model": "openai/gpt-4o-mini"},
    "together": {"base_url": "https://api.together.xyz/v1", "key_env": "TOGETHER_API_KEY", "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo"},
    # ── 本地模型 ──
    "ollama": {"base_url": "http://localhost:11434/v1", "key_env": None,
               "model": os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")},
}

# ── 错误恢复配置 ────────────────────────────────────────────
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
RETRY_BASE_DELAY = float(os.environ.get("RETRY_BASE_DELAY", "1.0"))
RETRY_STRATEGY = os.environ.get("RETRY_STRATEGY", "exponential")  # exponential | linear
FALLBACK_ORDER = ["zhipu", "deepseek", "qwen", "silicon", "openai", "groq", "moonshot", "ollama"]


def _get_fallback_order():
    """动态生成 fallback 顺序 — 新注册的 provider 自动加入"""
    order = list(FALLBACK_ORDER)
    for name in PROVIDERS:
        if name not in order:
            order.append(name)
    return order


# ── 错误分类 ────────────────────────────────────────────────

class ErrorKind:
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    BALANCE = "balance"
    TIMEOUT = "timeout"
    SERVER = "server"
    NETWORK = "network"
    NO_TOOLS = "no_tools"    # 模型不支持 function calling
    UNKNOWN = "unknown"


def classify_error(e: Exception) -> str:
    """根据异常类型和消息分类错误"""
    msg = str(e).lower()
    etype = type(e).__name__.lower()

    if "401" in msg or "unauthorized" in msg or "invalid api key" in msg or "authentication" in msg:
        return ErrorKind.AUTH
    if "402" in msg or "insufficient" in msg or "balance" in msg or "quota" in msg:
        return ErrorKind.BALANCE
    if "429" in msg or "rate" in msg or "too many" in msg or "limit" in msg:
        return ErrorKind.RATE_LIMIT
    if "timeout" in etype or "timeout" in msg or "timed out" in msg:
        return ErrorKind.TIMEOUT
    if any(code in msg for code in ("500", "502", "503", "504")):
        return ErrorKind.SERVER
    if "connection" in msg or "network" in msg or "dns" in msg:
        return ErrorKind.NETWORK
    if "does not support tools" in msg or "does not support functions" in msg:
        return ErrorKind.NO_TOOLS
    return ErrorKind.UNKNOWN


_RETRYABLE = {ErrorKind.RATE_LIMIT, ErrorKind.TIMEOUT, ErrorKind.SERVER, ErrorKind.NETWORK, ErrorKind.UNKNOWN}
_SKIP_TO_FALLBACK = {ErrorKind.AUTH, ErrorKind.BALANCE}

_ERROR_MESSAGES = {
    ErrorKind.RATE_LIMIT: "请求过于频繁，等待后重试",
    ErrorKind.AUTH: "API Key 无效或已过期",
    ErrorKind.BALANCE: "账户余额不足",
    ErrorKind.TIMEOUT: "请求超时，重试中",
    ErrorKind.SERVER: "服务端错误，重试中",
    ErrorKind.NETWORK: "网络连接异常，重试中",
    ErrorKind.NO_TOOLS: "模型不支持工具调用，以纯对话模式重试",
    ErrorKind.UNKNOWN: "未知错误，重试中",
}


# ── 熔断器（指数退避恢复）──────────────────────────────────

class CircuitBreaker:
    """每个提供商的熔断器 — 指数退避恢复 + 计费错误长冷却 + 状态持久化

    恢复策略:
    - 普通错误: 1min → 5min → 25min → 1h (cap)
    - 计费错误 (auth/balance): 5h → 24h (cap)
    """

    _SAVE_PATH = os.path.expanduser("~/.nexus-agent/circuit_breaker.json")
    _RECOVERY_BASE = 60.0       # 1 分钟
    _RECOVERY_MULTIPLIER = 5.0  # 每次 ×5
    _RECOVERY_CAP = 3600.0      # 1 小时上限
    _BILLING_BASE = 18000.0     # 5 小时
    _BILLING_CAP = 86400.0      # 24 小时上限

    def __init__(self, threshold: int = 3, recovery_time: float = 60.0, persist: bool = True):
        self.threshold = threshold
        self.recovery_time = recovery_time  # 兼容旧接口，也作为 base
        self._recovery_base = recovery_time  # 使用构造参数作为 base
        self._persist = persist
        self._state: dict[str, dict] = {}
        if persist:
            self._load()

    def _default_state(self) -> dict:
        return {
            "failures": 0, "last_failure": 0.0, "open": False,
            "reason": "", "trip_count": 0, "error_kind": "",
        }

    def _calc_recovery(self, state: dict) -> float:
        """根据 trip_count 和错误类型计算恢复时间"""
        kind = state.get("error_kind", "")
        trip = state.get("trip_count", 1)
        if kind in (ErrorKind.AUTH, ErrorKind.BALANCE):
            return min(self._BILLING_BASE * (2 ** (trip - 1)), self._BILLING_CAP)
        return min(self._recovery_base * (self._RECOVERY_MULTIPLIER ** (trip - 1)), self._RECOVERY_CAP)

    def is_open(self, provider: str) -> bool:
        state = self._state.get(provider)
        if not state or not state["open"]:
            return False
        recovery = self._calc_recovery(state)
        if time.time() - state["last_failure"] > recovery:
            # 半开状态 — 允许一次探测
            state["open"] = False
            state["failures"] = 0
            state["reason"] = ""
            # 不重置 trip_count，下次失败会用更长冷却
            self._save()
            return False
        return True

    def record_failure(self, provider: str, immediate: bool = False, error_kind: str = ""):
        if provider not in self._state:
            self._state[provider] = self._default_state()
        state = self._state[provider]
        state["failures"] += 1
        state["last_failure"] = time.time()
        if error_kind:
            state["error_kind"] = error_kind
        if immediate or state["failures"] >= self.threshold:
            if not state["open"]:
                state["trip_count"] = state.get("trip_count", 0) + 1
            state["open"] = True
            recovery = self._calc_recovery(state)
            state["reason"] = "恢复时间 {:.0f}s".format(recovery)
        self._save()

    def record_success(self, provider: str):
        if provider in self._state:
            self._state[provider] = self._default_state()
            self._save()

    def get_status(self) -> dict[str, str]:
        result = {}
        for prov, state in self._state.items():
            if state["open"]:
                recovery = self._calc_recovery(state)
                remaining = recovery - (time.time() - state["last_failure"])
                trip = state.get("trip_count", 1)
                result[prov] = "熔断中 (第{}次, 恢复倒计时 {:.0f}s)".format(trip, max(0, remaining))
            elif state["failures"] > 0:
                result[prov] = "警告 (连续失败 {}次)".format(state["failures"])
            else:
                result[prov] = "正常"
        return result

    def get_recovery_time(self, provider: str) -> float:
        """获取当前恢复时间（供外部查询）"""
        state = self._state.get(provider)
        if not state:
            return self._RECOVERY_BASE
        return self._calc_recovery(state)

    def _load(self):
        """从磁盘加载熔断状态"""
        try:
            if os.path.exists(self._SAVE_PATH):
                with open(self._SAVE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for prov, state in data.items():
                    self._state[prov] = {**self._default_state(), **state}
        except Exception:
            pass

    def _save(self):
        """持久化熔断状态"""
        if not self._persist:
            return
        try:
            os.makedirs(os.path.dirname(self._SAVE_PATH), exist_ok=True)
            with open(self._SAVE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


_breaker = CircuitBreaker()


def get_circuit_breaker() -> CircuitBreaker:
    return _breaker


# ── 客户端管理（无全局状态）────────────────────────────────

def register_provider(name: str, base_url: str, key_env: str | None, model: str,
                      caps: set | None = None, tier: int = 1, cost: int = 1):
    """注册新的 provider（运行时动态添加）"""
    PROVIDERS[name] = {"base_url": base_url, "key_env": key_env, "model": model}
    from agent_core.model_router import MODEL_REGISTRY, MODEL_TIERS
    if name not in MODEL_REGISTRY:
        MODEL_REGISTRY[name] = {}
    MODEL_REGISTRY[name][model] = {
        "tier": tier,
        "caps": caps or {"fast"},
        "cost": cost,
    }
    MODEL_TIERS[name] = {tier: model}


def _create_client(provider: str, api_key: str | None = None) -> OpenAI:
    """创建指定 provider 的 OpenAI 客户端

    支持多 Key 轮换: 优先使用 AuthManager 的 round-robin key，
    也可通过 api_key 参数显式指定。
    """
    if provider == "custom":
        base_url = os.environ["CUSTOM_BASE_URL"]
        key = api_key or os.environ.get("CUSTOM_API_KEY", "no-key")
    elif provider in PROVIDERS:
        cfg = PROVIDERS[provider]
        base_url = cfg["base_url"]
        if api_key:
            key = api_key
        else:
            # 尝试 AuthManager 轮换
            rotated = _auth_manager.get_key(provider)
            if rotated:
                key = rotated
            else:
                key = os.environ.get(cfg["key_env"] or "", "no-key") if cfg["key_env"] else "no-key"
    else:
        raise ValueError("未知提供商: {}".format(provider))
    return OpenAI(api_key=key, base_url=base_url)


# ── 兼容旧接口（保留全局 client 用于 main.py 等顶层调用）──

_client: OpenAI | None = None
_provider_name: str | None = None


def _init_client(provider: str | None = None) -> tuple[OpenAI, str]:
    global _client, _provider_name
    if _client and not provider:
        return _client, _provider_name or ""

    provider = provider or os.environ.get("LLM_PROVIDER", "openai").lower()
    client = _create_client(provider)
    if not provider:
        _client, _provider_name = client, provider
    return client, provider


def reset_client():
    global _client, _provider_name
    _client = None
    _provider_name = None
    clear_client_cache()


def switch_provider(name: str) -> str:
    name = name.lower().strip()
    if name not in PROVIDERS:
        raise ValueError("未知提供商: {}，可选: {}".format(name, ", ".join(PROVIDERS.keys())))
    os.environ["LLM_PROVIDER"] = name
    reset_client()
    clear_client_cache()
    return provider_info()


def list_providers() -> list[dict]:
    current = os.environ.get("LLM_PROVIDER", "openai").lower()
    result = []
    for name, cfg in PROVIDERS.items():
        has_key = True
        if cfg.get("key_env"):
            key = os.environ.get(cfg["key_env"], "")
            has_key = bool(key) and key not in ("sk-xxx", "xxx", "")
        status = "正常"
        if _breaker.is_open(name):
            status = "熔断"
        result.append({"name": name, "model": cfg["model"], "active": name == current,
                        "configured": has_key, "status": status})
    return result


def get_model(provider: str | None = None) -> str:
    """获取指定 provider 的默认模型名"""
    provider = provider or os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "custom":
        return os.environ.get("CUSTOM_MODEL", "custom-model")
    if provider == "ollama":
        return os.environ.get("OLLAMA_MODEL", PROVIDERS["ollama"]["model"])
    return os.environ.get("AGENT_MODEL", PROVIDERS.get(provider, {}).get("model", "gpt-4o-mini"))


def provider_info() -> str:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    return "{} / {}".format(provider, get_model(provider))


# ── 退避策略 ────────────────────────────────────────────────

def _calc_delay(attempt: int, error_kind: str) -> float:
    base = RETRY_BASE_DELAY
    if error_kind == ErrorKind.RATE_LIMIT:
        base *= 3
    if RETRY_STRATEGY == "exponential":
        return base * (2 ** attempt)
    else:
        return base * (attempt + 1)


# ── 非流式调用（接受显式 provider）──────────────────────────

def chat_completion(messages: list[dict], tools: list[dict] | None = None,
                    model: str | None = None, provider: str | None = None) -> ChatCompletionMessage:
    """非流式 LLM 调用。返回 OpenAI ChatCompletionMessage 对象。"""
    provider = provider or os.environ.get("LLM_PROVIDER", "openai").lower()
    client = _get_cached_client(provider)
    model = model or get_model(provider)
    # 追踪当前 key
    _last_used_key[provider] = client.api_key or ""
    kwargs: dict = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message


# ── 流式调用（接受显式 provider）────────────────────────────

def chat_completion_stream(messages: list[dict], tools: list[dict] | None = None,
                           model: str | None = None, provider: str | None = None) -> Generator[tuple[str, Any], None, None]:
    """流式调用 LLM，yield (type, data) 元组"""
    provider = provider or os.environ.get("LLM_PROVIDER", "openai").lower()
    client = _get_cached_client(provider)
    model = model or get_model(provider)
    _last_used_key[provider] = client.api_key or ""
    kwargs: dict = {"model": model, "messages": messages, "stream": True}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    resp = client.chat.completions.create(**kwargs)
    tool_calls_buf: dict[int, dict] = {}
    full_content = ""
    thinking_content = ""

    for chunk in resp:
        choice = chunk.choices[0] if chunk.choices else None
        if not choice:
            continue
        delta = choice.delta

        if delta and getattr(delta, "reasoning_content", None):
            thinking_content += delta.reasoning_content
            yield ("thinking", delta.reasoning_content)

        if delta and getattr(delta, "content", None):
            full_content += delta.content
            yield ("content", delta.content)

        if delta and getattr(delta, "tool_calls", None):
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tool_calls_buf:
                    tool_calls_buf[idx] = {"id": "", "name": "", "arguments": ""}
                if tc_delta.id:
                    tool_calls_buf[idx]["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        tool_calls_buf[idx]["name"] = tc_delta.function.name
                        yield ("tool_call_start", tool_calls_buf[idx]["name"])
                    if tc_delta.function.arguments:
                        tool_calls_buf[idx]["arguments"] += tc_delta.function.arguments

        if choice.finish_reason:
            break

    if tool_calls_buf:
        for idx in sorted(tool_calls_buf.keys()):
            yield ("tool_call_end", tool_calls_buf[idx])

    finish_reason = choice.finish_reason if choice else "stop"
    yield ("done", {"content": full_content, "thinking": thinking_content,
                    "tool_calls": list(tool_calls_buf.values()) if tool_calls_buf else None,
                    "finish_reason": finish_reason})


# ── Key 轮换辅助 ────────────────────────────────────────────

# 追踪每个 provider 最近使用的 key（用于两阶段降级）
_last_used_key: dict[str, str] = {}


def _get_current_key(provider: str) -> str | None:
    """获取当前 provider 正在使用的 key"""
    if provider in _last_used_key:
        return _last_used_key[provider]
    cfg = PROVIDERS.get(provider, {})
    key_env = cfg.get("key_env")
    if key_env:
        return os.environ.get(key_env, "")
    return None


def _get_next_available_key(provider: str, exclude: set[str]) -> str | None:
    """获取同 provider 下一个可用 key（排除已尝试的）"""
    all_keys = _auth_manager.get_all_keys(provider)
    for k in all_keys:
        if k not in exclude:
            return k
    return None


def _mark_current_key_success(provider: str):
    """标记当前 key 成功"""
    key = _get_current_key(provider)
    if key:
        _auth_manager.mark_key_success(provider, key)


# ── Session 粘性 — provider client 缓存 ────────────────────

_client_cache: dict[str, tuple[OpenAI, float]] = {}  # provider → (client, last_used_ts)
_CLIENT_CACHE_TTL = 300.0  # 5 分钟缓存


def _get_cached_client(provider: str, api_key: str | None = None) -> OpenAI:
    """获取缓存的 client（Session 粘性），减少不必要的连接重建"""
    cache_key = "{}:{}".format(provider, api_key or "default")
    now = time.time()
    if cache_key in _client_cache:
        client, last_used = _client_cache[cache_key]
        if now - last_used < _CLIENT_CACHE_TTL:
            _client_cache[cache_key] = (client, now)
            return client
    client = _create_client(provider, api_key)
    _client_cache[cache_key] = (client, now)
    # 清理过期缓存
    expired = [k for k, (_, ts) in _client_cache.items() if now - ts > _CLIENT_CACHE_TTL]
    for k in expired:
        del _client_cache[k]
    return client


def clear_client_cache():
    """清除 client 缓存（provider 切换时调用）"""
    _client_cache.clear()


# ── 带错误恢复的调用 ────────────────────────────────────────

def chat_completion_resilient(messages: list[dict], tools: list[dict] | None = None,
                              model: str | None = None, stream: bool = True,
                              provider: str | None = None):
    """带自动重试和 fallback 的 LLM 调用。接受显式 provider 参数。"""
    provider = provider or os.environ.get("LLM_PROVIDER", "zhipu").lower()
    if not stream:
        return _resilient_sync(messages, tools, model, provider)
    return _resilient_stream_wrapper(messages, tools, model, provider)


def _resilient_sync(messages, tools, model, provider):
    """非流式的错误恢复调用 — 两阶段降级:
    阶段1: 同 provider 内 key 轮换（auth/balance 错误时尝试其他 key）
    阶段2: 跨 provider fallback
    """
    # ── 阶段 1: 同 provider key 轮换 ──
    if not _breaker.is_open(provider):
        keys_tried: set[str] = set()
        for attempt in range(MAX_RETRIES):
            try:
                result = chat_completion(messages, tools, model, provider=provider)
                _breaker.record_success(provider)
                # 标记当前 key 成功
                _mark_current_key_success(provider)
                return result
            except Exception as e:
                kind = classify_error(e)
                friendly = _ERROR_MESSAGES.get(kind, str(e))

                # 模型不支持 tools → 去掉 tools 参数立即重试（不算失败）
                if kind == ErrorKind.NO_TOOLS and tools:
                    _status_print("⚠️  {}: {}".format(provider, friendly))
                    try:
                        result = chat_completion(messages, None, model, provider=provider)
                        _breaker.record_success(provider)
                        _mark_current_key_success(provider)
                        return result
                    except Exception as e2:
                        kind = classify_error(e2)
                        friendly = _ERROR_MESSAGES.get(kind, str(e2))

                if kind in _SKIP_TO_FALLBACK:
                    # 阶段 1.5: auth/balance 错误 → 先尝试同 provider 其他 key
                    current_key = _get_current_key(provider)
                    if current_key:
                        keys_tried.add(current_key)
                    _auth_manager.mark_key_failed(provider, current_key or "", cooldown=300)

                    next_key = _get_next_available_key(provider, keys_tried)
                    if next_key:
                        _status_print("🔑 {}: {}，尝试备用 Key...".format(provider, friendly))
                        try:
                            client = _create_client(provider, api_key=next_key)
                            kwargs: dict = {"model": model, "messages": messages}
                            if tools:
                                kwargs["tools"] = tools
                                kwargs["tool_choice"] = "auto"
                            resp = client.chat.completions.create(**kwargs)
                            _breaker.record_success(provider)
                            _auth_manager.mark_key_success(provider, next_key)
                            return resp.choices[0].message
                        except Exception:
                            keys_tried.add(next_key)
                            _auth_manager.mark_key_failed(provider, next_key, cooldown=300)

                    # 所有 key 都失败，进入阶段 2
                    _status_print("⚠️  {}: {}，切换备用模型...".format(provider, friendly))
                    _breaker.record_failure(provider, immediate=True, error_kind=kind)
                    break

                if attempt < MAX_RETRIES - 1:
                    delay = _calc_delay(attempt, kind)
                    _status_print("⏳ {}: {} (第{}次重试，等待{:.1f}s)".format(
                        provider, friendly, attempt + 1, delay))
                    time.sleep(delay)
                else:
                    _status_print("❌ {}: 重试{}次仍失败".format(provider, MAX_RETRIES))
                    _breaker.record_failure(provider, error_kind=kind)

    # fallback
    for fallback in _get_fallback_order():
        if fallback == provider:
            continue
        if not _provider_has_key(fallback):
            continue
        if _breaker.is_open(fallback):
            continue
        try:
            _status_print("🔄 自动切换到 {} ...".format(fallback))
            fb_model = PROVIDERS[fallback]["model"]
            result = chat_completion(messages, tools, fb_model, provider=fallback)
            _breaker.record_success(fallback)
            return result
        except Exception as e:
            kind = classify_error(e)
            _status_print("⚠️  {}: {}".format(fallback, _ERROR_MESSAGES.get(kind, str(e))))
            _breaker.record_failure(fallback, immediate=(kind in _SKIP_TO_FALLBACK), error_kind=kind)
            continue

    raise RuntimeError("所有模型均调用失败，请检查 API Key 和账户余额")


def _resilient_stream_wrapper(messages, tools, model, provider):
    """流式调用的错误恢复包装器 — 两阶段降级:
    阶段1: 同 provider 内 key 轮换
    阶段2: 跨 provider fallback
    """
    attempts = []
    if not _breaker.is_open(provider):
        attempts.append((provider, model))
    for fallback in _get_fallback_order():
        if fallback == provider:
            continue
        if not _provider_has_key(fallback):
            continue
        if _breaker.is_open(fallback):
            continue
        attempts.append((fallback, PROVIDERS[fallback]["model"]))

    for i, (prov, m) in enumerate(attempts):
        try:
            if prov != provider or i > 0:
                _status_print("🔄 自动切换到 {} ...".format(prov))

            gen = chat_completion_stream(messages, tools, m, provider=prov)
            first_event = next(gen)
            _breaker.record_success(prov)
            _mark_current_key_success(prov)

            yield first_event

            try:
                for event in gen:
                    yield event
            except Exception as mid_err:
                kind = classify_error(mid_err)
                friendly = _ERROR_MESSAGES.get(kind, str(mid_err))
                _status_print("⚠️  {} 流式中断: {}".format(prov, friendly))
                _breaker.record_failure(prov)
                yield ("error", "流式传输中断: {}".format(friendly))
            return

        except StopIteration:
            _breaker.record_success(prov)
            yield ("done", {"content": "", "thinking": "", "tool_calls": None, "finish_reason": "stop"})
            return

        except Exception as e:
            kind = classify_error(e)
            friendly = _ERROR_MESSAGES.get(kind, str(e))

            # 模型不支持 tools → 去掉 tools 参数立即重试（不算失败）
            if kind == ErrorKind.NO_TOOLS and tools:
                _status_print("⚠️  {}: {}".format(prov, friendly))
                try:
                    gen2 = chat_completion_stream(messages, None, m, provider=prov)
                    first2 = next(gen2)
                    _breaker.record_success(prov)
                    _mark_current_key_success(prov)
                    yield first2
                    for event in gen2:
                        yield event
                    return
                except Exception as e2:
                    kind = classify_error(e2)
                    friendly = _ERROR_MESSAGES.get(kind, str(e2))

            # 阶段 1.5: auth/balance → 尝试同 provider 其他 key
            if kind in _SKIP_TO_FALLBACK and prov == provider:
                current_key = _get_current_key(prov)
                keys_tried = {current_key} if current_key else set()
                _auth_manager.mark_key_failed(prov, current_key or "", cooldown=300)
                next_key = _get_next_available_key(prov, keys_tried)
                if next_key:
                    _status_print("🔑 {}: {}，尝试备用 Key...".format(prov, friendly))
                    try:
                        gen2 = chat_completion_stream(messages, tools, m, provider=prov)
                        first2 = next(gen2)
                        _breaker.record_success(prov)
                        _auth_manager.mark_key_success(prov, next_key)
                        yield first2
                        for event in gen2:
                            yield event
                        return
                    except Exception:
                        _auth_manager.mark_key_failed(prov, next_key, cooldown=300)

            _status_print("⚠️  {}: {}".format(prov, friendly))
            _breaker.record_failure(prov, immediate=(kind in _SKIP_TO_FALLBACK), error_kind=kind)
            continue

    yield ("error", "所有模型均调用失败，请检查 API Key 和账户余额")


def _provider_has_key(name: str) -> bool:
    cfg = PROVIDERS.get(name, {})
    key_env = cfg.get("key_env")
    if not key_env:
        return True
    key = os.environ.get(key_env, "")
    return bool(key) and key not in ("sk-xxx", "xxx", "")
