"""Hook 事件系统 

支持的 10 种事件类型:
- fileEdited       — 文件保存时触发
- fileCreated      — 文件创建时触发
- fileDeleted      — 文件删除时触发
- userTriggered    — 用户手动触发
- promptSubmit     — 消息发送时触发
- agentStop        — Agent 执行完成时触发
- preToolUse       — 工具执行前触发（可阻止执行）
- postToolUse      — 工具执行后触发
- preTaskExecution — Spec 任务开始前触发
- postTaskExecution— Spec 任务完成后触发

动作类型:
- askAgent   — 发送提示给 Agent
- runCommand — 执行 shell 命令

Hook JSON Schema :
{
  "name": "string",
  "version": "1.0.0",
  "description": "string",
  "when": {
    "type": "事件类型",
    "patterns": ["文件匹配模式"],      // fileEdited/fileCreated/fileDeleted 必填
    "toolTypes": ["read", "write", ...]  // preToolUse/postToolUse 必填
  },
  "then": {
    "type": "askAgent 或 runCommand",
    "prompt": "string",    // askAgent 必填
    "command": "string"    // runCommand 必填
  }
}
"""
from __future__ import annotations
import json, os, subprocess, fnmatch, re
from typing import Any, Callable, Optional
from dataclasses import dataclass, field

# ── 工具分类 ─────────────────────────

TOOL_CATEGORIES: dict[str, list[str]] = {
    "read": ["read_file", "list_dir", "calculator", "current_time",
             "system_info", "get_env", "text_stats", "json_parse",
             "regex_match", "csv_parse", "web_search", "http_get"],
    "write": ["write_file", "shell", "run_python", "http_post"],
    "shell": ["shell"],
    "web": ["web_search", "http_get", "http_post"],
    "spec": [],  # spec 相关工具动态添加
}


def get_tool_category(tool_name: str) -> set[str]:
    """获取工具所属的分类集合"""
    cats = set()
    for cat, tools in TOOL_CATEGORIES.items():
        if tool_name in tools:
            cats.add(cat)
    # MCP 工具默认归为 read（安全侧）
    if tool_name.startswith("mcp_"):
        cats.add("read")
    if not cats:
        cats.add("read")  # 默认 read
    return cats


def _matches_tool_types(tool_name: str, tool_types: list[str]) -> bool:
    """检查工具是否匹配 toolTypes 过滤条件

    支持:
    - 内置分类: read, write, shell, web, spec
    - 通配符: * 匹配所有
    - 正则: .*sql.* 匹配工具名
    """
    if not tool_types:
        return True
    if "*" in tool_types:
        return True

    tool_cats = get_tool_category(tool_name)

    for tt in tool_types:
        # 内置分类匹配
        if tt in TOOL_CATEGORIES and tt in tool_cats:
            return True
        # 正则匹配工具名
        try:
            if re.search(tt, tool_name):
                return True
        except re.error:
            pass
    return False


# ── Hook 数据结构 ─────────────────────────

@dataclass
class HookConfig:
    """单个 Hook 配置"""
    name: str
    version: str = "1.0.0"
    description: str = ""
    disabled: bool = False

    # when
    event_type: str = ""           # 10 种事件类型之一
    file_patterns: list[str] = field(default_factory=list)  # fileEdited/Created/Deleted
    tool_types: list[str] = field(default_factory=list)      # preToolUse/postToolUse

    # then
    action_type: str = ""          # askAgent | runCommand
    prompt: str = ""               # askAgent 的提示
    command: str = ""              # runCommand 的命令
    timeout: int = 60              # runCommand 超时

    @staticmethod
    def from_dict(d: dict) -> "HookConfig":
        when = d.get("when", {})
        then = d.get("then", {})
        return HookConfig(
            name=d.get("name", "unnamed"),
            version=d.get("version", "1.0.0"),
            description=d.get("description", ""),
            disabled=d.get("disabled", False),
            event_type=when.get("type", d.get("event", "")),
            file_patterns=when.get("patterns", []),
            tool_types=when.get("toolTypes", []),
            action_type=then.get("type", d.get("action", "")),
            prompt=then.get("prompt", d.get("message", "")),
            command=then.get("command", d.get("command", "")),
            timeout=d.get("timeout", 60),
        )

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "when": {"type": self.event_type},
            "then": {"type": self.action_type},
        }
        if self.file_patterns:
            d["when"]["patterns"] = self.file_patterns
        if self.tool_types:
            d["when"]["toolTypes"] = self.tool_types
        if self.action_type == "askAgent":
            d["then"]["prompt"] = self.prompt
        elif self.action_type == "runCommand":
            d["then"]["command"] = self.command
        return d


# ── 事件类型常量 ─────────────────────────────────────────────

class EventType:
    FILE_EDITED = "fileEdited"
    FILE_CREATED = "fileCreated"
    FILE_DELETED = "fileDeleted"
    USER_TRIGGERED = "userTriggered"
    PROMPT_SUBMIT = "promptSubmit"
    AGENT_STOP = "agentStop"
    PRE_TOOL_USE = "preToolUse"
    POST_TOOL_USE = "postToolUse"
    PRE_TASK_EXECUTION = "preTaskExecution"
    POST_TASK_EXECUTION = "postTaskExecution"

    ALL = [FILE_EDITED, FILE_CREATED, FILE_DELETED, USER_TRIGGERED,
           PROMPT_SUBMIT, AGENT_STOP, PRE_TOOL_USE, POST_TOOL_USE,
           PRE_TASK_EXECUTION, POST_TASK_EXECUTION]

    # 映射旧事件名到新事件名
    LEGACY_MAP = {
        "before_run": PROMPT_SUBMIT,
        "after_run": AGENT_STOP,
        "before_tool": PRE_TOOL_USE,
        "after_tool": POST_TOOL_USE,
        "before_task": PRE_TASK_EXECUTION,
        "after_task": POST_TASK_EXECUTION,
    }


# ── Hook 管理器 ─────────────────────────────────────────────

class HookManager:
    """Hook 管理器 — 管理所有 Hook 的注册、触发和生命周期"""

    def __init__(self):
        self._hooks: list[HookConfig] = []
        self._callbacks: dict[str, list[Callable]] = {}  # 编程式注册
        self._agent_ref = None  # Agent 引用（用于 askAgent）

    def set_agent(self, agent):
        self._agent_ref = agent

    # ── 配置加载 ─────────────────────────────────────────────

    def load_from_file(self, path: str) -> list[str]:
        """从 JSON 文件加载 Hook 配置"""
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []

        loaded = []
        # 支持新格式（顶层数组或 hooks 字段）和旧格式
        hooks_data = data if isinstance(data, list) else data.get("hooks", [])

        for h in hooks_data:
            cfg = HookConfig.from_dict(h)
            if cfg.disabled or not cfg.event_type or not cfg.action_type:
                continue
            # 旧事件名兼容
            if cfg.event_type in EventType.LEGACY_MAP:
                cfg.event_type = EventType.LEGACY_MAP[cfg.event_type]
            self._hooks.append(cfg)
            loaded.append(cfg.name)

        return loaded

    def load_from_files(self, paths: list[str]) -> list[str]:
        """从多个配置文件加载"""
        all_loaded = []
        for p in paths:
            all_loaded.extend(self.load_from_file(p))
        return all_loaded

    # ── 编程式注册（兼容旧 API）────────────────────────────────

    def on(self, event: str, callback: Callable):
        """编程式注册回调: manager.on("preToolUse", my_fn)"""
        # 旧事件名兼容
        event = EventType.LEGACY_MAP.get(event, event)
        self._callbacks.setdefault(event, []).append(callback)

    # ── 触发 ─────────────────────────────────────────────────

    def emit(self, event: str, **context) -> dict:
        """触发事件，返回结果

        Args:
            event: 事件类型
            **context: 事件上下文
                - tool_name: 工具名（preToolUse/postToolUse）
                - tool_args: 工具参数
                - tool_result: 工具结果（postToolUse）
                - file_path: 文件路径（fileEdited/Created/Deleted）
                - task: 任务对象（preTaskExecution/postTaskExecution）
                - user_input: 用户输入（promptSubmit）

        Returns:
            {"allowed": bool, "messages": list[str], "outputs": list[str]}
        """
        result = {"allowed": True, "messages": [], "outputs": []}

        # 1. 执行配置文件中的 Hook
        for hook in self._hooks:
            if hook.event_type != event:
                continue
            if not self._should_trigger(hook, context):
                continue

            output = self._execute_hook(hook, context)
            if output is not None:
                result["outputs"].append(output)
                # preToolUse: 检查输出是否拒绝
                if event == EventType.PRE_TOOL_USE:
                    if self._is_access_denied(output):
                        result["allowed"] = False

        # 2. 执行编程式回调
        for cb in self._callbacks.get(event, []):
            try:
                cb_result = cb(**context)
                if cb_result is False:
                    result["allowed"] = False
                elif isinstance(cb_result, str):
                    result["outputs"].append(cb_result)
            except Exception as e:
                result["messages"].append("Hook [{}] 错误: {}".format(event, e))

        return result

    def _should_trigger(self, hook: HookConfig, context: dict) -> bool:
        """检查 Hook 是否应该在当前上下文中触发"""
        # 文件事件：检查 patterns
        if hook.event_type in (EventType.FILE_EDITED, EventType.FILE_CREATED, EventType.FILE_DELETED):
            file_path = context.get("file_path", "")
            if not file_path or not hook.file_patterns:
                return False
            return any(fnmatch.fnmatch(file_path, p) or fnmatch.fnmatch(os.path.basename(file_path), p)
                       for p in hook.file_patterns)

        # 工具事件：检查 toolTypes
        if hook.event_type in (EventType.PRE_TOOL_USE, EventType.POST_TOOL_USE):
            tool_name = context.get("tool_name", "")
            if not tool_name:
                return False
            return _matches_tool_types(tool_name, hook.tool_types)

        return True

    def _execute_hook(self, hook: HookConfig, context: dict) -> str | None:
        """执行单个 Hook 动作"""
        if hook.action_type == "runCommand":
            return self._run_command(hook, context)
        elif hook.action_type == "askAgent":
            return self._ask_agent(hook, context)
        # 兼容旧格式
        elif hook.action_type == "run_command":
            return self._run_command(hook, context)
        elif hook.action_type == "print_message":
            print("  💡 {}".format(hook.prompt))
            return hook.prompt
        return None

    def _run_command(self, hook: HookConfig, context: dict) -> str | None:
        """执行 runCommand 动作"""
        cmd = hook.command
        if not cmd:
            return None
        try:
            timeout = hook.timeout if hook.timeout > 0 else None
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               timeout=timeout or 60)
            output = r.stdout.strip() or r.stderr.strip()
            if r.returncode != 0 and r.stderr:
                print("  ⚠️  Hook [{}]: {}".format(hook.name, r.stderr[:100]))
            return output
        except subprocess.TimeoutExpired:
            print("  ⚠️  Hook [{}] 超时 ({}s)".format(hook.name, hook.timeout))
            return None
        except Exception as e:
            print("  ⚠️  Hook [{}] 错误: {}".format(hook.name, e))
            return None

    def _ask_agent(self, hook: HookConfig, context: dict) -> str | None:
        """执行 askAgent 动作 — 将提示发送给 Agent"""
        prompt = hook.prompt
        if not prompt:
            return None

        # 注入上下文变量
        tool_name = context.get("tool_name", "")
        tool_args = context.get("tool_args", {})
        if tool_name:
            prompt = prompt + "\n\n[工具: {}, 参数: {}]".format(
                tool_name, json.dumps(tool_args, ensure_ascii=False)[:200])

        # 如果有 Agent 引用，通过 spawn 执行
        if self._agent_ref and hasattr(self._agent_ref, "spawn"):
            try:
                result = self._agent_ref.spawn(prompt, system_prompt="你是 Hook 审查助手。简洁回答。")
                return result
            except Exception as e:
                return "Hook askAgent 错误: {}".format(e)

        # 无 Agent 引用时直接返回 prompt
        return prompt

    @staticmethod
    def _is_access_denied(output: str) -> bool:
        """检查 Hook 输出是否表示拒绝访问"""
        if not output:
            return False
        deny_keywords = ["denied", "forbidden", "not allowed", "拒绝", "禁止",
                         "不允许", "access denied", "permission denied", "blocked"]
        lower = output.lower()
        return any(kw in lower for kw in deny_keywords)

    # ── 查询 ─────────────────────────────────────────────────

    def list_hooks(self) -> list[dict]:
        return [h.to_dict() for h in self._hooks]

    def get_hooks_for_event(self, event: str) -> list[HookConfig]:
        return [h for h in self._hooks if h.event_type == event]


# ── 全局单例 ────────────────────────────────────────────────

_manager: HookManager | None = None


def get_hook_manager() -> HookManager:
    global _manager
    if _manager is None:
        _manager = HookManager()
    return _manager


# ── 兼容旧接口 ─────────────────────────────────────────────

def load_hooks(agent, config_path: str = "config/hooks.json") -> list[str]:
    """加载 Hook 并注册到 Agent。优先使用 WorkspaceManager 合并多工作区配置。"""
    mgr = get_hook_manager()
    mgr.set_agent(agent)

    # 尝试通过 WorkspaceManager 加载多工作区 hooks
    loaded = []
    try:
        from agent_core.workspace import get_workspace_manager
        ws_mgr = get_workspace_manager()
        hook_paths = ws_mgr.merge_hooks_configs()
        if hook_paths:
            loaded = mgr.load_from_files(hook_paths)
    except Exception:
        pass

    # 回退: 直接加载指定路径
    if not loaded:
        loaded = mgr.load_from_file(config_path)

    # 桥接: Agent 的 on/emit 委托给 HookManager
    agent._hook_manager = mgr

    def new_on(event, callback):
        mgr.on(event, callback)

    def new_emit(event, **kwargs):
        result = mgr.emit(event, **kwargs)
        return result["allowed"]

    agent.on = new_on
    agent._emit = new_emit

    return loaded
