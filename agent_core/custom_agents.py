"""自定义 Agent 创建 

通过 JSON 配置文件定义专用 Agent，每个自定义 Agent 可以指定：
- system_prompt  — 专用系统提示词
- model          — 指定模型/提供商
- tools          — 限定可用工具列表
- steering       — 额外 steering 文件
- max_iterations — 最大迭代次数
- auto_approve   — 自动放行的工具

目录结构:
  agents/
    code-reviewer.json
    translator.json

配置示例:
{
  "name": "code-reviewer",
  "description": "代码审查专家",
  "system_prompt": "你是资深代码审查专家，关注代码质量、安全性和性能。",
  "model": "zhipu",
  "tools": ["read_file", "list_dir", "shell"],
  "max_iterations": 5,
  "auto_approve": ["read_file", "list_dir"]
}

- 创建专用子 Agent
- 每个 Agent 有独立 system prompt 和工具集
- 通过 invokeSubAgent 调用
"""
from __future__ import annotations
import json, os, glob
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentConfig:
    """自定义 Agent 配置"""
    name: str
    description: str = ""
    system_prompt: str = ""
    model: str | None = None       # 提供商名称，None 表示跟随主 Agent
    tools: list[str] | None = None  # 限定工具列表，None 表示全部
    steering: list[str] = field(default_factory=list)  # 额外 steering 文件
    max_iterations: int = 10
    auto_approve: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "max_iterations": self.max_iterations,
        }
        if self.model:
            d["model"] = self.model
        if self.tools is not None:
            d["tools"] = self.tools
        if self.steering:
            d["steering"] = self.steering
        if self.auto_approve:
            d["auto_approve"] = self.auto_approve
        return d

    @staticmethod
    def from_dict(d: dict) -> "AgentConfig":
        return AgentConfig(
            name=d.get("name", "unnamed"),
            description=d.get("description", ""),
            system_prompt=d.get("system_prompt", ""),
            model=d.get("model"),
            tools=d.get("tools"),
            steering=d.get("steering", []),
            max_iterations=d.get("max_iterations", 10),
            auto_approve=d.get("auto_approve", []),
        )

    @staticmethod
    def from_file(path: str) -> "AgentConfig":
        with open(path, "r", encoding="utf-8") as f:
            return AgentConfig.from_dict(json.load(f))


class CustomAgentManager:
    """自定义 Agent 管理器 — 发现、加载、创建、调用"""

    def __init__(self, agents_dirs: list[str] | None = None):
        self._agents_dirs = agents_dirs or self._default_dirs()
        self._configs: dict[str, AgentConfig] = {}
        self._discover()

    @staticmethod
    def _default_dirs() -> list[str]:
        dirs = []
        global_dir = os.path.expanduser("~/.nexus-agent/agents")
        if os.path.isdir(global_dir):
            dirs.append(global_dir)
        local_dir = "agents"
        if os.path.isdir(local_dir):
            dirs.append(os.path.abspath(local_dir))
        return dirs

    def _discover(self):
        """扫描所有 agents 目录，加载配置"""
        self._configs.clear()
        for adir in self._agents_dirs:
            if not os.path.isdir(adir):
                continue
            for path in sorted(glob.glob(os.path.join(adir, "*.json"))):
                try:
                    cfg = AgentConfig.from_file(path)
                    self._configs[cfg.name] = cfg
                except Exception as e:
                    print("  ⚠️  Agent 配置加载失败 [{}]: {}".format(path, e))

    def reload(self):
        self._discover()

    # ── 查询 ─────────────────────────────────────────────────

    def list_agents(self) -> list[dict]:
        return [cfg.to_dict() for cfg in self._configs.values()]

    def get(self, name: str) -> AgentConfig | None:
        return self._configs.get(name)

    # ── 创建与保存 ───────────────────────────────────────────

    def create_config(self, name: str, description: str = "",
                      system_prompt: str = "", model: str | None = None,
                      tools: list[str] | None = None,
                      save_dir: str | None = None) -> AgentConfig:
        """创建新的 Agent 配置并保存"""
        cfg = AgentConfig(
            name=name,
            description=description,
            system_prompt=system_prompt,
            model=model,
            tools=tools,
        )
        # 保存到文件
        target_dir = save_dir or (self._agents_dirs[-1] if self._agents_dirs
                                  else os.path.abspath("agents"))
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, "{}.json".format(name))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=2)

        self._configs[name] = cfg
        return cfg

    def delete_config(self, name: str) -> bool:
        """删除 Agent 配置"""
        cfg = self._configs.get(name)
        if not cfg:
            return False
        # 查找并删除文件
        for adir in self._agents_dirs:
            path = os.path.join(adir, "{}.json".format(name))
            if os.path.exists(path):
                os.remove(path)
        del self._configs[name]
        return True

    # ── 调用 ─────────────────────────────────────────────────

    def invoke(self, name: str, task: str, parent_agent=None) -> str:
        """调用自定义 Agent 执行任务（不修改全局状态，线程安全）"""
        cfg = self._configs.get(name)
        if not cfg:
            return "[错误] 未找到 Agent: {}".format(name)

        prompt = cfg.system_prompt or "你是 {} — {}".format(cfg.name, cfg.description)

        # 加载额外 steering
        if cfg.steering:
            steering_parts = []
            for sf in cfg.steering:
                if os.path.exists(sf):
                    try:
                        with open(sf, "r", encoding="utf-8") as f:
                            steering_parts.append(f.read().strip())
                    except Exception:
                        pass
            if steering_parts:
                prompt += "\n\n" + "\n\n".join(steering_parts)

        # auto_approve: 通过实例级配置传递，不再修改 os.environ（线程安全）
        if parent_agent:
            result = parent_agent.spawn(
                task,
                system_prompt=prompt,
                tools_filter=cfg.tools,
                timeout=cfg.max_iterations * 30,  # 粗略超时
            )
        else:
            from agent_core.agent import Agent
            agent = Agent(
                system_prompt=prompt,
                stream=False,
                provider=cfg.model or None,
                tools_filter=cfg.tools,
            )
            agent.max_iterations = cfg.max_iterations
            # 将 auto_approve 注入到 agent 实例（不污染全局环境变量）
            if cfg.auto_approve:
                agent._auto_approve_tools = set(cfg.auto_approve)
            result = agent.run(task)

        return result

    def invoke_async(self, name: str, task: str, parent_agent=None,
                     callback=None):
        """异步调用自定义 Agent（返回 SubAgentFuture）"""
        if not parent_agent:
            return self.invoke(name, task)  # 无父 Agent 时退化为同步

        cfg = self._configs.get(name)
        if not cfg:
            return "[错误] 未找到 Agent: {}".format(name)

        prompt = cfg.system_prompt or "你是 {} — {}".format(cfg.name, cfg.description)
        return parent_agent.spawn_async(
            task, system_prompt=prompt,
            tools_filter=cfg.tools,
            timeout=cfg.max_iterations * 30,
            callback=callback,
        )


# ── 全局单例 ────────────────────────────────────────────────

_manager: CustomAgentManager | None = None


def get_agent_manager(agents_dirs: list[str] | None = None) -> CustomAgentManager:
    global _manager
    if _manager is None:
        _manager = CustomAgentManager(agents_dirs)
    return _manager
