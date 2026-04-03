"""MCP Server 管理器

特性：
- 多层配置合并（全局 ~/.nexus-agent/mcp.json + 项目 config/mcp_servers.json）
- 自动发现、连接、注册工具
- autoApprove 工具免确认
- 配置变更自动重连
- 状态查看与管理命令
"""
from __future__ import annotations
import json, os, hashlib
from agent_core.mcp_client import MCPClient
from agent_core.tools import get_registry

# 活跃的 MCP 连接
_clients: dict[str, MCPClient] = {}
# 配置指纹（用于检测变更）
_config_hash: str = ""


def _load_config() -> dict[str, dict]:
    """加载并合并多层 MCP 配置

    优先使用 WorkspaceManager 的合并逻辑（全局 + 多工作区），
    回退到直接读取配置文件。
    """
    try:
        from agent_core.workspace import get_workspace_manager
        ws_mgr = get_workspace_manager()
        merged = ws_mgr.merge_mcp_config()
        if merged:
            return merged
    except Exception:
        pass

    # 回退: 直接读取配置文件
    merged: dict[str, dict] = {}

    global_path = os.path.expanduser("~/.nexus-agent/mcp.json")
    if os.path.exists(global_path):
        try:
            with open(global_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for name, cfg in data.get("mcpServers", {}).items():
                merged[name] = cfg
        except Exception:
            pass

    project_path = "config/mcp_servers.json"
    if os.path.exists(project_path):
        try:
            with open(project_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for name, cfg in data.get("mcpServers", {}).items():
                merged[name] = cfg
        except Exception:
            pass

    return merged


def _config_fingerprint(config: dict) -> str:
    """计算配置指纹，用于检测变更"""
    raw = json.dumps(config, sort_keys=True).encode()
    return hashlib.md5(raw).hexdigest()


def _register_tools(client: MCPClient):
    """将 MCP 工具注册到全局工具注册表"""
    registry = get_registry()
    prefix = f"mcp_{client.name}_"

    for tool in client.tools:
        tool_name = f"{prefix}{tool['name']}"
        mcp_tool_name = tool["name"]

        def make_caller(c, tn):
            def caller(**kwargs):
                return c.call_tool(tn, kwargs)
            return caller

        schema = tool.get("inputSchema", {"type": "object", "properties": {}})
        schema = {k: v for k, v in schema.items() if k != "$schema"}

        registry[tool_name] = {
            "function": make_caller(client, mcp_tool_name),
            "spec": {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": f"[MCP:{client.name}] {tool.get('description', '')}",
                    "parameters": schema,
                },
            },
            "_mcp_client": client,
            "_mcp_tool": mcp_tool_name,
        }


def _unregister_tools(client_name: str):
    """从全局注册表移除指定 MCP 的工具"""
    registry = get_registry()
    prefix = f"mcp_{client_name}_"
    to_remove = [k for k in registry if k.startswith(prefix)]
    for k in to_remove:
        del registry[k]


# ── 公开 API ────────────────────────────────────────────────

def load_mcp_servers() -> list[str]:
    """加载配置并连接所有启用的 MCP Server

    Returns:
        已注册的工具名列表
    """
    global _config_hash
    config = _load_config()
    _config_hash = _config_fingerprint(config)

    loaded_tools = []

    for name, cfg in config.items():
        if cfg.get("disabled", False):
            continue
        # 跳过以 _ 开头的元数据字段
        if name.startswith("_"):
            continue

        print(f"  🔌 连接 MCP: {name} ...")
        client = MCPClient(name, cfg)

        if client.connect():
            _clients[name] = client
            _register_tools(client)
            tool_names = [f"mcp_{name}_{t['name']}" for t in client.tools]
            loaded_tools.extend(tool_names)
            auto = f", autoApprove={cfg.get('autoApprove', [])}" if cfg.get("autoApprove") else ""
            print(f"  ✅ {name}: {len(client.tools)} 个工具{auto}")
        else:
            print(f"  ❌ {name}: 连接失败")

    return loaded_tools


def reload_mcp_servers() -> list[str]:
    """重新加载配置，自动重连变更的 MCP Server

     配置变更后自动重连，无需重启。
    """
    global _config_hash
    config = _load_config()
    new_hash = _config_fingerprint(config)

    if new_hash == _config_hash:
        print("  ℹ️  MCP 配置无变更")
        return list_mcp_tools()

    _config_hash = new_hash
    print("  🔄 检测到 MCP 配置变更，重新连接 ...")

    # 关闭已移除或变更的连接
    current_names = set(_clients.keys())
    new_names = {n for n, c in config.items()
                 if not c.get("disabled", False) and not n.startswith("_")}

    # 移除不再需要的
    for name in current_names - new_names:
        print(f"  🔌 断开: {name}")
        _unregister_tools(name)
        _clients[name].disconnect()
        del _clients[name]

    # 重连变更的 + 新增的
    for name in new_names:
        cfg = config[name]
        if name in _clients:
            # 检查配置是否变更
            old_cfg_hash = hashlib.md5(
                json.dumps({"cmd": _clients[name].command,
                            "args": _clients[name].args}).encode()
            ).hexdigest()
            new_cfg_hash = hashlib.md5(
                json.dumps({"cmd": cfg.get("command", ""),
                            "args": cfg.get("args", [])}).encode()
            ).hexdigest()
            if old_cfg_hash == new_cfg_hash:
                continue  # 配置没变，跳过
            # 配置变了，重连
            print(f"  🔄 重连: {name}")
            _unregister_tools(name)
            _clients[name].disconnect()

        print(f"  🔌 连接: {name} ...")
        client = MCPClient(name, cfg)
        if client.connect():
            _clients[name] = client
            _register_tools(client)
            print(f"  ✅ {name}: {len(client.tools)} 个工具")
        else:
            print(f"  ❌ {name}: 连接失败")

    return list_mcp_tools()


def shutdown_mcp_servers():
    """关闭所有 MCP Server"""
    for name, client in _clients.items():
        try:
            client.disconnect()
        except Exception:
            pass
    _clients.clear()


def list_mcp_tools() -> list[str]:
    """列出所有已注册的 MCP 工具名"""
    registry = get_registry()
    return [k for k in registry if k.startswith("mcp_")]


def get_mcp_status() -> list[dict]:
    """获取所有 MCP Server 的状态（供 /mcp 命令使用）"""
    config = _load_config()
    status = []
    for name, cfg in config.items():
        if name.startswith("_"):
            continue
        client = _clients.get(name)
        status.append({
            "name": name,
            "disabled": cfg.get("disabled", False),
            "connected": client.connected if client else False,
            "tools": len(client.tools) if client else 0,
            "autoApprove": cfg.get("autoApprove", []),
            "description": cfg.get("_description", ""),
        })
    return status


def is_mcp_auto_approved(tool_name: str) -> bool:
    """检查 MCP 工具是否在 autoApprove 列表中"""
    registry = get_registry()
    entry = registry.get(tool_name)
    if not entry or "_mcp_client" not in entry:
        return False
    client: MCPClient = entry["_mcp_client"]
    mcp_tool: str = entry["_mcp_tool"]
    return client.is_auto_approved(mcp_tool)
