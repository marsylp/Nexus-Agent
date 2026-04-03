"""LLM 抽象层测试"""
import os, pytest
from agent_core.llm import (
    PROVIDERS, list_providers, get_model, provider_info,
    _provider_has_key, classify_error, ErrorKind,
    CircuitBreaker, _calc_delay, MAX_RETRIES, RETRY_BASE_DELAY,
    FALLBACK_ORDER, _RETRYABLE, _SKIP_TO_FALLBACK,
)


class TestProviderConfig:
    def test_providers_exist(self):
        assert "zhipu" in PROVIDERS
        assert "deepseek" in PROVIDERS
        assert "openai" in PROVIDERS

    def test_list_providers(self):
        providers = list_providers()
        assert len(providers) >= 5

    def test_get_model(self):
        m = get_model("zhipu")
        assert m == PROVIDERS["zhipu"]["model"]

    def test_provider_has_key(self):
        # ollama 不需要 key
        assert _provider_has_key("ollama") is True


class TestErrorClassification:
    def test_rate_limit(self):
        assert classify_error(Exception("429 Too Many Requests")) == ErrorKind.RATE_LIMIT

    def test_auth(self):
        assert classify_error(Exception("401 Unauthorized")) == ErrorKind.AUTH

    def test_balance(self):
        assert classify_error(Exception("insufficient balance")) == ErrorKind.BALANCE

    def test_timeout(self):
        assert classify_error(TimeoutError("timed out")) == ErrorKind.TIMEOUT

    def test_server(self):
        assert classify_error(Exception("502 Bad Gateway")) == ErrorKind.SERVER

    def test_retryable_and_skip_disjoint(self):
        assert _RETRYABLE & _SKIP_TO_FALLBACK == set()


class TestCircuitBreaker:
    def test_basic_trigger(self):
        cb = CircuitBreaker(threshold=2, recovery_time=1.0, persist=False)
        cb.record_failure("test")
        assert not cb.is_open("test")
        cb.record_failure("test")
        assert cb.is_open("test")

    def test_immediate_trigger(self):
        cb = CircuitBreaker(threshold=5, persist=False)
        cb.record_failure("test", immediate=True)
        assert cb.is_open("test")

    def test_success_resets(self):
        cb = CircuitBreaker(threshold=1, persist=False)
        cb.record_failure("test")
        assert cb.is_open("test")
        cb.record_success("test")
        assert not cb.is_open("test")

    def test_recovery(self):
        import time
        cb = CircuitBreaker(threshold=1, recovery_time=0.1, persist=False)
        cb.record_failure("test")
        assert cb.is_open("test")
        time.sleep(0.15)
        assert not cb.is_open("test")


class TestBackoff:
    def test_exponential(self):
        d0 = _calc_delay(0, ErrorKind.TIMEOUT)
        d1 = _calc_delay(1, ErrorKind.TIMEOUT)
        d2 = _calc_delay(2, ErrorKind.TIMEOUT)
        assert d0 < d1 < d2

    def test_rate_limit_longer(self):
        d_rate = _calc_delay(0, ErrorKind.RATE_LIMIT)
        d_normal = _calc_delay(0, ErrorKind.TIMEOUT)
        assert d_rate > d_normal


class TestRetryConfig:
    def test_config_values(self):
        assert MAX_RETRIES >= 2
        assert RETRY_BASE_DELAY > 0
        assert len(FALLBACK_ORDER) >= 3
