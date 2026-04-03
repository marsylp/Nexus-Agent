"""MCP 协议客户端

特性：
- 标准 JSON-RPC 2.0 over stdio
- 自动检测传输格式（JSON Lines / Content-Length 头）
- 非阻塞 IO + select 超时控制
- 完整的生命周期管理（启动→握手→调用→关闭）
- 工具 schema 自动转换为 OpenAI function calling 格式
"""
from __future__ import annotations
import json, subprocess, threading, uuid, os, time, fcntl, select
from typing import Any


class MCPClient:
    """单个 MCP Server 的客户端连接

     MCP 连接管理：
    - 进程生命周期管理
    - 协议自动协商
    - 工具发现与调用
    """

    def __init__(self, name: str, config: dict):
        self.name = name
        self.command: str = config.get("command", "")
        self.args: list[str] = config.get("args", [])
        self.env: dict = config.get("env", {})
        self.auto_approve: list[str] = config.get("autoApprove", [])
        self.disabled: bool = config.get("disabled", False)

        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._buf = b""
        self._mode: str | None = None  # "jsonl" | "header"
        self._server_info: dict = {}
        self._server_capabilities: dict = {}
        self.tools: list[dict] = []  # 原始 MCP tool 定义

    # ── 生命周期 ─────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def connect(self) -> bool:
        """启动 MCP Server 并完成协议握手"""
        if self.disabled:
            return False
        if not self.command:
            return False
        try:
            full_env = {**os.environ, **(self.env or {})}
            # 清理空值环境变量（未配置的 key）
            full_env = {k: v for k, v in full_env.items() if v}

            self._process = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=full_env,
            )
            time.sleep(1)
            if self._process.poll() is not None:
                err = self._process.stderr.read().decode(errors="ignore")
                raise RuntimeError(f"进程立即退出: {err[:200]}")

            # 非阻塞 stdout
            fd = self._process.stdout.fileno()
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

            # MCP 协议握手
            self._handshake()
            return True

        except Exception as e:
            print(f"  ⚠️  MCP [{self.name}] 连接失败: {e}")
            self.disconnect()
            return False

    def disconnect(self):
        """关闭 MCP Server 进程"""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        self._buf = b""
        self._mode = None
        self.tools = []

    def reconnect(self) -> bool:
        """重新连接（配置变更后调用）"""
        self.disconnect()
        return self.connect()

    # ── MCP 协议握手 ─────────────────────────────────────────

    def _handshake(self):
        """MCP 协议初始化握手：initialize → initialized → tools/list"""
        # Step 1: initialize
        result = self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "nexus-agent", "version": "1.0.0"},
        })
        if not result:
            raise RuntimeError("initialize 无响应")

        self._server_info = result.get("serverInfo", {})
        self._server_capabilities = result.get("capabilities", {})

        # Step 2: initialized 通知
        self._notify("notifications/initialized", {})
        time.sleep(0.3)

        # Step 3: 发现工具
        self._discover_tools()

    def _discover_tools(self):
        """获取 MCP Server 提供的工具列表"""
        result = self._rpc("tools/list", {})
        if result and "tools" in result:
            self.tools = result["tools"]

    # ── 工具调用 ─────────────────────────────────────────────

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """调用 MCP 工具，返回文本结果"""
        if not self.connected:
            return f"[MCP 错误] {self.name} 未连接"
        try:
            result = self._rpc("tools/call", {
                "name": tool_name,
                "arguments": arguments,
            })
            if not result:
                return "[MCP 错误] 无响应"
            if "content" in result:
                parts = []
                for item in result["content"]:
                    if item.get("type") == "text":
                        parts.append(item["text"])
                    elif item.get("type") == "image":
                        parts.append(f"[图片: {item.get('mimeType', 'image')}]")
                    else:
                        parts.append(json.dumps(item, ensure_ascii=False))
                return "\n".join(parts)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"[MCP 错误] {e}"

    def is_auto_approved(self, tool_name: str) -> bool:
        """检查工具是否在 autoApprove 列表中"""
        if not self.auto_approve:
            return False
        return tool_name in self.auto_approve or "*" in self.auto_approve

    # ── 工具 Schema 转换 ─────────────────────────────────────

    def get_openai_tools(self, prefix: str = "") -> list[dict]:
        """将 MCP 工具转换为 OpenAI function calling 格式

        Args:
            prefix: 工具名前缀，如 "mcp_filesystem_"
        """
        specs = []
        for tool in self.tools:
            name = f"{prefix}{tool['name']}" if prefix else tool["name"]
            schema = tool.get("inputSchema", {"type": "object", "properties": {}})
            # 清理 $schema 字段（OpenAI 不接受）
            schema = {k: v for k, v in schema.items() if k != "$schema"}
            specs.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"[MCP:{self.name}] {tool.get('description', '')}",
                    "parameters": schema,
                },
            })
        return specs

    # ── JSON-RPC 通信层 ──────────────────────────────────────

    def _rpc(self, method: str, params: dict, timeout: float = 15) -> dict | None:
        """发送 JSON-RPC 请求并等待匹配的响应"""
        with self._lock:
            rid = str(uuid.uuid4())[:8]
            self._write({"jsonrpc": "2.0", "id": rid, "method": method,
                          "params": params})
            return self._read_response(rid, timeout)

    def _notify(self, method: str, params: dict):
        """发送 JSON-RPC 通知（无 id，不等待响应）"""
        with self._lock:
            self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, msg: dict):
        if not self._process or not self._process.stdin:
            raise RuntimeError("MCP Server 未启动")
        body = json.dumps(msg).encode("utf-8")
        if self._mode == "header":
            header = f"Content-Length: {len(body)}\r\n\r\n".encode()
            self._process.stdin.write(header + body)
        else:
            self._process.stdin.write(body + b"\n")
        self._process.stdin.flush()

    def _read_response(self, req_id: str, timeout: float = 15) -> dict | None:
        """读取匹配 req_id 的 JSON-RPC 响应"""
        deadline = time.time() + timeout

        while time.time() < deadline:
            if not self.connected:
                return None

            # 填充缓冲区
            self._fill_buf(deadline)

            if not self._buf:
                continue

            # 首次数据自动检测协议
            if self._mode is None:
                stripped = self._buf.lstrip()
                self._mode = "header" if stripped.startswith(b"Content-Length") else "jsonl"

            # 解析消息
            msg = self._parse_one_message()
            if msg is not None:
                if msg.get("id") == req_id:
                    return msg.get("result")
                # 通知或不匹配的响应，继续
                continue

        return None

    def _parse_one_message(self) -> dict | None:
        """从缓冲区解析一条完整消息"""
        if self._mode == "header":
            return self._parse_header()
        return self._parse_jsonl()

    def _parse_jsonl(self) -> dict | None:
        """JSON Lines: 按 \\n 分割，每行一个 JSON"""
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
        return None

    def _parse_header(self) -> dict | None:
        """Content-Length 头: 先读头部获取长度，再读对应字节的 body"""
        sep = b"\r\n\r\n"
        if sep not in self._buf:
            return None

        sep_idx = self._buf.index(sep)
        header = self._buf[:sep_idx].decode("utf-8", errors="ignore")

        length = 0
        for line in header.split("\r\n"):
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1].strip())
                break

        if length <= 0:
            self._buf = self._buf[sep_idx + 4:]
            return None

        body_start = sep_idx + 4
        if len(self._buf) < body_start + length:
            return None  # body 还没读完

        body = self._buf[body_start:body_start + length]
        self._buf = self._buf[body_start + length:]

        try:
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _fill_buf(self, deadline: float):
        """非阻塞读取 stdout 数据"""
        wait = min(deadline - time.time(), 1.0)
        if wait <= 0:
            return
        ready, _, _ = select.select([self._process.stdout], [], [], wait)
        if ready:
            try:
                chunk = self._process.stdout.read(65536)
                if chunk:
                    self._buf += chunk
            except BlockingIOError:
                pass
