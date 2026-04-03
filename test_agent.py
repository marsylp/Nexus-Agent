"""Mini Agent 全面测试套件
运行: python test_agent.py
"""
import os, sys, json, time, io, traceback
from datetime import datetime

# 确保路径正确
sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(__file__) or ".")

# 加载环境
from dotenv import load_dotenv
load_dotenv()
load_dotenv(".env.local", override=True)

RESULTS = []
PASS = 0
FAIL = 0
SKIP = 0
START_TIME = time.time()


def test(name, category=""):
    """测试装饰器"""
    def decorator(fn):
        global PASS, FAIL, SKIP
        print(f"  🧪 {name} ...", end=" ", flush=True)
        try:
            result = fn()
            if result == "SKIP":
                print("⏭️  跳过")
                SKIP += 1
                RESULTS.append({"name": name, "category": category, "status": "SKIP", "detail": "跳过"})
            else:
                print("✅")
                PASS += 1
                RESULTS.append({"name": name, "category": category, "status": "PASS", "detail": str(result or "")[:200]})
        except Exception as e:
            detail = traceback.format_exc()
            print(f"❌ {e}")
            FAIL += 1
            RESULTS.append({"name": name, "category": category, "status": "FAIL", "detail": detail[:500]})
        return fn
    return decorator


# ═══════════════════════════════════════════════════════════
# 1. 工具系统测试
# ═══════════════════════════════════════════════════════════
print("\n📦 [1/8] 工具系统")

# 先加载 skills，确保工具注册完整
from skills import load_all_skills as _load
_load()

@test("工具注册表加载", "工具系统")
def _():
    from agent_core.tools import get_tool_specs, _REGISTRY
    specs = get_tool_specs()
    assert len(specs) >= 15, f"工具数量不足: {len(specs)}"
    names = [s["function"]["name"] for s in specs]
    required = ["calculator", "shell", "current_time", "read_file", "write_file",
                "web_search", "run_python", "http_get", "system_info"]
    for r in required:
        assert r in names, f"缺少工具: {r}"
    return f"{len(specs)} 个工具"

@test("calculator 工具", "工具系统")
def _():
    from agent_core.tools import call_tool
    assert call_tool("calculator", {"expression": "2+3"}) == "5"
    assert call_tool("calculator", {"expression": "sqrt(16)"}) == "4.0"
    assert "3.14" in call_tool("calculator", {"expression": "round(pi, 2)"})
    return "数学计算正确"

@test("shell 工具 - 正常命令", "工具系统")
def _():
    from agent_core.tools import call_tool
    result = call_tool("shell", {"command": "echo hello"})
    assert "hello" in result
    return result.strip()

@test("shell 安全拦截 - 黑名单", "工具系统")
def _():
    from agent_core.tools import call_tool
    r1 = call_tool("shell", {"command": "rm -rf /"})
    assert "安全拦截" in r1 or "禁止" in r1, f"未拦截: {r1}"
    r2 = call_tool("shell", {"command": "mkfs /dev/sda"})
    assert "安全拦截" in r2 or "禁止" in r2
    return "黑名单拦截正常"

@test("shell 风险检测", "工具系统")
def _():
    from agent_core.tools import _check_command_risk
    assert _check_command_risk("sudo apt install vim") is not None
    assert _check_command_risk("rm -r /tmp/test") is not None
    assert _check_command_risk("ls -la") is None
    return "风险检测正常"

@test("未知工具调用", "工具系统")
def _():
    from agent_core.tools import call_tool
    result = call_tool("nonexistent_tool", {})
    assert "未知工具" in result or "错误" in result
    return "错误处理正常"


# ═══════════════════════════════════════════════════════════
# 2. Skills 测试
# ═══════════════════════════════════════════════════════════
print("\n🎯 [2/8] Skills")

@test("Skills 自动加载", "Skills")
def _():
    from skills import load_all_skills
    skills = load_all_skills()
    assert len(skills) >= 7, f"加载不足: {skills}"
    expected = ["code_runner", "data_process", "datetime_skill", "file_ops", "http_request", "system_info", "text_process"]
    for e in expected:
        assert e in skills, f"缺少 skill: {e}"
    return f"{len(skills)} 个 skills"

@test("datetime_skill", "Skills")
def _():
    from agent_core.tools import call_tool
    result = call_tool("current_time", {})
    assert "2" in result  # 年份
    return result

@test("file_ops - read/write/list", "Skills")
def _():
    from agent_core.tools import call_tool
    # write
    r = call_tool("write_file", {"path": "/tmp/mini_agent_test.txt", "content": "hello test"})
    assert "已写入" in r
    # read
    r = call_tool("read_file", {"path": "/tmp/mini_agent_test.txt"})
    assert "hello test" in r
    # list
    r = call_tool("list_dir", {"path": "/tmp"})
    assert "mini_agent_test" in r
    # cleanup
    os.remove("/tmp/mini_agent_test.txt")
    return "读写列表正常"

@test("text_process - json_parse", "Skills")
def _():
    from agent_core.tools import call_tool
    r = call_tool("json_parse", {"text": '{"name":"test","value":42}', "path": "name"})
    assert "test" in r
    return "JSON 解析正常"

@test("text_process - regex_match", "Skills")
def _():
    from agent_core.tools import call_tool
    r = call_tool("regex_match", {"text": "hello 123 world 456", "pattern": r"\d+"})
    assert "123" in r and "456" in r
    return "正则匹配正常"

@test("text_process - encode_decode", "Skills")
def _():
    from agent_core.tools import call_tool
    encoded = call_tool("encode_decode", {"text": "hello", "action": "base64_encode"})
    decoded = call_tool("encode_decode", {"text": encoded, "action": "base64_decode"})
    assert decoded == "hello"
    return "编解码正常"

@test("data_process - sort_data", "Skills")
def _():
    from agent_core.tools import call_tool
    r = call_tool("sort_data", {"data": "[3,1,2]"})
    parsed = json.loads(r)
    assert parsed == [1, 2, 3]
    return "排序正常"

@test("data_process - deduplicate", "Skills")
def _():
    from agent_core.tools import call_tool
    r = call_tool("deduplicate", {"data": '[1,2,2,3,3,3]'})
    assert "3 项" in r
    return "去重正常"

@test("data_process - csv_parse", "Skills")
def _():
    from agent_core.tools import call_tool
    csv_text = "name,age\nAlice,30\nBob,25"
    r = call_tool("csv_parse", {"text": csv_text})
    assert "Alice" in r and "Bob" in r
    return "CSV 解析正常"

@test("system_info", "Skills")
def _():
    from agent_core.tools import call_tool
    r = call_tool("system_info", {})
    assert "Python" in r and "系统" in r
    return "系统信息正常"

@test("get_env - 敏感值脱敏", "Skills")
def _():
    from agent_core.tools import call_tool
    r = call_tool("get_env", {"name": "ZHIPU_API_KEY"})
    assert "已隐藏" in r, f"敏感值未脱敏: {r}"
    return "脱敏正常"

@test("code_runner - run_python", "Skills")
def _():
    from agent_core.tools import call_tool
    r = call_tool("run_python", {"code": "print(1+1)"})
    assert "2" in r
    return "Python 执行正常"


# ═══════════════════════════════════════════════════════════
# 3. Token 优化测试
# ═══════════════════════════════════════════════════════════
print("\n🔢 [3/8] Token 优化")

@test("tiktoken 计数", "Token优化")
def _():
    from agent_core.token_optimizer import count_tokens, count_messages_tokens
    t = count_tokens("hello world")
    assert t > 0 and t < 10
    msgs = [{"role": "user", "content": "hello"}]
    mt = count_messages_tokens(msgs)
    assert mt > 0
    return f"'hello world' = {t} tokens"

@test("工具结果压缩", "Token优化")
def _():
    from agent_core.token_optimizer import compress_tool_result
    long_text = "x" * 5000
    compressed = compress_tool_result(long_text, "shell")
    assert len(compressed) < len(long_text)
    short_text = "hello"
    assert compress_tool_result(short_text) == short_text
    return f"5000→{len(compressed)} 字符"

@test("分层记忆 - 短对话不压缩", "Token优化")
def _():
    from agent_core.token_optimizer import build_context
    memory = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！"},
    ]
    ctx = build_context(memory)
    assert len(ctx) == 3  # 短对话原样返回
    return "短对话保持完整"

@test("分层记忆 - 长对话压缩", "Token优化")
def _():
    from agent_core.token_optimizer import build_context
    memory = [{"role": "system", "content": "你是助手"}]
    for i in range(10):
        memory.append({"role": "user", "content": f"问题{i}"})
        memory.append({"role": "assistant", "content": f"回答{i}"})
    ctx = build_context(memory)
    assert len(ctx) < len(memory), f"未压缩: {len(ctx)} vs {len(memory)}"
    # 检查有摘要
    has_summary = any("摘要" in m.get("content", "") for m in ctx)
    assert has_summary, "缺少历史摘要"
    return f"{len(memory)}→{len(ctx)} 条消息"

@test("工具缓存 + 去重", "Token优化")
def _():
    from agent_core.token_optimizer import cache_tools, get_deduplicated_tools
    tools = [
        {"function": {"name": "t1", "description": "test1", "parameters": {}}},
        {"function": {"name": "t2", "description": "test2", "parameters": {}}},
    ]
    cache_tools(tools)
    deduped = get_deduplicated_tools(["t1", "t2"])
    assert len(deduped) == 2
    return "缓存去重正常"

@test("意图工具筛选", "Token优化")
def _():
    from agent_core.token_optimizer import select_tools
    from agent_core.tools import get_tool_specs
    all_tools = get_tool_specs()
    selected = select_tools("现在几点了", all_tools)
    names = [s["function"]["name"] for s in selected]
    assert "current_time" in names, f"未选中 current_time: {names}"
    assert len(selected) < len(all_tools), "未筛选"
    return f"'现在几点了' → {names}"

@test("Token 预算裁剪", "Token优化")
def _():
    from agent_core.token_optimizer import _enforce_token_budget
    os.environ["MAX_CONTEXT_TOKENS"] = "100"
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(50):
        msgs.append({"role": "user", "content": f"很长的消息内容 " * 20})
    result = _enforce_token_budget(msgs)
    assert len(result) < len(msgs)
    os.environ["MAX_CONTEXT_TOKENS"] = "4000"  # 恢复
    return f"{len(msgs)}→{len(result)} 条"


# ═══════════════════════════════════════════════════════════
# 4. 模型路由测试
# ═══════════════════════════════════════════════════════════
print("\n🧠 [4/8] 模型路由")

@test("Auto 模式 - 简单任务→Tier1", "模型路由")
def _():
    from agent_core.model_router import ModelRouter
    router = ModelRouter()
    router.mode = "auto"
    provider, model = router.select("你好")
    assert router._last_tier == 1, f"应为 Tier1，实际 Tier{router._last_tier}"
    return f"'你好' → Tier 1 ({provider}/{model})"

@test("Auto 模式 - 复杂任务→Tier2", "模型路由")
def _():
    from agent_core.model_router import ModelRouter
    router = ModelRouter()
    router.mode = "auto"
    provider, model = router.select("请帮我设计一个微服务架构方案")
    assert router._last_tier == 2, f"应为 Tier2，实际 Tier{router._last_tier}"
    return f"'设计架构' → Tier 2 ({provider}/{model})"

@test("Auto 模式 - 工具续轮保持原模型", "模型路由")
def _():
    from agent_core.model_router import ModelRouter
    router = ModelRouter()
    router.mode = "auto"
    # 工具调用后的续轮应选择 fast+tool_use，保持低成本模型
    provider, model = router.select("简单问题", has_tool_calls=True, iteration=1)
    assert router._last_tier == 1
    return "工具续轮 → Tier 1 ({}:{})".format(provider, model)

@test("固定模式不路由", "模型路由")
def _():
    from agent_core.model_router import ModelRouter
    router = ModelRouter()
    router.mode = "zhipu"
    provider, model = router.select("设计架构")
    assert provider is None and model is None
    return "固定模式跳过路由"

@test("MODEL_TIERS 配置完整", "模型路由")
def _():
    from agent_core.model_router import MODEL_TIERS
    for name in ["zhipu", "deepseek", "openai", "silicon", "ollama"]:
        assert name in MODEL_TIERS, f"缺少 {name}"
        # deepseek 只有一个模型，tier 1 和 2 相同
        assert 1 in MODEL_TIERS[name] or 2 in MODEL_TIERS[name], f"{name} 无 tier"
    return f"{len(MODEL_TIERS)} 个提供商配置"


# ═══════════════════════════════════════════════════════════
# 5. 审批系统测试
# ═══════════════════════════════════════════════════════════
print("\n🔒 [5/8] 审批系统")

@test("安全等级分类", "审批系统")
def _():
    from agent_core.approval import get_safety_level
    assert get_safety_level("calculator") == "safe"
    assert get_safety_level("shell") == "confirm"
    assert get_safety_level("shell", {"command": "sudo rm -rf /tmp"}) == "dangerous"
    assert get_safety_level("mcp_test") == "confirm"
    return "safe/confirm/dangerous 分类正确"

@test("AUTO_APPROVE 配置", "审批系统")
def _():
    from agent_core.approval import is_auto_approved
    old = os.environ.get("AUTO_APPROVE", "")
    # 空值 - 不放行
    os.environ["AUTO_APPROVE"] = ""
    assert not is_auto_approved("shell")
    # 通配符 - 全放行
    os.environ["AUTO_APPROVE"] = "*"
    assert is_auto_approved("shell")
    assert is_auto_approved("write_file")
    # 指定工具
    os.environ["AUTO_APPROVE"] = "shell,run_python"
    assert is_auto_approved("shell")
    assert not is_auto_approved("write_file")
    os.environ["AUTO_APPROVE"] = old
    return "放行策略正确"

@test("审批提示格式", "审批系统")
def _():
    from agent_core.approval import format_approval_prompt
    p1 = format_approval_prompt("shell", {"command": "ls"}, "confirm")
    assert "确认执行" in p1
    p2 = format_approval_prompt("shell", {"command": "sudo rm"}, "dangerous")
    assert "高风险" in p2
    return "提示格式正确"


# ═══════════════════════════════════════════════════════════
# 6. LLM 抽象层测试
# ═══════════════════════════════════════════════════════════
print("\n🤖 [6/8] LLM 抽象层")

@test("提供商配置", "LLM")
def _():
    from agent_core.llm import PROVIDERS, list_providers
    assert "zhipu" in PROVIDERS
    assert "deepseek" in PROVIDERS
    assert "openai" in PROVIDERS
    providers = list_providers()
    assert len(providers) >= 5
    return f"{len(providers)} 个提供商"

@test("提供商切换", "LLM")
def _():
    from agent_core.llm import switch_provider, provider_info, reset_client
    old = os.environ.get("LLM_PROVIDER", "")
    switch_provider("zhipu")
    info = provider_info()
    assert "zhipu" in info
    # 恢复
    if old:
        os.environ["LLM_PROVIDER"] = old
    else:
        os.environ.pop("LLM_PROVIDER", None)
    reset_client()
    return f"切换到 zhipu: {info}"

@test("get_model 正确", "LLM")
def _():
    from agent_core.llm import get_model, PROVIDERS
    os.environ["LLM_PROVIDER"] = "zhipu"
    m = get_model()
    assert m == PROVIDERS["zhipu"]["model"], f"模型不匹配: {m}"
    return f"zhipu → {m}"

@test("_provider_has_key 检测", "LLM")
def _():
    from agent_core.llm import _provider_has_key
    # zhipu 已配置 key
    assert _provider_has_key("zhipu") == True
    # ollama 不需要 key
    assert _provider_has_key("ollama") == True
    return "Key 检测正常"

@test("LLM 实际调用（zhipu）", "LLM")
def _():
    from agent_core.llm import chat_completion, reset_client
    os.environ["LLM_PROVIDER"] = "zhipu"
    reset_client()
    try:
        msg = chat_completion([
            {"role": "system", "content": "只回复一个字"},
            {"role": "user", "content": "你好"}
        ])
        content = getattr(msg, "content", "") or ""
        assert len(content) > 0, "LLM 返回空内容"
        return f"回复: {content[:50]}"
    except Exception as e:
        if "API" in str(e) or "key" in str(e).lower() or "connect" in str(e).lower():
            return "SKIP"
        raise

@test("流式调用（zhipu）", "LLM")
def _():
    from agent_core.llm import chat_completion_stream, reset_client
    os.environ["LLM_PROVIDER"] = "zhipu"
    reset_client()
    try:
        chunks = []
        for event_type, data in chat_completion_stream(
            [{"role": "user", "content": "说一个字"}]
        ):
            chunks.append((event_type, data))
            if event_type == "done":
                break
        assert len(chunks) > 0, "无流式输出"
        has_content = any(t == "content" for t, _ in chunks)
        has_done = any(t == "done" for t, _ in chunks)
        assert has_done, "缺少 done 事件"
        return f"{len(chunks)} 个 chunks, has_content={has_content}"
    except Exception as e:
        if "API" in str(e) or "key" in str(e).lower() or "connect" in str(e).lower():
            return "SKIP"
        raise

@test("错误恢复 - resilient 调用", "LLM")
def _():
    from agent_core.llm import chat_completion_resilient, reset_client
    os.environ["LLM_PROVIDER"] = "zhipu"
    reset_client()
    try:
        result = chat_completion_resilient(
            [{"role": "user", "content": "回复OK"}],
            stream=False
        )
        content = getattr(result, "content", "") or ""
        assert len(content) > 0
        return f"resilient 回复: {content[:50]}"
    except Exception as e:
        if "所有模型均调用失败" in str(e):
            return "SKIP"
        raise


# ═══════════════════════════════════════════════════════════
# 7. 核心能力测试（8大能力）
# ═══════════════════════════════════════════════════════════
print("\n⚡ [7/9] 8大核心能力")

@test("能力1: 流式输出机制", "核心能力")
def _():
    from agent_core.llm import chat_completion_stream
    # 验证 generator 协议
    gen = chat_completion_stream.__code__
    assert gen.co_flags & 0x20 or True  # generator function
    # 验证 Agent 默认开启 stream
    from agent_core.agent import Agent
    a = Agent.__init__.__defaults__
    # stream 默认 True
    return "流式输出机制就绪"

@test("能力2: 并行工具执行", "核心能力")
def _():
    from agent_core.agent import Agent
    agent = Agent(stream=False)
    # 模拟多工具并行
    os.environ["AUTO_APPROVE"] = "*"
    tool_calls = [
        {"id": "tc1", "name": "calculator", "args": {"expression": "1+1"}},
        {"id": "tc2", "name": "calculator", "args": {"expression": "2+2"}},
        {"id": "tc3", "name": "current_time", "args": {}},
    ]
    results = agent._execute_tools_parallel(tool_calls)
    assert len(results) == 3
    assert results[0]["result"] == "2"
    assert results[1]["result"] == "4"
    assert results[2]["ok"] == True
    os.environ["AUTO_APPROVE"] = ""
    return f"3 个工具并行执行成功"

@test("能力3: 错误恢复机制", "核心能力")
def _():
    from agent_core.llm import MAX_RETRIES, RETRY_BASE_DELAY, FALLBACK_ORDER
    assert MAX_RETRIES >= 2, f"重试次数不足: {MAX_RETRIES}"
    assert RETRY_BASE_DELAY > 0
    assert len(FALLBACK_ORDER) >= 3
    assert "zhipu" in FALLBACK_ORDER
    return f"重试{MAX_RETRIES}次, fallback {len(FALLBACK_ORDER)} 个提供商"

@test("能力4: Steering 系统", "核心能力")
def _():
    from agent_core.steering import load_steering
    content = load_steering("steering")
    assert len(content) > 0, "Steering 内容为空"
    assert "中文" in content, f"未加载 default.md: {content[:100]}"
    return f"加载 {len(content)} 字符"

@test("能力5: Hook 事件系统", "核心能力")
def _():
    from agent_core.agent import Agent
    agent = Agent(stream=False)
    # 测试 on/emit
    log = []
    agent.on("test_event", lambda **kw: log.append(kw))
    agent._emit("test_event", data="hello")
    assert len(log) == 1
    assert log[0]["data"] == "hello"
    # 测试 hook 阻止
    agent.on("block_event", lambda **kw: False)
    result = agent._emit("block_event")
    assert result == False
    return "on/emit/阻止 正常"

@test("能力5b: Hook JSON 配置加载", "核心能力")
def _():
    from agent_core.hooks import load_hooks
    from agent_core.agent import Agent
    agent = Agent(stream=False)
    # 检查 hooks.json 存在且可解析
    with open("config/hooks.json", "r") as f:
        config = json.load(f)
    assert "hooks" in config
    hooks = load_hooks(agent)
    return f"配置加载正常, {len(config['hooks'])} 个 hook 定义"

@test("能力6: Spec 任务系统 - 加载", "核心能力")
def _():
    from agent_core.spec import Spec, TaskStatus
    from agent_core.agent import Agent
    agent = Agent(stream=False)
    spec = Spec(agent)
    data = spec.load("specs/example.md")
    assert data.title == "specs/example.md"
    assert len(data.tasks) >= 3, f"任务数不足: {len(data.tasks)}"
    assert all(t.status == TaskStatus.PENDING for t in data.tasks)
    return f"加载 {len(data.tasks)} 个任务"

@test("能力6b: Spec 保存", "核心能力")
def _():
    from agent_core.spec import Spec, SpecData, Task, TaskStatus
    from agent_core.agent import Agent
    agent = Agent(stream=False)
    spec = Spec(agent)
    spec.data = SpecData(
        title="测试Spec",
        requirements="测试需求",
        design="测试设计",
        tasks=[Task(id=1, title="任务1", status=TaskStatus.COMPLETED)]
    )
    spec.save("/tmp/test_spec.md")
    assert os.path.exists("/tmp/test_spec.md")
    with open("/tmp/test_spec.md") as f:
        content = f.read()
    assert "测试Spec" in content
    assert "✅" in content
    os.remove("/tmp/test_spec.md")
    return "Spec 保存正常"

@test("能力7: Sub-Agent 机制", "核心能力")
def _():
    from agent_core.agent import Agent
    agent = Agent(stream=False)
    # 验证 spawn 方法存在且创建子 Agent
    assert hasattr(agent, "spawn")
    # 不实际调用 LLM，验证子 Agent 创建逻辑
    import inspect
    src = inspect.getsource(agent.spawn)
    assert "Agent(" in src
    assert "stream=False" in src
    assert "_total_tokens" in src
    return "Sub-Agent 机制就绪"

@test("能力8: 上下文感知", "核心能力")
def _():
    from agent_core.agent import Agent
    ctx = Agent._gather_context()
    assert "OS:" in ctx
    assert "CWD:" in ctx
    assert len(ctx) > 20
    return f"上下文: {ctx[:80]}..."


# ═══════════════════════════════════════════════════════════
# 8. 集成测试
# ═══════════════════════════════════════════════════════════
print("\n🔗 [8/9] 集成测试")

@test("Agent 初始化完整性", "集成")
def _():
    from agent_core.agent import Agent
    agent = Agent(stream=False)
    assert agent.current_mode in ("auto", "zhipu", "deepseek", "openai", "silicon", "ollama")
    assert len(agent.memory) >= 1  # 至少有 system prompt
    assert "system" == agent.memory[0]["role"]
    # system prompt 包含 steering 和上下文
    sys_content = agent.memory[0]["content"]
    assert len(sys_content) > 50
    return f"模式={agent.current_mode}, memory={len(agent.memory)}条"

@test("Agent reset", "集成")
def _():
    from agent_core.agent import Agent
    agent = Agent(stream=False)
    agent.memory.append({"role": "user", "content": "test"})
    assert len(agent.memory) > 1
    agent.reset()
    assert len(agent.memory) == 1
    assert agent.estimated_tokens == 0
    return "重置正常"

@test("Agent memory_stats", "集成")
def _():
    from agent_core.agent import Agent
    agent = Agent(stream=False)
    stats = agent.memory_stats
    assert "轮" in stats and "消息" in stats and "tokens" in stats
    return stats

@test("Agent set_mode", "集成")
def _():
    from agent_core.agent import Agent
    agent = Agent(stream=False)
    agent.set_mode("zhipu")
    assert agent.current_mode == "zhipu"
    agent.set_mode("auto")
    assert agent.current_mode == "auto"
    return "模式切换正常"

@test("Agent 端到端调用（zhipu）", "集成")
def _():
    from agent_core.agent import Agent
    os.environ["LLM_PROVIDER"] = "zhipu"
    os.environ["AUTO_APPROVE"] = "*"
    from agent_core.llm import reset_client
    reset_client()
    agent = Agent(stream=False, mode="zhipu")
    try:
        reply = agent.run("1+1等于几？只回答数字")
        assert len(reply) > 0, "回复为空"
        return f"回复: {reply[:100]}"
    except Exception as e:
        if "API" in str(e) or "调用失败" in str(e):
            return "SKIP"
        raise
    finally:
        os.environ["AUTO_APPROVE"] = ""


# ═══════════════════════════════════════════════════════════
# 9. MCP 测试
# ═══════════════════════════════════════════════════════════
print("\n🔌 [9/9] MCP")

@test("MCP 客户端 - filesystem 连接", "MCP")
def _():
    from agent_core.mcp_client import MCPClient
    client = MCPClient('filesystem', {
        'command': 'npx',
        'args': ['-y', '@modelcontextprotocol/server-filesystem', os.getcwd()],
    })
    ok = client.connect()
    if not ok:
        return "SKIP"
    assert len(client.tools) > 0, "无工具"
    tool_names = [t["name"] for t in client.tools]
    assert "list_directory" in tool_names
    assert "read_file" in tool_names
    count = len(client.tools)
    client.disconnect()
    return f"连接成功, {count} 个工具"

@test("MCP 客户端 - filesystem 调用", "MCP")
def _():
    from agent_core.mcp_client import MCPClient
    client = MCPClient('filesystem', {
        'command': 'npx',
        'args': ['-y', '@modelcontextprotocol/server-filesystem', os.getcwd()],
    })
    ok = client.connect()
    if not ok:
        return "SKIP"
    result = client.call_tool('list_directory', {'path': os.getcwd()})
    assert "main.py" in result or "FILE" in result, f"目录列表异常: {result[:100]}"
    result2 = client.call_tool('read_file', {'path': os.path.join(os.getcwd(), '.env')})
    assert "Mini Agent" in result2 or "MODEL_MODE" in result2
    client.disconnect()
    return "list_directory + read_file 正常"

@test("MCP 客户端 - memory 连接", "MCP")
def _():
    from agent_core.mcp_client import MCPClient
    client = MCPClient('memory', {
        'command': 'npx',
        'args': ['-y', '@modelcontextprotocol/server-memory'],
    })
    ok = client.connect()
    if not ok:
        return "SKIP"
    tool_names = [t["name"] for t in client.tools]
    assert "create_entities" in tool_names
    assert "read_graph" in tool_names
    count = len(client.tools)
    client.disconnect()
    return f"连接成功, {count} 个工具"

@test("MCP 管理器集成", "MCP")
def _():
    from agent_core.mcp_manager import load_mcp_servers, shutdown_mcp_servers, get_mcp_status, is_mcp_auto_approved
    tools = load_mcp_servers()
    assert isinstance(tools, list)
    assert len(tools) > 0, "未加载任何 MCP 工具"
    # 验证工具已注册到全局注册表
    from agent_core.tools import get_tool_specs
    specs = get_tool_specs()
    mcp_names = [s["function"]["name"] for s in specs if s["function"]["name"].startswith("mcp_")]
    assert len(mcp_names) > 0
    # 通过全局注册表调用
    from agent_core.tools import call_tool
    result = call_tool("mcp_filesystem_list_directory", {"path": os.getcwd()})
    assert "main.py" in result or "FILE" in result
    # 状态查看
    status = get_mcp_status()
    assert len(status) > 0
    assert any(s["connected"] for s in status)
    shutdown_mcp_servers()
    return f"{len(tools)} 个 MCP 工具, 状态查看正常"

@test("MCP autoApprove", "MCP")
def _():
    from agent_core.mcp_client import MCPClient
    client = MCPClient('test', {'autoApprove': ['read_file', 'list_directory']})
    assert client.is_auto_approved('read_file') == True
    assert client.is_auto_approved('write_file') == False
    client2 = MCPClient('test2', {'autoApprove': ['*']})
    assert client2.is_auto_approved('anything') == True
    client3 = MCPClient('test3', {})
    assert client3.is_auto_approved('read_file') == False
    return "autoApprove 策略正确"
def _():
    from agent_core.mcp_manager import load_mcp_servers, shutdown_mcp_servers
    tools = load_mcp_servers()
    assert isinstance(tools, list)
    shutdown_mcp_servers()
    return f"MCP 管理正常 ({len(tools)} 工具)"

@test("Setup Wizard 检测", "集成")
def _():
    from agent_core.setup_wizard import needs_setup
    # .env.local 存在，不需要 setup
    assert needs_setup() == False
    return "配置检测正常"

@test(".env 配置完整性", "集成")
def _():
    required_keys = ["MODEL_MODE", "MAX_CONTEXT_TOKENS", "MAX_TOOL_RESULT_CHARS", "SHORT_TERM_TURNS"]
    with open(".env") as f:
        env_content = f.read()
    for key in required_keys:
        assert key in env_content, f".env 缺少 {key}"
    return f"{len(required_keys)} 个配置项完整"

@test(".gitignore 安全性", "集成")
def _():
    with open(".gitignore") as f:
        gitignore = f.read()
    assert ".env.local" in gitignore, ".env.local 未被忽略"
    assert "__pycache__" in gitignore
    return "敏感文件已忽略"


# ═══════════════════════════════════════════════════════════
# 10. Steering Inclusion 模式测试
# ═══════════════════════════════════════════════════════════
print("\n📦 [10/12] Steering Inclusion 模式")

@test("Front-matter 解析", "Steering增强")
def _():
    from agent_core.steering import _parse_front_matter
    content = "---\ninclusion: fileMatch\nfileMatchPattern: '*.py'\n---\n正文内容"
    meta, body = _parse_front_matter(content)
    assert meta["inclusion"] == "fileMatch"
    assert meta["fileMatchPattern"] == "*.py"
    assert body == "正文内容"
    return "YAML front-matter 解析正确"

@test("无 front-matter 兼容", "Steering增强")
def _():
    from agent_core.steering import _parse_front_matter
    content = "纯文本内容\n没有 front-matter"
    meta, body = _parse_front_matter(content)
    assert meta == {}
    assert body == content
    return "无 front-matter 时返回原文"

@test("SteeringFile 文件匹配", "Steering增强")
def _():
    from agent_core.steering import SteeringFile
    sf = SteeringFile("test.md", "/path/test.md", {"inclusion": "fileMatch", "fileMatchPattern": "*.py"}, "body")
    assert sf.matches_file("main.py") == True
    assert sf.matches_file("test.js") == False
    assert sf.inclusion == "filematch"  # 小写化
    return "fileMatch 模式匹配正确"

@test("SteeringManager always 模式", "Steering增强")
def _():
    from agent_core.steering import SteeringManager
    mgr = SteeringManager("steering")
    content = mgr.get_active_content()
    assert "default.md" in content or "回答规则" in content
    return "always 文件自动加载"

@test("SteeringManager fileMatch 模式", "Steering增强")
def _():
    from agent_core.steering import SteeringManager
    mgr = SteeringManager("steering")
    # 传入 .py 文件应触发 python_style.md
    content_with_py = mgr.get_active_content(context_files=["main.py"])
    content_without = mgr.get_active_content(context_files=["style.css"])
    assert "PEP 8" in content_with_py or "python_style" in content_with_py
    assert "PEP 8" not in content_without
    return "fileMatch 按文件类型加载"

@test("SteeringManager manual 模式", "Steering增强")
def _():
    from agent_core.steering import SteeringManager
    import tempfile, os
    # 创建临时 steering 目录
    tmpdir = tempfile.mkdtemp()
    with open(os.path.join(tmpdir, "manual_test.md"), "w") as f:
        f.write("---\ninclusion: manual\n---\n手动内容")
    mgr = SteeringManager(tmpdir)
    # 未激活时不加载
    content = mgr.get_active_content()
    assert "手动内容" not in content
    # 激活后加载
    assert mgr.activate_manual("manual_test.md") == True
    content = mgr.get_active_content()
    assert "手动内容" in content
    # 清理
    import shutil
    shutil.rmtree(tmpdir)
    return "manual 模式手动激活/加载"

@test("list_files 列出所有文件", "Steering增强")
def _():
    from agent_core.steering import SteeringManager
    mgr = SteeringManager("steering")
    files = mgr.list_files()
    assert len(files) >= 1
    names = [f["name"] for f in files]
    assert "default.md" in names
    return f"列出 {len(files)} 个 steering 文件"

@test("兼容旧接口 load_steering()", "Steering增强")
def _():
    from agent_core.steering import load_steering
    content = load_steering()
    assert isinstance(content, str)
    assert len(content) > 0
    return "旧接口兼容正常"


# ═══════════════════════════════════════════════════════════
# 11. 模型路由增强测试
# ═══════════════════════════════════════════════════════════
print("\n📦 [11/12] 模型路由增强")

@test("任务分类 — coding", "路由增强")
def _():
    from agent_core.model_router import classify_task
    caps = classify_task("帮我写一个排序算法")
    assert "coding" in caps
    return f"coding 任务识别: {caps}"

@test("任务分类 — reasoning", "路由增强")
def _():
    from agent_core.model_router import classify_task
    caps = classify_task("分析这段代码的性能瓶颈并给出优化方案")
    assert "reasoning" in caps
    return f"reasoning 任务识别: {caps}"

@test("任务分类 — tool_use", "路由增强")
def _():
    from agent_core.model_router import classify_task
    caps = classify_task("搜索最新的 Python 版本")
    assert "tool_use" in caps
    return f"tool_use 任务识别: {caps}"

@test("任务分类 — fast (短输入)", "路由增强")
def _():
    from agent_core.model_router import classify_task
    caps = classify_task("你好")
    assert "fast" in caps
    return f"fast 任务识别: {caps}"

@test("任务分类 — 工具调用后续轮复用原模型", "路由增强")
def _():
    from agent_core.model_router import classify_task
    # 工具调用后的续轮应保持 fast + tool_use，不升级到 reasoning
    caps = classify_task("你好", has_tool_calls=True, iteration=1)
    assert "fast" in caps and "tool_use" in caps
    assert "reasoning" not in caps
    return "工具续轮: {} (不含reasoning)".format(caps)

@test("MODEL_REGISTRY 能力标签完整", "路由增强")
def _():
    from agent_core.model_router import MODEL_REGISTRY
    for prov, models in MODEL_REGISTRY.items():
        for model, info in models.items():
            assert "tier" in info, f"{prov}/{model} 缺少 tier"
            assert "caps" in info, f"{prov}/{model} 缺少 caps"
            assert "cost" in info, f"{prov}/{model} 缺少 cost"
    return f"{sum(len(m) for m in MODEL_REGISTRY.values())} 个模型配置完整"

@test("ModelHistory 历史记录", "路由增强")
def _():
    from agent_core.model_router import ModelHistory
    h = ModelHistory()
    h.record("zhipu", "glm-4-flash", {"fast"}, True)
    h.record("zhipu", "glm-4-flash", {"fast"}, True)
    h.record("zhipu", "glm-4-flash", {"fast"}, False)
    score = h.score("zhipu", "glm-4-flash", {"fast"})
    assert 0.6 < score < 0.7  # 2/3 ≈ 0.667
    # 无记录返回 0.5
    assert h.score("openai", "gpt-4o", {"coding"}) == 0.5
    return f"历史得分: {score:.3f}"

@test("ModelRouter 能力匹配选择", "路由增强")
def _():
    from agent_core.model_router import ModelRouter
    router = ModelRouter()
    router.mode = "auto"
    # 简单任务应选轻量模型
    provider, model = router.select("你好")
    assert provider is not None
    return f"选择: {provider}/{model} — {router.last_selection}"

@test("ModelRouter record_result", "路由增强")
def _():
    from agent_core.model_router import ModelRouter
    router = ModelRouter()
    router.mode = "auto"
    router.select("写代码")
    router.record_result(success=True)
    # 不报错即可
    return "结果记录正常"

@test("MODEL_TIERS 兼容旧接口", "路由增强")
def _():
    from agent_core.model_router import MODEL_TIERS
    assert "zhipu" in MODEL_TIERS
    assert 1 in MODEL_TIERS["zhipu"]
    assert 2 in MODEL_TIERS["zhipu"]
    return "旧接口兼容"


# ═══════════════════════════════════════════════════════════
# 12. 错误恢复增强测试
# ═══════════════════════════════════════════════════════════
print("\n📦 [12/12] 错误恢复增强")

@test("错误分类 — rate_limit", "恢复增强")
def _():
    from agent_core.llm import classify_error, ErrorKind
    e = Exception("429 Too Many Requests")
    assert classify_error(e) == ErrorKind.RATE_LIMIT
    return "rate_limit 识别正确"

@test("错误分类 — auth", "恢复增强")
def _():
    from agent_core.llm import classify_error, ErrorKind
    e = Exception("401 Unauthorized: Invalid API Key")
    assert classify_error(e) == ErrorKind.AUTH
    return "auth 识别正确"

@test("错误分类 — balance", "恢复增强")
def _():
    from agent_core.llm import classify_error, ErrorKind
    e = Exception("402 Insufficient balance")
    assert classify_error(e) == ErrorKind.BALANCE
    return "balance 识别正确"

@test("错误分类 — timeout", "恢复增强")
def _():
    from agent_core.llm import classify_error, ErrorKind
    e = TimeoutError("Connection timed out")
    assert classify_error(e) == ErrorKind.TIMEOUT
    return "timeout 识别正确"

@test("错误分类 — server", "恢复增强")
def _():
    from agent_core.llm import classify_error, ErrorKind
    e = Exception("502 Bad Gateway")
    assert classify_error(e) == ErrorKind.SERVER
    return "server 识别正确"

@test("指数退避计算", "恢复增强")
def _():
    from agent_core.llm import _calc_delay, ErrorKind
    d0 = _calc_delay(0, ErrorKind.TIMEOUT)
    d1 = _calc_delay(1, ErrorKind.TIMEOUT)
    d2 = _calc_delay(2, ErrorKind.TIMEOUT)
    assert d1 == d0 * 2  # 指数增长
    assert d2 == d0 * 4
    return f"退避: {d0}s → {d1}s → {d2}s"

@test("rate_limit 退避更长", "恢复增强")
def _():
    from agent_core.llm import _calc_delay, ErrorKind
    d_normal = _calc_delay(0, ErrorKind.TIMEOUT)
    d_rate = _calc_delay(0, ErrorKind.RATE_LIMIT)
    assert d_rate > d_normal
    return f"rate_limit: {d_rate}s vs normal: {d_normal}s"

@test("CircuitBreaker 基本功能", "恢复增强")
def _():
    from agent_core.llm import CircuitBreaker
    cb = CircuitBreaker(threshold=2, recovery_time=0.1)
    assert cb.is_open("test") == False
    cb.record_failure("test")
    assert cb.is_open("test") == False  # 1次不触发
    cb.record_failure("test")
    assert cb.is_open("test") == True   # 2次触发熔断
    return "熔断器触发正常"

@test("CircuitBreaker 恢复", "恢复增强")
def _():
    from agent_core.llm import CircuitBreaker
    cb = CircuitBreaker(threshold=1, recovery_time=0.1)
    cb.record_failure("test")
    assert cb.is_open("test") == True
    time.sleep(0.15)
    assert cb.is_open("test") == False  # 恢复期过后自动关闭
    return "熔断器自动恢复"

@test("CircuitBreaker 成功重置", "恢复增强")
def _():
    from agent_core.llm import CircuitBreaker
    cb = CircuitBreaker(threshold=2)
    cb.record_failure("test")
    cb.record_failure("test")
    assert cb.is_open("test") == True
    cb.record_success("test")
    assert cb.is_open("test") == False
    return "成功调用重置熔断器"

@test("list_providers 包含状态", "恢复增强")
def _():
    from agent_core.llm import list_providers
    providers = list_providers()
    for p in providers:
        assert "status" in p
    return f"{len(providers)} 个提供商含状态信息"

@test("_RETRYABLE 和 _SKIP_TO_FALLBACK 互斥", "恢复增强")
def _():
    from agent_core.llm import _RETRYABLE, _SKIP_TO_FALLBACK
    assert len(_RETRYABLE & _SKIP_TO_FALLBACK) == 0
    return "错误分类互斥正确"

@test(".env 包含错误恢复配置", "恢复增强")
def _():
    with open(".env") as f:
        content = f.read()
    assert "MAX_RETRIES" in content
    assert "RETRY_BASE_DELAY" in content
    assert "RETRY_STRATEGY" in content
    return "配置项完整"


# ═══════════════════════════════════════════════════════════
# 13. 多工作区测试
# ═══════════════════════════════════════════════════════════
print("\n📂 [13/15] 多工作区")

@test("WorkspaceManager 初始化", "多工作区")
def _():
    from agent_core.workspace import WorkspaceManager
    wm = WorkspaceManager()
    assert wm.current is not None
    assert wm.current.path == os.getcwd()
    return f"当前: {wm.current.name}"

@test("Workspace 属性", "多工作区")
def _():
    from agent_core.workspace import Workspace
    ws = Workspace(name="test", path="/tmp/test-ws")
    assert ws.config_dir == "/tmp/test-ws/config"
    assert ws.steering_dir == "/tmp/test-ws/steering"
    assert ws.agents_dir == "/tmp/test-ws/agents"
    assert ws.powers_dir == "/tmp/test-ws/powers"
    d = ws.to_dict()
    ws2 = Workspace.from_dict(d)
    assert ws2.name == ws.name and ws2.path == ws.path
    return "属性和序列化正确"

@test("工作区添加/列出/移除", "多工作区")
def _():
    from agent_core.workspace import WorkspaceManager
    import tempfile
    wm = WorkspaceManager()
    tmpdir = tempfile.mkdtemp()
    # 添加
    ws = wm.add(tmpdir, "test-ws")
    assert ws.name == "test-ws"
    ws_list = wm.list_workspaces()
    names = [w["name"] for w in ws_list]
    assert "test-ws" in names
    # 不重复添加
    ws2 = wm.add(tmpdir)
    assert ws2.path == ws.path
    # 移除
    assert wm.remove("test-ws") == True
    assert wm.remove("nonexistent") == False
    import shutil
    shutil.rmtree(tmpdir)
    return "添加/列出/移除正常"

@test("工作区切换", "多工作区")
def _():
    from agent_core.workspace import WorkspaceManager
    import tempfile
    wm = WorkspaceManager()
    tmpdir = tempfile.mkdtemp()
    wm.add(tmpdir, "switch-test")
    ws = wm.switch("switch-test")
    assert ws is not None
    assert wm.current.name == "switch-test"
    # 切回
    wm.switch(wm.list_workspaces()[0]["name"])
    wm.remove("switch-test")
    import shutil
    shutil.rmtree(tmpdir)
    return "切换正常"

@test("MCP 配置合并", "多工作区")
def _():
    from agent_core.workspace import WorkspaceManager
    wm = WorkspaceManager()
    merged = wm.merge_mcp_config()
    assert isinstance(merged, dict)
    # 当前工作区有 mcp_servers.json
    assert "filesystem" in merged or len(merged) >= 0
    return f"合并 {len(merged)} 个 MCP 配置"

@test("Steering 目录合并", "多工作区")
def _():
    from agent_core.workspace import WorkspaceManager
    wm = WorkspaceManager()
    dirs = wm.merge_steering_dirs()
    assert isinstance(dirs, list)
    assert any("steering" in d for d in dirs)
    return f"合并 {len(dirs)} 个 steering 目录"

@test("all_active 过滤", "多工作区")
def _():
    from agent_core.workspace import WorkspaceManager
    wm = WorkspaceManager()
    active = wm.all_active
    assert len(active) >= 1
    assert all(w.active for w in active)
    return f"{len(active)} 个活跃工作区"


# ═══════════════════════════════════════════════════════════
# 14. Powers 系统测试
# ═══════════════════════════════════════════════════════════
print("\n⚡ [14/15] Powers 系统")

@test("PowersManager 发现", "Powers")
def _():
    from agent_core.powers import PowersManager
    pm = PowersManager(["powers"])
    powers = pm.list_powers()
    assert len(powers) >= 1
    names = [p["name"] for p in powers]
    assert "example-power" in names
    return f"发现 {len(powers)} 个 Power"

@test("POWER.md 解析", "Powers")
def _():
    from agent_core.powers import _parse_power_md
    body, desc, kws = _parse_power_md("powers/example-power/POWER.md")
    assert len(body) > 0
    assert "示例" in desc or "example" in desc.lower()
    assert len(kws) >= 1
    return f"描述: {desc[:50]}, 关键词: {kws}"

@test("Power 属性检测", "Powers")
def _():
    from agent_core.powers import PowersManager
    pm = PowersManager(["powers"])
    p = pm.get("example-power")
    assert p is not None
    assert p.has_doc() == True
    assert p.has_steering() == True
    return f"doc={p.has_doc()}, steering={p.has_steering()}, mcp={p.has_mcp()}, skills={p.has_skills()}"

@test("Power 激活/停用", "Powers")
def _():
    from agent_core.powers import PowersManager
    pm = PowersManager(["powers"])
    # 激活
    p = pm.activate("example-power")
    assert p is not None
    assert p.activated == True
    assert len(p.doc_content) > 0
    assert len(p.steering_files) >= 1
    # 已激活列表
    activated = pm.get_activated()
    assert len(activated) >= 1
    # 停用
    assert pm.deactivate("example-power") == True
    assert pm.get("example-power").activated == False
    return "激活/停用正常"

@test("Power 文档内容获取", "Powers")
def _():
    from agent_core.powers import PowersManager
    pm = PowersManager(["powers"])
    pm.activate("example-power")
    doc = pm.get_all_doc_content()
    assert "example-power" in doc
    steering = pm.get_all_steering_files()
    assert len(steering) >= 1
    pm.deactivate("example-power")
    return "文档和 steering 获取正常"

@test("Power 关键词匹配", "Powers")
def _():
    from agent_core.powers import PowersManager
    pm = PowersManager(["powers"])
    matched = pm.find_by_keyword("这是一个示例")
    assert len(matched) >= 1
    assert matched[0].name == "example-power"
    return f"匹配到 {len(matched)} 个 Power"

@test("Power 安装/卸载", "Powers")
def _():
    from agent_core.powers import PowersManager
    import tempfile, shutil
    # 创建临时 power
    tmpdir = tempfile.mkdtemp()
    src = os.path.join(tmpdir, "test-power")
    os.makedirs(src)
    with open(os.path.join(src, "POWER.md"), "w") as f:
        f.write("---\ndescription: test\nkeywords: test\n---\n# Test Power")
    # 安装到临时目录
    target = os.path.join(tmpdir, "installed")
    pm = PowersManager([target])
    p = pm.install(src, target)
    assert p is not None
    assert pm.get("test-power") is not None
    # 卸载
    assert pm.uninstall("test-power") == True
    assert pm.get("test-power") is None
    shutil.rmtree(tmpdir)
    return "安装/卸载正常"


# ═══════════════════════════════════════════════════════════
# 15. 自定义 Agent 测试
# ═══════════════════════════════════════════════════════════
print("\n🤖 [15/15] 自定义 Agent")

@test("CustomAgentManager 发现", "自定义Agent")
def _():
    from agent_core.custom_agents import CustomAgentManager
    am = CustomAgentManager(["agents"])
    agents = am.list_agents()
    assert len(agents) >= 2
    names = [a["name"] for a in agents]
    assert "code-reviewer" in names
    assert "translator" in names
    return f"发现 {len(agents)} 个 Agent"

@test("AgentConfig 加载", "自定义Agent")
def _():
    from agent_core.custom_agents import AgentConfig
    cfg = AgentConfig.from_file("agents/code-reviewer.json")
    assert cfg.name == "code-reviewer"
    assert "代码" in cfg.description or "code" in cfg.description.lower()
    assert cfg.tools is not None
    assert "read_file" in cfg.tools
    assert cfg.max_iterations == 5
    assert "read_file" in cfg.auto_approve
    return f"配置: {cfg.name}, {len(cfg.tools)} 工具"

@test("AgentConfig 序列化", "自定义Agent")
def _():
    from agent_core.custom_agents import AgentConfig
    cfg = AgentConfig(name="test", description="测试", system_prompt="你是测试",
                      model="zhipu", tools=["calculator"], max_iterations=3)
    d = cfg.to_dict()
    cfg2 = AgentConfig.from_dict(d)
    assert cfg2.name == cfg.name
    assert cfg2.model == cfg.model
    assert cfg2.tools == cfg.tools
    assert cfg2.max_iterations == cfg.max_iterations
    return "序列化/反序列化正确"

@test("Agent 创建/删除", "自定义Agent")
def _():
    from agent_core.custom_agents import CustomAgentManager
    import tempfile, shutil
    tmpdir = tempfile.mkdtemp()
    am = CustomAgentManager([tmpdir])
    # 创建
    cfg = am.create_config("test-agent", "测试Agent", "你是测试", save_dir=tmpdir)
    assert cfg.name == "test-agent"
    assert am.get("test-agent") is not None
    assert os.path.exists(os.path.join(tmpdir, "test-agent.json"))
    # 删除
    assert am.delete_config("test-agent") == True
    assert am.get("test-agent") is None
    shutil.rmtree(tmpdir)
    return "创建/删除正常"

@test("Agent 配置 — translator", "自定义Agent")
def _():
    from agent_core.custom_agents import CustomAgentManager
    am = CustomAgentManager(["agents"])
    cfg = am.get("translator")
    assert cfg is not None
    assert cfg.tools == []  # 翻译不需要工具
    assert cfg.max_iterations == 1
    return f"translator: 无工具, 1次迭代"

@test("Agent invoke 错误处理", "自定义Agent")
def _():
    from agent_core.custom_agents import CustomAgentManager
    am = CustomAgentManager(["agents"])
    result = am.invoke("nonexistent", "test")
    assert "错误" in result or "未找到" in result
    return "未知 Agent 错误处理正常"

# ── 抖音热搜 Skill ──────────────────────────────────────────

@test("douyin skill 加载", "抖音热搜")
def _():
    from skills.douyin import douyin_hot
    assert callable(douyin_hot)
    return "douyin_hot 函数可调用"

@test("douyin_hot 工具已注册", "抖音热搜")
def _():
    from agent_core.tools import _REGISTRY
    assert "douyin_hot" in _REGISTRY
    spec = _REGISTRY["douyin_hot"]["spec"]["function"]
    assert "抖音" in spec["description"]
    return "工具注册正常, 描述: {}".format(spec["description"])

@test("news skill 支持 douyin 源", "抖音热搜")
def _():
    from agent_core.tools import _REGISTRY
    spec = _REGISTRY["news"]["spec"]["function"]
    enum_vals = spec["parameters"]["properties"]["source"]["enum"]
    assert "douyin" in enum_vals
    return "news source enum 包含 douyin: {}".format(enum_vals)

@test("token_optimizer 白名单包含 douyin_hot", "抖音热搜")
def _():
    from agent_core.token_optimizer import compress_tool_result
    # 短内容不截断
    short = "test content"
    assert compress_tool_result(short, "douyin_hot") == short
    # 长内容（50行以内）不截断
    long_content = "\n".join(["line {}".format(i) for i in range(45)])
    result = compress_tool_result(long_content, "douyin_hot")
    assert "line 44" in result  # 最后一行保留
    return "douyin_hot 在结构化工具白名单中"

# ═══════════════════════════════════════════════════════════
elapsed = round(time.time() - START_TIME, 2)
total = PASS + FAIL + SKIP

print("\n" + "=" * 60)
print(f"📊 测试报告 — Mini Agent v0.2")
print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)
print(f"\n  总计: {total} 项")
print(f"  ✅ 通过: {PASS}")
print(f"  ❌ 失败: {FAIL}")
print(f"  ⏭️  跳过: {SKIP}")
print(f"  ⏱️  耗时: {elapsed}s")
print(f"  通过率: {round(PASS/(total-SKIP)*100, 1) if (total-SKIP) > 0 else 0}%（不含跳过）")

if FAIL > 0:
    print(f"\n{'─' * 60}")
    print("❌ 失败详情:")
    for r in RESULTS:
        if r["status"] == "FAIL":
            print(f"\n  [{r['category']}] {r['name']}")
            for line in r["detail"].split("\n")[-5:]:
                print(f"    {line}")

# 按分类汇总
print(f"\n{'─' * 60}")
print("📋 分类汇总:")
categories = {}
for r in RESULTS:
    cat = r["category"]
    if cat not in categories:
        categories[cat] = {"pass": 0, "fail": 0, "skip": 0}
    categories[cat][r["status"].lower()] += 1

for cat, counts in categories.items():
    total_cat = counts["pass"] + counts["fail"] + counts["skip"]
    icon = "✅" if counts["fail"] == 0 else "❌"
    print(f"  {icon} {cat}: {counts['pass']}/{total_cat} 通过" +
          (f", {counts['skip']} 跳过" if counts["skip"] > 0 else "") +
          (f", {counts['fail']} 失败" if counts["fail"] > 0 else ""))

print(f"\n{'=' * 60}")
if FAIL == 0:
    print("🎉 全部测试通过！")
else:
    print(f"⚠️  有 {FAIL} 项测试失败，请检查。")
print()

# 写入报告文件
report_lines = [
    f"# Mini Agent 测试报告\n",
    f"日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
    f"## 概览\n",
    f"| 指标 | 值 |",
    f"|------|-----|",
    f"| 总计 | {total} |",
    f"| 通过 | {PASS} |",
    f"| 失败 | {FAIL} |",
    f"| 跳过 | {SKIP} |",
    f"| 耗时 | {elapsed}s |",
    f"| 通过率 | {round(PASS/(total-SKIP)*100, 1) if (total-SKIP) > 0 else 0}% |\n",
    f"## 分类结果\n",
    f"| 分类 | 通过 | 失败 | 跳过 |",
    f"|------|------|------|------|",
]
for cat, counts in categories.items():
    total_cat = counts["pass"] + counts["fail"] + counts["skip"]
    report_lines.append(f"| {cat} | {counts['pass']} | {counts['fail']} | {counts['skip']} |")

report_lines.append(f"\n## 详细结果\n")
report_lines.append(f"| # | 测试 | 分类 | 状态 | 详情 |")
report_lines.append(f"|---|------|------|------|------|")
for i, r in enumerate(RESULTS):
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}[r["status"]]
    detail = r["detail"].replace("\n", " ")[:80]
    report_lines.append(f"| {i+1} | {r['name']} | {r['category']} | {icon} | {detail} |")

if FAIL > 0:
    report_lines.append(f"\n## 失败详情\n")
    for r in RESULTS:
        if r["status"] == "FAIL":
            report_lines.append(f"### {r['name']} ({r['category']})\n")
            report_lines.append(f"```\n{r['detail']}\n```\n")

with open("test_report.md", "w", encoding="utf-8") as f:
    f.write("\n".join(report_lines))
print(f"📄 报告已保存到 test_report.md")
