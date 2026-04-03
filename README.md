#  ◈ Nexus Agent

轻量级 AI Agent 框架，内置 Harness 驾驭层 + 162 个专家角色自动匹配。

> 你输入自然语言，Nexus Agent 自动选模型、匹配专家角色、执行工具、检查输出质量。

## 为什么存在

现有 Agent 框架能让 Agent "跑起来"，但无法保证 Agent "跑得对"。Nexus Agent 解决三个问题：

1. Agent 写出违反架构规范的代码 → Harness 驾驭层在写入前拦截
2. Agent 回复中编造 URL、包名、过度输出 → OutputGuard 自动检测并修正
3. 不同领域的问题需要不同专业知识 → Agency Agents 自动匹配 162 个专家角色

## 快速开始

```bash
git clone <repo-url> nexus-agent
cd nexus-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.local.example .env.local
# 编辑 .env.local，填入至少一个 API Key（DeepSeek / 智谱 / OpenAI / SiliconFlow）
```

启动：

```bash
# CLI 模式（终端交互）
python main.py

# Web 模式（浏览器交互）
python3 -m server.app
# 打开 http://localhost:8000
```

Web 模式提供：
- 162 个专家角色分组浏览和手动选择（中文名称）
- 20 个大模型 Provider 的 API Key 配置和模型切换
- Ollama 本地模型测试连接
- 亮色/暗色主题切换
- Markdown 渲染（代码高亮、表格、列表）
- WebSocket 实时对话

你会看到 REPL 交互界面。直接输入自然语言开始对话：

```
  ❯ 帮我写一个 React 虚拟列表组件
    匹配专家: 🖥️ 前端开发者
    coding → deepseek/deepseek-chat
  ...（流式输出）
```

Nexus Agent 自动完成了三件事：识别这是前端任务 → 匹配前端开发者角色 → 选择 coding 能力最强的模型。


## Agency Agents：162 个专家角色自动匹配

Nexus Agent 集成了 [agency-agents](https://github.com/msitarzewski/agency-agents) 开源角色库。你对话时，框架根据输入内容自动匹配最合适的专家角色，将其专业知识注入到 Agent 的 system prompt 中。

### 工作原理

```
用户输入 "检查这个 API 有没有 SQL 注入漏洞"
    │
    ├─ 关键词匹配: "安全" + "漏洞" + "SQL注入" → security-engineer (0.85)
    ├─ 防抖检查: 新角色分数足够高 → 切换
    ├─ 注入角色 system prompt → Agent 以安全工程师身份回答
    └─ 终端提示: 匹配专家: 🔒 安全工程师
```

### 覆盖的角色分类

| 分类 | 角色数 | 示例 |
|------|--------|------|
| engineering | 26 | 前端开发者、安全工程师、DevOps 自动化师、数据库优化师 |
| marketing | 29 | SEO 专家、抖音策略师、小红书专家、增长黑客 |
| design | 8 | 品牌守护者、UI 设计师、UX 研究员 |
| testing | 8 | API 测试、性能压测、无障碍审计 |
| game-development | 20 | Unity、Unreal、Godot、Blender 专家 |
| 其他 | 71 | 产品、项目管理、销售、学术、空间计算等 |

### 设置

agency-agents 仓库已包含在项目中（`agency-agents/` 目录）。如果你删除了它，重新获取：

```bash
git clone https://github.com/msitarzewski/agency-agents.git agency-agents
```

也可以通过环境变量指定路径：

```bash
# .env
AGENCY_AGENTS_DIR=/path/to/agency-agents
```

找不到目录时，功能静默降级，不影响 Nexus Agent 正常使用。

### 命令

| 命令 | 说明 |
|------|------|
| `/agency` | 查看当前匹配的专家角色 |
| `/agency list` | 列出所有 162 个角色 |
| `/agency list engineering` | 按分类筛选 |
| `/agency use 安全工程师` | 手动切换角色 |
| `/agency reset` | 重置为自动匹配模式 |
| `/agency reload` | 重新扫描角色目录 |


## 核心能力

### 模型智能路由

Auto 模式根据输入内容自动选择最合适的模型。简单问候用轻量模型，代码任务用 coding 能力强的模型。

```
"你好"           → fast  → glm-4-flash（免费、低延迟）
"写一个排序函数"  → coding → deepseek-chat（代码能力强、成本低）
"分析这个架构"    → reasoning → gpt-4o（推理能力强）
```

支持 20 个提供商：OpenAI、Anthropic、Google、DeepSeek、智谱、通义千问、豆包、Moonshot、Groq、Mistral、xAI、Cohere、百川、MiniMax、阶跃星辰、零一万物、SiliconFlow、OpenRouter、Together、Ollama（本地）。

### Harness 驾驭层

在 Agent 写代码和回复时自动检查质量，不靠自觉，靠拦截。

```
写操作前 → Verifier 检查（依赖方向/命名规范/安全/幻觉路径）
回复后   → OutputGuard 检查（编造 URL/编造包名/过度输出/代码幻觉）
度量闭环 → 高频违规自动生成 Steering 注入 system prompt
```

初始化驾驭层：

```bash
python main.py
❯ /harness init    # 生成 .harnessrc.json 配置
❯ /harness scan    # 扫描项目结构
❯ /harness doctor  # 7 项健康检查
❯ /harness metrics # 查看拦截/自修复/逃逸统计
```

### 9 层工具安全管线

从命令黑名单到用户确认，9 层递进式安全检查：

```
L1 命令黑名单 → L2 风险检测 → L3 Provider 策略 → L4 Tier 限制
→ L5 安全等级 → L6 MCP autoApprove → L7 AUTO_APPROVE
→ L8 preToolUse Hook → L9 用户确认
```

### 上下文卫生系统

解决 Agent 长对话中的四个痛点：

| 痛点 | 解决方案 |
|------|---------|
| 上下文中毒（错误信息污染） | MemoryDetox — 有毒消息在摘要时被替代 |
| 上下文分心（过度关注历史） | FocusManager — 动态调整短期记忆轮数 |
| 上下文混乱（工具文档太多） | ToolBudget — 基于使用频率和 token 预算控制 |
| 上下文冲突（前后信息矛盾） | ConflictDetector — 以最新指令为准 |

### 多 Agent 协作编排器

借鉴 CrewAI + LangGraph 的设计：

```python
from agent_core import AgentOrchestrator, AgentRole, SharedContext, Task, Pipeline

reviewer = AgentRole(name="reviewer", role="代码审查专家", goal="发现问题")
fixer = AgentRole(name="fixer", role="代码修复专家", goal="修复问题")

pipeline = Pipeline("code-review")
pipeline.add_task(Task(name="分析", description="分析代码安全性",
    agent_role=reviewer, output_key="analysis"))

orchestrator = AgentOrchestrator(agent)
orchestrator.register_role(reviewer)
results = orchestrator.run(pipeline, SharedContext())
```

### 错误自动恢复

两阶段降级 + 熔断器：

```
阶段 1: 同 Provider 内重试 + Key 轮换
阶段 2: 跨 Provider Fallback（跳过已熔断的）
熔断器: 指数退避恢复（1min → 5min → 25min → 1h）
```


## 交互命令

| 命令 | 说明 |
|------|------|
| `/models` | 查看/切换模型 |
| `/tools` | 查看已注册工具 |
| `/tokens` | 查看 Token 消耗 |
| `/agency` | 专家角色管理（自动匹配） |
| `/spec <需求>` | 创建 Spec 并执行 |
| `/task <需求>` | 长任务控制 |
| `/spawn <任务>` | 派生子 Agent |
| `/harness` | 驾驭层管理 |
| `/mcp` | MCP Server 管理 |
| `/steer` | Steering 文件管理 |
| `/workspace` | 工作区管理 |
| `/power` | Powers 扩展管理 |
| `/agent` | 自定义 Agent 管理 |
| `/save` `/load` | 会话管理 |
| `/keys` | 多 Key 轮换管理 |
| `/quit` | 退出 |

## 扩展

### 添加工具

在 `skills/` 下新建 `.py` 文件，框架自动发现加载：

```python
from agent_core import tool

@tool("my_tool", "工具描述", {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
})
def my_tool(query: str) -> str:
    return f"结果: {query}"
```

### 添加 Steering 指导

在 `steering/` 下创建 `.md` 文件：

```yaml
---
inclusion: always      # 始终加载（默认）
# inclusion: fileMatch   # 文件匹配时加载（需配合 fileMatchPattern）
# inclusion: manual      # /steer <name> 手动激活
---

你的指导内容...
```

### 添加 Hook

编辑 `config/hooks.json`，支持 10 种事件 × 2 种动作。

### 添加 MCP Server

编辑 `config/mcp_servers.json`，标准 JSON-RPC 2.0 over stdio。

## 配置

### `.env`（共享配置，可提交 Git）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MODEL_MODE` | 模型模式（auto / 固定提供商名） | `auto` |
| `MAX_CONTEXT_TOKENS` | 上下文 Token 预算 | `4000` |
| `MAX_STREAM_OUTPUT_CHARS` | 流式输出最大字符数 | `8000` |
| `STREAM_TIMEOUT` | 流式输出超时（秒） | `120` |
| `AUTO_APPROVE` | 工具自动放行策略 | 空 |
| `MAX_RETRIES` | 单个提供商最大重试次数 | `3` |
| `DISABLE_OLLAMA` | 禁用 Ollama 本地模型探测 | `1` |
| `AGENCY_AGENTS_DIR` | agency-agents 仓库路径（可选） | 自动搜索 |

### `.env.local`（敏感配置，不提交 Git）

```bash
# 填入任意一个即可使用，支持 19 个提供商
DEEPSEEK_API_KEY=sk-xxx
OPENAI_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-ant-xxx
GOOGLE_API_KEY=AIza-xxx
ZHIPU_API_KEY=xxx
# 更多：QWEN / DOUBAO / MOONSHOT / GROQ / MISTRAL / XAI / COHERE
#       BAICHUAN / MINIMAX / STEPFUN / YI / SILICON / OPENROUTER / TOGETHER
```

## 项目结构

```
nexus-agent/
├── main.py                  # 入口 + REPL（CLI 模式）
├── server/                  # Web 模式（localhost 交互）
│   ├── app.py               # FastAPI + WebSocket 服务
│   └── static/index.html    # 前端页面（角色面板 + 模型选择 + API Key 设置）
├── agent_core/              # 核心框架
│   ├── agent.py             # Agent 引擎（Run Loop + Hook + 工具执行）
│   ├── agency_agents.py     # Agency Agents 自动匹配系统
│   ├── agency_i18n.py       # 162 个角色的中英文名称映射
│   ├── mixins/              # Agent 职责拆分（Session/SubAgent/SceneModel）
│   ├── orchestrator.py      # 多 Agent 协作编排器
│   ├── llm.py               # LLM 抽象层（20 个 Provider + 错误恢复 + 熔断器）
│   ├── model_router.py      # 模型智能路由
│   ├── tools.py             # 工具注册表 + Shell 安全
│   ├── approval.py          # 9 层工具安全管线
│   ├── token_optimizer.py   # Token 优化（分层记忆 + 压缩）
│   ├── context_hygiene.py   # 上下文卫生系统
│   ├── memory_index.py      # 可检索记忆索引
│   ├── md_render.py         # 终端 Markdown 渲染器
│   ├── harness/             # Harness 驾驭层（Verifier + OutputGuard + Metrics）
│   ├── steering.py          # Steering 指导系统
│   ├── hooks.py             # Hook 事件系统
│   ├── task_harness.py      # 长任务控制框架
│   └── ui.py                # 终端 UI 工具
├── commands/                # 命令处理模块（14 个文件）
│   ├── agency_cmds.py       # /agency 专家角色管理
│   ├── harness_cmds.py      # /harness 驾驭层管理
│   └── ...
├── skills/                  # 技能扩展包（自动发现加载）
├── agency-agents/           # 162 个专家角色（https://github.com/msitarzewski/agency-agents）
├── steering/                # Steering 指导文件
├── config/                  # Hook + MCP 配置
├── agents/                  # 自定义 Agent 配置
├── tests/                   # pytest 测试套件
├── .env / .env.local        # 配置文件
└── requirements.txt         # Python 依赖
```

## 运行测试

```bash
pip install pytest hypothesis
pytest                              # 全部测试
pytest tests/test_agency_agents.py  # Agency Agents 匹配测试
pytest tests/test_orchestrator.py   # 编排器测试
pytest -k "harness"                 # Harness 相关测试
```

## 技术栈

- Python 3.10+
- OpenAI SDK（多提供商兼容接口）
- FastAPI + uvicorn（Web 模式）
- prompt_toolkit（CLI 输入，CJK 字符支持）
- tiktoken（Token 精确计数）
- python-dotenv（环境变量管理）
- requests（HTTP 请求）
- Hypothesis（属性测试）

## License

MIT

## 致谢

- [agency-agents](https://github.com/msitarzewski/agency-agents) — 162 个专家角色定义，MIT 协议，由 [AgentLand Contributors](https://github.com/msitarzewski/agency-agents) 维护。Nexus Agent 通过自动匹配系统集成这些角色，在对话时注入对应的专业知识。
