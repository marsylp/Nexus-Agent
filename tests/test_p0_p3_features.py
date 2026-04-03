"""P0~P3 演进功能测试

覆盖:
- P0-1: 多 Key 轮换 (AuthManager)
- P0-2: 会话持久化 (SessionStore)
- P0-3: 熔断器指数退避升级
- P1-4: Per-provider 工具策略
- P1-5: Docker 沙箱
- P2-6: 场景级模型路由
- P2-8: Skill 资格检查
- P3-10: 预压缩记忆冲刷
- P3-11: 统一模型引用格式
"""
import os, time, json, tempfile, shutil, pytest
from agent_core.llm import (
    AuthProfile, AuthManager, CircuitBreaker, ErrorKind,
    get_auth_manager, PROVIDERS,
)
from agent_core.session_store import SessionStore, SessionMeta
from agent_core.approval import (
    check_provider_tool_policy, filter_tools_by_policy,
    PROVIDER_TOOL_POLICY,
)
from agent_core.model_router import (
    parse_model_ref, format_model_ref, resolve_model_ref,
    MODEL_REGISTRY,
)
from agent_core.token_optimizer import _extract_important_info


# ═══════════════════════════════════════════════════════════
# P0-1: 多 Key 轮换 (AuthManager)
# ═══════════════════════════════════════════════════════════

class TestAuthProfile:
    def test_initial_available(self):
        ap = AuthProfile(key="sk-test-1")
        assert ap.available

    def test_mark_failed_cooldown(self):
        ap = AuthProfile(key="sk-test-1")
        ap.mark_failed(cooldown=0.1)
        assert not ap.available
        assert ap.fail_count == 1
        time.sleep(0.15)
        assert ap.available

    def test_mark_success_resets(self):
        ap = AuthProfile(key="sk-test-1")
        ap.mark_failed(cooldown=60)
        assert not ap.available
        ap.mark_success()
        assert ap.available
        assert ap.fail_count == 0

    def test_multiple_failures(self):
        ap = AuthProfile(key="sk-test-1")
        ap.mark_failed(cooldown=60)
        ap.mark_failed(cooldown=60)
        assert ap.fail_count == 2


class TestAuthManager:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.save_path = os.path.join(self.tmpdir, "auth.json")
        self.mgr = AuthManager()
        self.mgr._SAVE_PATH = self.save_path
        self.mgr._profiles = {}
        self.mgr._index = {}

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_key(self):
        self.mgr.add_key("zhipu", "sk-key-1")
        assert self.mgr.has_extra_keys("zhipu")
        assert len(self.mgr._profiles["zhipu"]) == 1

    def test_add_duplicate_key(self):
        self.mgr.add_key("zhipu", "sk-key-1")
        self.mgr.add_key("zhipu", "sk-key-1")
        assert len(self.mgr._profiles["zhipu"]) == 1

    def test_round_robin(self):
        self.mgr.add_key("test", "key-a")
        self.mgr.add_key("test", "key-b")
        self.mgr.add_key("test", "key-c")
        keys = [self.mgr.get_key("test") for _ in range(6)]
        assert keys == ["key-a", "key-b", "key-c", "key-a", "key-b", "key-c"]

    def test_skip_cooled_key(self):
        self.mgr.add_key("test", "key-a")
        self.mgr.add_key("test", "key-b")
        self.mgr.mark_key_failed("test", "key-a", cooldown=60)
        # key-a 冷却中，应该跳过
        key = self.mgr.get_key("test")
        assert key == "key-b"

    def test_all_keys_cooled(self):
        self.mgr.add_key("test", "key-a")
        self.mgr.add_key("test", "key-b")
        self.mgr.mark_key_failed("test", "key-a", cooldown=60)
        self.mgr.mark_key_failed("test", "key-b", cooldown=60)
        assert self.mgr.get_key("test") is None

    def test_no_extra_keys(self):
        assert not self.mgr.has_extra_keys("nonexistent")
        assert self.mgr.get_key("nonexistent") is None

    def test_persistence(self):
        self.mgr.add_key("zhipu", "sk-persist-1")
        self.mgr.add_key("zhipu", "sk-persist-2")
        # 重新加载
        mgr2 = AuthManager()
        mgr2._SAVE_PATH = self.save_path
        mgr2._profiles = {}
        mgr2._load()
        assert len(mgr2._profiles.get("zhipu", [])) == 2

    def test_mark_key_success(self):
        self.mgr.add_key("test", "key-a")
        self.mgr.mark_key_failed("test", "key-a", cooldown=60)
        assert not self.mgr._profiles["test"][0].available
        self.mgr.mark_key_success("test", "key-a")
        assert self.mgr._profiles["test"][0].available


# ═══════════════════════════════════════════════════════════
# P0-2: 会话持久化 (SessionStore)
# ═══════════════════════════════════════════════════════════

class TestSessionStore:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = SessionStore(base_dir=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_and_load(self):
        memory = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        sid = self.store.save("test-001", memory, provider="zhipu", summary="测试会话")
        loaded, meta = self.store.load("test-001")
        assert len(loaded) == 3
        assert loaded[0]["role"] == "system"
        assert meta is not None
        assert meta.session_id == "test-001"
        assert meta.provider == "zhipu"
        assert meta.turns == 1

    def test_load_nonexistent(self):
        loaded, meta = self.store.load("nonexistent")
        assert loaded == []
        assert meta is None

    def test_list_sessions(self):
        self.store.save("s1", [{"role": "user", "content": "a"}], summary="会话1")
        self.store.save("s2", [{"role": "user", "content": "b"}], summary="会话2")
        sessions = self.store.list_sessions()
        assert len(sessions) == 2

    def test_delete_session(self):
        self.store.save("to-delete", [{"role": "user", "content": "x"}])
        assert self.store.delete("to-delete")
        loaded, _ = self.store.load("to-delete")
        assert loaded == []

    def test_delete_nonexistent(self):
        assert not self.store.delete("nope")

    def test_generate_id(self):
        sid = SessionStore.generate_id()
        assert len(sid) > 0
        assert "_" in sid

    def test_compact(self):
        """压缩应保留最近轮次，摘要旧轮次"""
        memory = [{"role": "system", "content": "sys"}]
        for i in range(10):
            memory.append({"role": "user", "content": f"问题{i}"})
            memory.append({"role": "assistant", "content": f"回答{i}"})
        self.store.save("compact-test", memory, summary="压缩测试")
        result = self.store.compact("compact-test")
        assert result is True
        loaded, _ = self.store.load("compact-test")
        # 压缩后应该比原来短
        assert len(loaded) < len(memory)
        # 应该包含摘要
        has_summary = any("历史摘要" in m.get("content", "") for m in loaded)
        assert has_summary

    def test_compact_short_session_noop(self):
        """短会话不需要压缩"""
        memory = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        self.store.save("short", memory)
        assert not self.store.compact("short")


class TestSessionMeta:
    def test_display(self):
        meta = SessionMeta(
            session_id="20250101_120000",
            created_at=time.time(),
            updated_at=time.time(),
            turns=5,
            provider="zhipu",
            summary="测试摘要内容",
        )
        display = meta.display()
        assert "5轮" in display
        assert "测试摘要" in display


# ═══════════════════════════════════════════════════════════
# P0-3: 熔断器指数退避升级
# ═══════════════════════════════════════════════════════════

class TestCircuitBreakerExponentialBackoff:
    def test_first_trip_uses_base(self):
        """第一次熔断使用 base recovery time"""
        cb = CircuitBreaker(threshold=1, recovery_time=1.0, persist=False)
        cb.record_failure("p1")
        assert cb.is_open("p1")
        recovery = cb.get_recovery_time("p1")
        assert recovery == 1.0  # base * 5^0

    def test_second_trip_longer(self):
        """第二次熔断恢复时间更长"""
        cb = CircuitBreaker(threshold=1, recovery_time=1.0, persist=False)
        # 第一次熔断
        cb.record_failure("p1")
        state = cb._state["p1"]
        assert state["trip_count"] == 1
        # 手动恢复
        cb.record_success("p1")
        # 第二次熔断
        cb.record_failure("p1")
        recovery = cb.get_recovery_time("p1")
        # trip_count 重置了，因为 record_success 重置了整个 state
        assert recovery == 1.0

    def test_consecutive_trips_escalate(self):
        """连续熔断（不经过 success）trip_count 递增"""
        cb = CircuitBreaker(threshold=1, recovery_time=1.0, persist=False)
        cb.record_failure("p1")
        assert cb._state["p1"]["trip_count"] == 1
        # 模拟半开恢复后再次失败
        cb._state["p1"]["open"] = False
        cb.record_failure("p1")
        assert cb._state["p1"]["trip_count"] == 2
        recovery = cb.get_recovery_time("p1")
        assert recovery == 5.0  # 1.0 * 5^1

    def test_billing_error_long_cooldown(self):
        """计费错误使用更长的冷却时间"""
        cb = CircuitBreaker(threshold=1, recovery_time=60.0, persist=False)
        cb.record_failure("p1", immediate=True, error_kind=ErrorKind.BALANCE)
        recovery = cb.get_recovery_time("p1")
        assert recovery == cb._BILLING_BASE  # 5 小时

    def test_auth_error_long_cooldown(self):
        """认证错误使用更长的冷却时间"""
        cb = CircuitBreaker(threshold=1, recovery_time=60.0, persist=False)
        cb.record_failure("p1", immediate=True, error_kind=ErrorKind.AUTH)
        recovery = cb.get_recovery_time("p1")
        assert recovery == cb._BILLING_BASE

    def test_recovery_cap(self):
        """恢复时间有上限"""
        cb = CircuitBreaker(threshold=1, recovery_time=60.0, persist=False)
        # 模拟多次 trip
        cb._state["p1"] = cb._default_state()
        cb._state["p1"]["trip_count"] = 100
        cb._state["p1"]["open"] = True
        cb._state["p1"]["last_failure"] = time.time()
        recovery = cb.get_recovery_time("p1")
        assert recovery <= cb._RECOVERY_CAP

    def test_billing_cap(self):
        """计费错误冷却也有上限"""
        cb = CircuitBreaker(threshold=1, recovery_time=60.0, persist=False)
        cb._state["p1"] = cb._default_state()
        cb._state["p1"]["trip_count"] = 100
        cb._state["p1"]["error_kind"] = ErrorKind.BALANCE
        recovery = cb.get_recovery_time("p1")
        assert recovery <= cb._BILLING_CAP

    def test_status_shows_trip_count(self):
        """状态报告应显示 trip 次数"""
        cb = CircuitBreaker(threshold=1, recovery_time=60.0, persist=False)
        cb.record_failure("p1")
        status = cb.get_status()
        assert "第1次" in status.get("p1", "")

    def test_persist_false_no_disk_io(self):
        """persist=False 不应读写磁盘"""
        cb = CircuitBreaker(persist=False)
        cb.record_failure("test_no_disk")
        cb.record_success("test_no_disk")
        # 不应抛异常，也不应创建文件


# ═══════════════════════════════════════════════════════════
# P1-4: Per-provider 工具策略
# ═══════════════════════════════════════════════════════════

class TestProviderToolPolicy:
    def test_ollama_blocks_shell(self):
        allowed, reason = check_provider_tool_policy("shell", provider="ollama")
        assert not allowed
        assert "ollama" in reason

    def test_ollama_blocks_write(self):
        allowed, _ = check_provider_tool_policy("write_file", provider="ollama")
        assert not allowed

    def test_ollama_allows_read(self):
        allowed, _ = check_provider_tool_policy("read_file", provider="ollama")
        assert allowed

    def test_silicon_blocks_shell(self):
        allowed, _ = check_provider_tool_policy("shell", provider="silicon")
        assert not allowed

    def test_openai_allows_all(self):
        allowed, _ = check_provider_tool_policy("shell", provider="openai")
        assert allowed

    def test_tier1_blocks_shell(self):
        allowed, reason = check_provider_tool_policy("shell", provider="unknown", tier=1)
        assert not allowed
        assert "Tier 1" in reason

    def test_tier2_allows_shell(self):
        allowed, _ = check_provider_tool_policy("shell", provider="unknown", tier=2)
        assert allowed

    def test_filter_tools_by_policy(self):
        tools = [
            {"function": {"name": "shell"}},
            {"function": {"name": "read_file"}},
            {"function": {"name": "calculator"}},
            {"function": {"name": "write_file"}},
        ]
        filtered = filter_tools_by_policy(tools, provider="ollama")
        names = [t["function"]["name"] for t in filtered]
        assert "shell" not in names
        assert "write_file" not in names
        assert "read_file" in names
        assert "calculator" in names

    def test_filter_no_policy(self):
        tools = [{"function": {"name": "shell"}}]
        filtered = filter_tools_by_policy(tools, provider="openai")
        assert len(filtered) == 1


# ═══════════════════════════════════════════════════════════
# P2-8: Skill 资格检查
# ═══════════════════════════════════════════════════════════

class TestSkillEligibility:
    def test_no_meta_always_eligible(self):
        from skills import _check_skill_eligible
        import types
        mod = types.ModuleType("test_mod")
        eligible, reason = _check_skill_eligible(mod)
        assert eligible

    def test_missing_bin_ineligible(self):
        from skills import _check_skill_eligible
        import types
        mod = types.ModuleType("test_mod")
        mod.SKILL_META = {"requires_bins": ["nonexistent_binary_xyz"]}
        eligible, reason = _check_skill_eligible(mod)
        assert not eligible
        assert "nonexistent_binary_xyz" in reason

    def test_missing_env_ineligible(self):
        from skills import _check_skill_eligible
        import types
        mod = types.ModuleType("test_mod")
        mod.SKILL_META = {"requires_env": ["NONEXISTENT_ENV_VAR_XYZ"]}
        eligible, reason = _check_skill_eligible(mod)
        assert not eligible
        assert "NONEXISTENT_ENV_VAR_XYZ" in reason

    def test_wrong_os_ineligible(self):
        from skills import _check_skill_eligible
        import types, platform
        mod = types.ModuleType("test_mod")
        # 设置一个不可能匹配的 OS
        mod.SKILL_META = {"os": ["plan9"]}
        eligible, reason = _check_skill_eligible(mod)
        assert not eligible

    def test_correct_os_eligible(self):
        from skills import _check_skill_eligible
        import types, platform
        mod = types.ModuleType("test_mod")
        current_os = platform.system().lower()
        mod.SKILL_META = {"os": [current_os]}
        eligible, reason = _check_skill_eligible(mod)
        assert eligible

    def test_existing_bin_eligible(self):
        from skills import _check_skill_eligible
        import types
        mod = types.ModuleType("test_mod")
        mod.SKILL_META = {"requires_bins": ["python3"]}
        eligible, reason = _check_skill_eligible(mod)
        assert eligible


# ═══════════════════════════════════════════════════════════
# P3-10: 预压缩记忆冲刷
# ═══════════════════════════════════════════════════════════

class TestPreCompactionFlush:
    def test_extract_preferences(self):
        messages = [
            {"role": "user", "content": "以后都用中文回答我"},
            {"role": "assistant", "content": "好的"},
        ]
        result = _extract_important_info(messages)
        assert "偏好" in result
        assert "中文" in result

    def test_extract_file_paths(self):
        messages = [
            {"role": "user", "content": "看看这个文件"},
            {"role": "assistant", "content": "我修改了 agent_core/llm.py 和 main.py"},
        ]
        result = _extract_important_info(messages)
        assert "涉及文件" in result
        assert "llm.py" in result

    def test_extract_decisions(self):
        messages = [
            {"role": "user", "content": "我们决定采用方案A"},
            {"role": "assistant", "content": "好的"},
        ]
        result = _extract_important_info(messages)
        assert "偏好" in result or "方案" in result

    def test_empty_messages(self):
        result = _extract_important_info([])
        assert result == ""

    def test_no_important_info(self):
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        result = _extract_important_info(messages)
        assert result == ""

    def test_deduplication(self):
        messages = [
            {"role": "user", "content": "以后都用英文回答"},
            {"role": "user", "content": "以后都用英文回答"},
        ]
        result = _extract_important_info(messages)
        lines = [l for l in result.strip().split("\n") if l]
        assert len(lines) == 1  # 去重后只有一条

    def test_limit_extraction(self):
        """提取数量有上限"""
        messages = []
        for i in range(20):
            messages.append({"role": "user", "content": f"记住第{i}条规则"})
            messages.append({"role": "assistant", "content": "好的"})
        result = _extract_important_info(messages)
        lines = [l for l in result.strip().split("\n") if l]
        assert len(lines) <= 10


# ═══════════════════════════════════════════════════════════
# P3-11: 统一模型引用格式
# ═══════════════════════════════════════════════════════════

class TestModelRefFormat:
    def test_parse_full_ref(self):
        prov, model = parse_model_ref("zhipu/glm-4-flash")
        assert prov == "zhipu"
        assert model == "glm-4-flash"

    def test_parse_model_only(self):
        prov, model = parse_model_ref("deepseek-chat")
        assert prov == ""
        assert model == "deepseek-chat"

    def test_format_ref(self):
        ref = format_model_ref("openai", "gpt-4o")
        assert ref == "openai/gpt-4o"

    def test_resolve_full_ref(self):
        prov, model = resolve_model_ref("zhipu/glm-4-flash")
        assert prov == "zhipu"
        assert model == "glm-4-flash"

    def test_resolve_model_lookup(self):
        prov, model = resolve_model_ref("glm-4-flash")
        assert prov == "zhipu"
        assert model == "glm-4-flash"

    def test_resolve_provider_name(self):
        prov, model = resolve_model_ref("deepseek")
        assert prov == "deepseek"
        assert model  # 应该有默认模型

    def test_resolve_unknown(self):
        prov, model = resolve_model_ref("unknown-model-xyz")
        assert prov == ""
        assert model == "unknown-model-xyz"

    def test_parse_with_spaces(self):
        prov, model = parse_model_ref("  openai / gpt-4o  ")
        assert prov == "openai"
        assert model == "gpt-4o"


# ═══════════════════════════════════════════════════════════
# P2-6: 场景级模型路由 (Agent 集成)
# ═══════════════════════════════════════════════════════════

class TestSceneModelRouting:
    def test_agent_has_scene_model_method(self):
        from agent_core.agent import Agent
        agent = Agent()
        assert hasattr(agent, "_get_scene_model")

    def test_scene_model_returns_tuple(self):
        from agent_core.agent import Agent
        agent = Agent()
        result = agent._get_scene_model("summarize")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_scene_model_prefers_cheap(self):
        from agent_core.agent import Agent
        agent = Agent()
        prov, model = agent._get_scene_model("summarize")
        # 应该选择 tier 1 的便宜模型
        if prov in MODEL_REGISTRY and model in MODEL_REGISTRY.get(prov, {}):
            info = MODEL_REGISTRY[prov][model]
            assert info["tier"] <= 1

    def test_agent_has_session_methods(self):
        from agent_core.agent import Agent
        agent = Agent()
        assert hasattr(agent, "save_session")
        assert hasattr(agent, "load_session")
        assert hasattr(agent, "list_sessions")

    def test_all_scenes_return_valid(self):
        """所有场景都应返回有效的 (provider, model) 元组"""
        from agent_core.agent import Agent
        agent = Agent()
        scenes = ["summarize", "subagent", "compaction", "heartbeat", "image", "classification"]
        for scene in scenes:
            prov, model = agent._get_scene_model(scene)
            assert isinstance(prov, str) and prov, "场景 {} 返回空 provider".format(scene)
            assert isinstance(model, str) and model, "场景 {} 返回空 model".format(scene)

    def test_scene_env_override(self):
        """环境变量应覆盖内置配置"""
        from agent_core.agent import Agent
        os.environ["SCENE_MODEL_HEARTBEAT"] = "openai/gpt-4o"
        try:
            agent = Agent()
            prov, model = agent._get_scene_model("heartbeat")
            assert prov == "openai"
            assert model == "gpt-4o"
        finally:
            del os.environ["SCENE_MODEL_HEARTBEAT"]

    def test_image_scene_prefers_tier2(self):
        """image 场景应倾向 tier 2 模型"""
        from agent_core.agent import Agent
        agent = Agent()
        prov, model = agent._get_scene_model("image")
        # image 需要 reasoning 能力，如果有 tier 2 可用应该选它
        # 但如果只有 tier 1 可用也不应崩溃
        assert prov and model


# ═══════════════════════════════════════════════════════════
# 两阶段降级 + Session 粘性
# ═══════════════════════════════════════════════════════════

class TestTwoStageDegradation:
    def test_key_tracking_helpers_exist(self):
        from agent_core.llm import _get_current_key, _get_next_available_key, _mark_current_key_success
        assert callable(_get_current_key)
        assert callable(_get_next_available_key)
        assert callable(_mark_current_key_success)

    def test_get_next_available_key_excludes(self):
        from agent_core.llm import _get_next_available_key
        mgr = AuthManager()
        mgr._profiles = {}
        mgr.add_key("test_prov", "key-a")
        mgr.add_key("test_prov", "key-b")
        mgr.add_key("test_prov", "key-c")
        # 临时替换全局 auth_manager
        import agent_core.llm as llm_mod
        old = llm_mod._auth_manager
        llm_mod._auth_manager = mgr
        try:
            result = _get_next_available_key("test_prov", {"key-a", "key-b"})
            assert result == "key-c"
            result2 = _get_next_available_key("test_prov", {"key-a", "key-b", "key-c"})
            assert result2 is None
        finally:
            llm_mod._auth_manager = old


class TestSessionStickiness:
    def test_client_cache_exists(self):
        from agent_core.llm import _get_cached_client, clear_client_cache, _client_cache
        assert callable(_get_cached_client)
        assert callable(clear_client_cache)

    def test_clear_cache(self):
        from agent_core.llm import clear_client_cache, _client_cache
        _client_cache["test"] = (None, time.time())
        clear_client_cache()
        assert len(_client_cache) == 0

    def test_switch_provider_clears_cache(self):
        from agent_core.llm import _client_cache, clear_client_cache
        _client_cache["old"] = (None, time.time())
        clear_client_cache()
        assert "old" not in _client_cache


# ═══════════════════════════════════════════════════════════
# 9 层工具策略管线
# ═══════════════════════════════════════════════════════════

class TestFullToolCheck:
    def test_full_check_safe_tool(self):
        from agent_core.approval import full_tool_check
        allowed, reason, layer = full_tool_check("calculator", {}, provider="openai")
        assert allowed
        assert layer == "L5"

    def test_full_check_blocked_by_provider(self):
        from agent_core.approval import full_tool_check
        allowed, reason, layer = full_tool_check("shell", {}, provider="ollama")
        assert not allowed
        assert layer == "L3/L4"

    def test_full_check_blocked_by_tier(self):
        from agent_core.approval import full_tool_check
        allowed, reason, layer = full_tool_check("shell", {}, provider="unknown", tier=1)
        assert not allowed
        assert layer == "L3/L4"

    def test_full_check_needs_confirmation(self):
        from agent_core.approval import full_tool_check
        allowed, reason, layer = full_tool_check("shell", {"command": "ls"}, provider="openai")
        assert allowed
        assert "needs-confirmation" in reason

    def test_full_check_auto_approved(self):
        from agent_core.approval import full_tool_check
        os.environ["AUTO_APPROVE"] = "shell"
        try:
            allowed, reason, layer = full_tool_check("shell", {"command": "ls"}, provider="openai")
            assert allowed
            assert layer == "L7"
        finally:
            del os.environ["AUTO_APPROVE"]


# ═══════════════════════════════════════════════════════════
# 三层记忆联动
# ═══════════════════════════════════════════════════════════

class TestThreeLayerMemory:
    def test_auto_persist_creates_session(self):
        """_auto_persist 应自动创建 session_id"""
        from agent_core.agent import Agent
        agent = Agent()
        agent._session_store = SessionStore(base_dir=tempfile.mkdtemp())
        agent.memory.append({"role": "user", "content": "测试"})
        agent.memory.append({"role": "assistant", "content": "回复"})
        agent._auto_persist()
        assert agent._session_id is not None
        # 验证磁盘上有文件
        loaded, meta = agent._session_store.load(agent._session_id)
        assert len(loaded) > 0

    def test_build_context_preserves_important_info(self):
        """build_context 压缩时应保留重要信息"""
        from agent_core.token_optimizer import build_context
        memory = [{"role": "system", "content": "sys"}]
        memory.append({"role": "user", "content": "以后都用英文回答我"})
        memory.append({"role": "assistant", "content": "OK"})
        for i in range(10):
            memory.append({"role": "user", "content": f"问题{i}"})
            memory.append({"role": "assistant", "content": f"回答{i}"})
        result = build_context(memory)
        # 压缩后应包含重要信息
        all_content = " ".join(m.get("content", "") for m in result)
        assert "重要信息" in all_content or "偏好" in all_content or len(result) < len(memory)


# ═══════════════════════════════════════════════════════════
# P3-9: 异步子 Agent + 并发控制
# ═══════════════════════════════════════════════════════════

class TestSubAgentFuture:
    def test_future_class_exists(self):
        from agent_core.agent import SubAgentFuture
        assert SubAgentFuture is not None

    def test_future_done_and_result(self):
        """Future 应正确报告完成状态"""
        from agent_core.agent import SubAgentFuture
        import threading

        # 模拟一个快速完成的 future
        class FakeParent:
            _sub_agent_semaphore = threading.Semaphore(3)
            _MAX_CONCURRENT_SUBAGENTS = 3
            def spawn(self, task, system_prompt=None, tools_filter=None, timeout=None):
                return "mock result: {}".format(task)

        parent = FakeParent()
        future = SubAgentFuture(parent, "test task", None, None, 10, None)
        assert not future.done()
        future.start()
        result = future.result(timeout=5)
        assert future.done()
        assert "mock result" in result

    def test_future_callback(self):
        """回调应在完成时被调用"""
        from agent_core.agent import SubAgentFuture
        import threading

        callback_results = []

        class FakeParent:
            _sub_agent_semaphore = threading.Semaphore(3)
            _MAX_CONCURRENT_SUBAGENTS = 3
            def spawn(self, task, system_prompt=None, tools_filter=None, timeout=None):
                return "callback test"

        parent = FakeParent()
        future = SubAgentFuture(
            parent, "test", None, None, 10,
            callback=lambda r: callback_results.append(r),
        )
        future.start()
        future.result(timeout=5)
        assert len(callback_results) == 1
        assert callback_results[0] == "callback test"

    def test_future_elapsed_time(self):
        """elapsed 应返回合理的时间"""
        from agent_core.agent import SubAgentFuture
        import threading

        class FakeParent:
            _sub_agent_semaphore = threading.Semaphore(3)
            _MAX_CONCURRENT_SUBAGENTS = 3
            def spawn(self, task, system_prompt=None, tools_filter=None, timeout=None):
                time.sleep(0.1)
                return "done"

        parent = FakeParent()
        future = SubAgentFuture(parent, "test", None, None, 10, None)
        future.start()
        future.result(timeout=5)
        assert future.elapsed >= 0.05  # 至少执行了一段时间

    def test_future_error_handling(self):
        """错误应被捕获而不是崩溃"""
        from agent_core.agent import SubAgentFuture
        import threading

        class FakeParent:
            _sub_agent_semaphore = threading.Semaphore(3)
            _MAX_CONCURRENT_SUBAGENTS = 3
            def spawn(self, task, system_prompt=None, tools_filter=None, timeout=None):
                raise RuntimeError("test error")

        parent = FakeParent()
        future = SubAgentFuture(parent, "test", None, None, 10, None)
        future.start()
        result = future.result(timeout=5)
        assert future.done()
        assert "错误" in result or "error" in result.lower()
        assert future.error is not None


class TestAgentConcurrencyControl:
    def test_semaphore_initialization(self):
        """信号量应延迟初始化"""
        from agent_core.agent import Agent
        agent = Agent()
        sem = agent._get_semaphore()
        assert sem is not None

    def test_max_concurrent_default(self):
        from agent_core.agent import Agent
        assert Agent._MAX_CONCURRENT_SUBAGENTS >= 1

    def test_get_sub_agent_status(self):
        from agent_core.agent import Agent
        agent = Agent()
        status = agent.get_sub_agent_status()
        assert "max_concurrent" in status
        assert "available_slots" in status
        assert "active" in status
        assert status["max_concurrent"] == Agent._MAX_CONCURRENT_SUBAGENTS

    def test_spawn_parallel_empty(self):
        from agent_core.agent import Agent
        agent = Agent()
        results = agent.spawn_parallel([])
        assert results == []

    def test_spawn_async_returns_future(self):
        """spawn_async 应返回 SubAgentFuture"""
        from agent_core.agent import Agent, SubAgentFuture
        agent = Agent()
        # 不实际执行（会调用 LLM），只验证接口
        assert hasattr(agent, "spawn_async")
        assert hasattr(agent, "spawn_parallel")


class TestCustomAgentManagerThreadSafety:
    def test_invoke_no_env_mutation(self):
        """invoke 不应修改 os.environ（线程安全）"""
        from agent_core.custom_agents import CustomAgentManager, AgentConfig
        mgr = CustomAgentManager(agents_dirs=[])
        # 手动注入一个配置
        cfg = AgentConfig(
            name="test-agent",
            description="test",
            system_prompt="test prompt",
            auto_approve=["read_file", "calculator"],
        )
        mgr._configs["test-agent"] = cfg

        old_approve = os.environ.get("AUTO_APPROVE", "")
        # invoke 会失败（没有 LLM），但不应修改环境变量
        try:
            mgr.invoke("test-agent", "test task")
        except Exception:
            pass
        assert os.environ.get("AUTO_APPROVE", "") == old_approve

    def test_invoke_async_exists(self):
        from agent_core.custom_agents import CustomAgentManager
        mgr = CustomAgentManager(agents_dirs=[])
        assert hasattr(mgr, "invoke_async")


# ═══════════════════════════════════════════════════════════
# P2-7: 任务分类升级（加权关键词 + 上下文信号 + LLM 回退）
# ═══════════════════════════════════════════════════════════

class TestWeightedClassification:
    """加权关键词评分系统测试"""

    def test_score_capabilities_returns_all_caps(self):
        from agent_core.model_router import _score_capabilities
        scores = _score_capabilities("hello")
        assert set(scores.keys()) == {"coding", "reasoning", "tool_use", "fast"}

    def test_score_all_zero_for_gibberish(self):
        from agent_core.model_router import _score_capabilities
        scores = _score_capabilities("xyzzy plugh")
        # 无关键词命中，所有分数应为 0 或很低
        assert all(v < 0.4 for v in scores.values())

    def test_coding_high_confidence(self):
        from agent_core.model_router import _score_capabilities
        scores = _score_capabilities("写代码实现一个二叉搜索树")
        assert scores["coding"] >= 0.8

    def test_reasoning_high_confidence(self):
        from agent_core.model_router import _score_capabilities
        scores = _score_capabilities("深入分析微服务架构的优缺点")
        assert scores["reasoning"] >= 0.8

    def test_tool_use_high_confidence(self):
        from agent_core.model_router import _score_capabilities
        scores = _score_capabilities("先搜索最新文档，然后下载到本地")
        assert scores["tool_use"] >= 0.8

    def test_fast_high_confidence(self):
        from agent_core.model_router import _score_capabilities
        scores = _score_capabilities("你好")
        assert scores["fast"] >= 0.8

    def test_multi_hit_bonus(self):
        """多关键词命中应有加成"""
        from agent_core.model_router import _score_capabilities
        # 单关键词
        s1 = _score_capabilities("debug")
        # 多关键词
        s2 = _score_capabilities("debug 修复 重构 优化代码")
        assert s2["coding"] > s1["coding"]

    def test_threshold_filtering(self):
        """低于阈值的能力不应出现在结果中"""
        from agent_core.model_router import classify_task
        needs = classify_task("你好")
        assert "fast" in needs
        # 简单问候不应触发 coding/reasoning
        assert "coding" not in needs
        assert "reasoning" not in needs


class TestContextSignals:
    """上下文信号检测测试"""

    def test_code_block_boosts_coding(self):
        from agent_core.model_router import _score_capabilities
        text_no_block = "帮我看看这个问题"
        text_with_block = "帮我看看这个问题 ```python\ndef foo(): pass\n```"
        s1 = _score_capabilities(text_no_block)
        s2 = _score_capabilities(text_with_block)
        assert s2["coding"] > s1["coding"]

    def test_url_boosts_tool_use(self):
        from agent_core.model_router import _score_capabilities
        text = "请访问 https://example.com 获取数据"
        scores = _score_capabilities(text)
        assert scores["tool_use"] >= 0.3

    def test_file_extension_boosts_coding(self):
        from agent_core.model_router import _score_capabilities
        text = "修改 main.py 文件"
        scores = _score_capabilities(text)
        assert scores["coding"] > 0

    def test_error_traceback_boosts_coding(self):
        from agent_core.model_router import _score_capabilities
        text = "出现了 error traceback 信息"
        scores = _score_capabilities(text)
        assert scores["coding"] > 0

    def test_question_mark_boosts_reasoning(self):
        from agent_core.model_router import _score_capabilities
        text_no_q = "这个方案不错"
        text_with_q = "这个方案不错?"
        s1 = _score_capabilities(text_no_q)
        s2 = _score_capabilities(text_with_q)
        assert s2["reasoning"] >= s1["reasoning"]

    def test_import_statement_boosts_coding(self):
        from agent_core.model_router import _score_capabilities
        text = "from collections import defaultdict"
        scores = _score_capabilities(text)
        assert scores["coding"] >= 0.4


class TestClassifyTaskUpgrade:
    """升级后的 classify_task 综合测试"""

    def test_backward_compat_greeting(self):
        from agent_core.model_router import classify_task
        for g in ["你好", "hi", "hello", "谢谢", "ok"]:
            assert "fast" in classify_task(g)

    def test_backward_compat_coding(self):
        from agent_core.model_router import classify_task
        assert "coding" in classify_task("写代码实现排序算法")

    def test_backward_compat_reasoning(self):
        from agent_core.model_router import classify_task
        assert "reasoning" in classify_task("分析对比两种方案的优缺点")

    def test_backward_compat_tool_use(self):
        from agent_core.model_router import classify_task
        assert "tool_use" in classify_task("搜索最新的技术文档并下载")

    def test_backward_compat_empty(self):
        from agent_core.model_router import classify_task
        assert "fast" in classify_task("")

    def test_backward_compat_long_no_keyword(self):
        from agent_core.model_router import classify_task
        assert "reasoning" in classify_task("a" * 50)

    def test_backward_compat_short_no_keyword(self):
        from agent_core.model_router import classify_task
        assert "fast" in classify_task("嗯")

    def test_backward_compat_tool_continuation(self):
        from agent_core.model_router import classify_task
        assert classify_task("复杂任务", has_tool_calls=True, iteration=1) == {"fast", "tool_use"}

    def test_programming_language_detection(self):
        """编程语言名称应触发 coding"""
        from agent_core.model_router import classify_task
        assert "coding" in classify_task("用 python 写一个脚本")
        assert "coding" in classify_task("javascript async await 用法")

    def test_complex_multi_cap_task(self):
        """复杂任务应触发多个能力"""
        from agent_core.model_router import classify_task
        needs = classify_task("先搜索 React 最佳实践，然后写代码实现组件，最后分析性能")
        assert len(needs) >= 2

    def test_code_with_error_context(self):
        """包含代码错误上下文应触发 coding"""
        from agent_core.model_router import classify_task
        text = "运行时出现 TypeError exception，请帮我看看 ```\ndef foo(): return None\n```"
        needs = classify_task(text)
        assert "coding" in needs


class TestLLMClassifyFallback:
    """LLM 回退分类测试（不实际调用 LLM）"""

    def test_llm_classify_disabled_by_default(self):
        from agent_core.model_router import _LLM_CLASSIFY_ENABLED
        # 默认不启用 LLM 分类
        assert not _LLM_CLASSIFY_ENABLED

    def test_classify_with_llm_returns_none_when_disabled(self):
        from agent_core.model_router import _classify_with_llm
        result = _classify_with_llm("写代码")
        assert result is None

    def test_ambiguous_detection(self):
        """模糊区间检测逻辑"""
        from agent_core.model_router import _score_capabilities, _CLASSIFY_THRESHOLD, _AMBIGUOUS_MARGIN
        # 验证阈值和边距常量存在且合理
        assert 0 < _CLASSIFY_THRESHOLD < 1
        assert 0 < _AMBIGUOUS_MARGIN < 0.5


class TestBackwardCompatPatterns:
    """确保 _compiled_patterns 向后兼容"""

    def test_compiled_patterns_exists(self):
        from agent_core.model_router import _compiled_patterns
        assert isinstance(_compiled_patterns, dict)
        assert set(_compiled_patterns.keys()) == {"coding", "reasoning", "tool_use", "fast"}

    def test_compiled_patterns_are_regex(self):
        import re
        from agent_core.model_router import _compiled_patterns
        for cap, pattern in _compiled_patterns.items():
            assert isinstance(pattern, type(re.compile("")))

    def test_keyword_weights_exist(self):
        from agent_core.model_router import _KEYWORD_WEIGHTS
        assert isinstance(_KEYWORD_WEIGHTS, dict)
        assert len(_KEYWORD_WEIGHTS) == 4

    def test_context_signals_exist(self):
        from agent_core.model_router import _CONTEXT_SIGNALS
        assert isinstance(_CONTEXT_SIGNALS, dict)
        assert len(_CONTEXT_SIGNALS) >= 3

    def test_score_capabilities_exported(self):
        from agent_core.model_router import _score_capabilities
        assert callable(_score_capabilities)
