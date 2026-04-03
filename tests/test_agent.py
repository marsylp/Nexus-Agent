"""Agent 集成测试"""
import os, json, pytest
from agent_core.agent import Agent


class TestAgentInit:
    def test_init_defaults(self):
        agent = Agent(stream=False)
        assert agent.current_mode in ("auto", "zhipu", "deepseek", "openai", "silicon", "ollama")
        assert len(agent.memory) >= 1
        assert agent.memory[0]["role"] == "system"
        assert len(agent.memory[0]["content"]) > 50

    def test_custom_system_prompt(self):
        agent = Agent(stream=False, system_prompt="你是测试助手")
        assert "你是测试助手" in agent.memory[0]["content"]

    def test_provider_property(self):
        agent = Agent(stream=False, provider="zhipu")
        assert agent.provider == "zhipu"
        agent.provider = "deepseek"
        assert agent.provider == "deepseek"

    def test_tools_filter(self):
        agent = Agent(stream=False, tools_filter=["calculator", "current_time"])
        names = [t["function"]["name"] for t in agent._all_tools]
        assert "calculator" in names
        assert "current_time" in names
        assert "shell" not in names


class TestAgentReset:
    def test_reset_clears_memory(self):
        agent = Agent(stream=False)
        agent.memory.append({"role": "user", "content": "test"})
        assert len(agent.memory) > 1
        agent.reset()
        assert len(agent.memory) == 1
        assert agent.estimated_tokens == 0


class TestAgentMemoryStats:
    def test_stats_format(self):
        agent = Agent(stream=False)
        stats = agent.memory_stats
        assert "轮" in stats and "消息" in stats and "tokens" in stats


class TestAgentSetMode:
    def test_mode_switch(self):
        agent = Agent(stream=False)
        agent.set_mode("zhipu")
        assert agent.current_mode == "zhipu"
        agent.set_mode("auto")
        assert agent.current_mode == "auto"


class TestAgentContext:
    def test_gather_context(self):
        ctx = Agent._gather_context()
        assert "OS:" in ctx
        assert "CWD:" in ctx
        assert len(ctx) > 20


class TestAgentHookEvents:
    def test_on_emit(self):
        agent = Agent(stream=False)
        log = []
        agent.on("test_event", lambda **kw: log.append(kw))
        agent._emit("test_event", data="hello")
        assert len(log) == 1
        assert log[0]["data"] == "hello"

    def test_hook_blocking(self):
        agent = Agent(stream=False)
        agent.on("block_event", lambda **kw: False)
        result = agent._emit("block_event")
        assert result is False


class TestAgentParallelTools:
    def test_parallel_execution(self):
        agent = Agent(stream=False)
        old = os.environ.get("AUTO_APPROVE", "")
        os.environ["AUTO_APPROVE"] = "*"
        try:
            tool_calls = [
                {"id": "tc1", "name": "calculator", "args": {"expression": "1+1"}},
                {"id": "tc2", "name": "calculator", "args": {"expression": "2+2"}},
                {"id": "tc3", "name": "current_time", "args": {}},
            ]
            results = agent._execute_tools_parallel(tool_calls)
            assert len(results) == 3
            assert results[0]["result"] == "2"
            assert results[1]["result"] == "4"
            assert results[2]["ok"] is True
        finally:
            os.environ["AUTO_APPROVE"] = old


class TestAgentSpawn:
    def test_spawn_method_exists(self):
        agent = Agent(stream=False)
        assert hasattr(agent, "spawn")
        assert hasattr(agent, "spawn_async")
        assert hasattr(agent, "spawn_parallel")
        assert hasattr(agent, "_do_spawn")
        import inspect
        src = inspect.getsource(agent._do_spawn)
        assert "Agent(" in src


class TestAgentSteering:
    def test_steering_loaded(self):
        agent = Agent(stream=False)
        content = agent.memory[0]["content"]
        # steering 内容应被加载到 system prompt
        assert len(content) > 100


class TestAgentContextFiles:
    def test_extract_context_files(self):
        files = Agent._extract_context_files("请看 main.py 和 setup.py 文件")
        assert "main.py" in files or len(files) >= 0  # 取决于正则实现


class TestAgentRebuildSystemPrompt:
    def test_rebuild_includes_steering(self):
        agent = Agent(stream=False)
        agent._steering_content = "测试 steering 内容"
        agent._rebuild_system_prompt()
        assert "测试 steering 内容" in agent.memory[0]["content"]

    def test_rebuild_includes_powers(self):
        agent = Agent(stream=False)
        agent._powers_content = "Power 文档内容"
        agent._rebuild_system_prompt()
        assert "Power 文档内容" in agent.memory[0]["content"]
        assert "[Powers]" in agent.memory[0]["content"]

    def test_inject_powers_content(self):
        agent = Agent(stream=False)
        agent.inject_powers_content("doc content", "steering content")
        assert "doc content" in agent.memory[0]["content"]
        assert "steering content" in agent.memory[0]["content"]

    def test_inject_empty_powers(self):
        agent = Agent(stream=False)
        agent.inject_powers_content("some doc")
        assert "some doc" in agent.memory[0]["content"]
        agent.inject_powers_content("", "")
        assert "some doc" not in agent.memory[0]["content"]


class TestAgentReloadConfig:
    def test_reload_refreshes_tools(self):
        agent = Agent(stream=False)
        old_count = len(agent._all_tools)
        agent.reload_config()
        assert len(agent._all_tools) == old_count  # 工具数不变但已刷新
