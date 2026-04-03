# Nexus Agent 架构解说文档

> 版本: 0.0.1 | 更新日期: 2026-04-03

本文档详细解说 Nexus Agent 的系统架构、模块设计、数据流和设计决策，面向希望深入理解或参与开发的工程师。

---

## 1. 系统全景

Nexus Agent 是一个 Python 原生的 AI Agent 框架，核心设计目标是**让 Agent 可靠地工作**，而不仅仅是让 Agent 能工作。

整个系统分为四个层次：

```
┌──────────────────────────────────────────────────────────────┐
│  表现层    main.py + commands/                                │
│            REPL 交互循环 + 14 个命令模块                      │
├──────────────────────────────────────────────────────────────┤
│  引擎层    agent_core/                                        │
│            Agent 引擎 + LLM 抽象 + 工具系统 + Token 优化      │
│            Agency Agents 自动匹配（162 个专家角色）            │
├──────────────────────────────────────────────────────────────┤
│  驾驭层    agent_core/harness/ + orchestrator.py              │
│            约束检查 + 输出守卫 + 认知感知 + 度量闭环 + 编排器  │
├──────────────────────────────────────────────────────────────┤
│  扩展层    skills/ + steering/ + powers/ + agents/ + config/  │
│            技能 + 指导 + 能力包 + 自定义 Agent + Hook/MCP     │
│            agency-agents/ — 162 个专家角色库（独立 Git 仓库）  │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. 表现层：main.py + commands/

### 2.1 设计决策

main.py 曾经是一个 1115 行的 God Function，所有 30+ 命令的解析和处理都堆在一个文件里。重构后拆分为：

- `main.py`（163 行）：只负责启动初始化和 REPL 循环
- `commands/`（14 个模块）：每个命令组一个文件

```
commands/
├── __init__.py          # 统一导出
├── model_cmds.py        # /models /model
├── spec_cmds.py         # /spec
├── task_cmds.py         # /task（最复杂，226 行）
├── spawn_cmds.py        # /spawn
├── mcp_cmds.py          # /mcp
├── steer_cmds.py        # /steer
├── workspace_cmds.py    # /workspace
├── power_cmds.py        # /power
├── agent_cmds.py        # /agent
├── agency_cmds.py       # /agency（专家角色管理）
├── session_cmds.py      # /save /load /sessions
├── keys_cmds.py         # /keys
└── harness_cmds.py      # /harness（210 行）
```

### 2.2 REPL 循环

```
用户输入 → 命令匹配（/开头）→ 委托给 commands/ 模块
         → 普通对话 → Powers 自动激活 → agent.run() → 流式输出
```

所有终端输出通过 `agent_core/ui.py` 统一格式化，包括 ANSI 颜色、Spinner 动画、进度条、对齐表格等。

---

## 3. 引擎层：Agent 核心

### 3.1 Agent 类的 Mixin 架构

Agent 类通过 Python 多继承组合四个职责模块：

```python
class Agent(SessionMixin, SubAgentMixin, SceneModelMixin):
    # Agent 本体：Run Loop、工具执行、Hook、Steering/Powers 注入
```

| Mixin | 文件 | 职责 | 方法数 |
|-------|------|------|--------|
| Agent 本体 | `agent.py` | Run Loop、工具执行、Hook 事件、Steering/Powers 注入、状态管理 | ~15 |
| `SessionMixin` | `mixins/session_mixin.py` | save/load/list/compact/auto_persist | 5 |
| `SubAgentMixin` | `mixins/sub_agent_mixin.py` | spawn/spawn_async/spawn_parallel + 信号量并发控制 | 6 |
| `SceneModelMixin` | `mixins/scene_model_mixin.py` | _resolve_model/_llm_summarize/_get_scene_model | 3 |

运行时只有一个 Agent 实例，Mixin 只是代码组织方式。

### 3.2 Run Loop 数据流

```
用户输入
  │
  ├─ promptSubmit Hook 触发
  ├─ Steering fileMatch 检查
  ├─ 工具筛选（select_tools）
  │
  ▼ 迭代循环（最多 max_iterations 轮）
  │
  ├─ 模型路由（_resolve_model）
  ├─ 上下文构建（build_context，含分层记忆压缩）
  ├─ LLM 调用（流式/非流式）
  │   ├─ Spinner 动画（思考中...）
  │   ├─ 流式 token 输出（带长度保护 + 超时保护）
  │   └─ 错误恢复（重试 + Key 轮换 + Fallback）
  │
  ├─ 如果有 tool_calls:
  │   ├─ preToolUse Hook（Harness 约束检查在此拦截）
  │   ├─ 9 层安全管线检查
  │   ├─ 并行工具执行（ThreadPoolExecutor，最多 4 并发）
  │   ├─ postToolUse Hook（自修复检测）
  │   ├─ 工具结果压缩
  │   └─ 继续下一轮迭代
  │
  ├─ 如果有文本输出:
  │   ├─ agentStop Hook（OutputGuard 输出质量检查在此触发）
  │   ├─ 自动持久化（L3 磁盘层）
  │   └─ 返回结果
  │
  └─ 达到最大迭代次数 → 返回累积内容
```

### 3.3 LLM 抽象层

```
chat_completion_resilient()
  │
  ├─ 阶段 1: 同 Provider 内
  │   ├─ 正常调用
  │   ├─ 失败 → 错误分类（rate_limit/auth/balance/timeout/server/network）
  │   ├─ 可重试错误 → 指数退避重试（最多 MAX_RETRIES 次）
  │   ├─ auth/balance 错误 → Key 轮换（AuthManager Round-Robin）
  │   └─ 全部失败 → 触发熔断器
  │
  └─ 阶段 2: 跨 Provider Fallback
      ├─ 按 FALLBACK_ORDER 尝试其他提供商
      ├─ 跳过已熔断的提供商
      └─ 全部失败 → 抛出 RuntimeError
```

熔断器状态机：

```
CLOSED ──(连续 3 次失败)──→ OPEN ──(恢复时间到)──→ HALF-OPEN
  ↑                                                    │
  └──────────────(探测成功)─────────────────────────────┘
                  (探测失败) → OPEN（更长恢复时间）
```

### 3.4 模型智能路由

三层分类策略：

```
Layer 1: 加权关键词评分
  每个关键词带权重（0~1），多命中有加成
  例: "写一个函数" → coding: 0.9

Layer 2: 上下文信号叠加
  代码块(```)、文件扩展名(.py)、URL、错误信息等

Layer 3: LLM 回退（可选，LLM_CLASSIFY=1）
  评分模糊时用便宜模型做最终判定
```

模型选择公式：
```
总分 = 能力覆盖度 × 0.5 + 历史成功率 × 0.3 + 成本优势 × 0.2
```

### 3.5 Token 优化

三层记忆架构：

```
┌─────────────────────────────────────────┐
│  System Prompt                           │  Steering + Powers + 环境上下文
├─────────────────────────────────────────┤
│  [历史摘要]                              │  超过 SHORT_TERM_TURNS 的旧对话
│  预压缩记忆冲刷提取的重要信息            │  → LLM 摘要 或 本地摘要
├─────────────────────────────────────────┤
│  短期记忆（完整保留最近 N 轮）           │  最近 4 轮完整对话
└─────────────────────────────────────────┘
```

### 3.6 上下文管理系统

> 核心原则：上下文不是用来"思考"的，是用来"检索"和"总结"的。

#### 3.6.1 四大痛点与解决方案

```
┌─────────────────────────────────────────────────────────────────┐
│  痛点一: 上下文中毒 → MemoryDetox（记忆消毒）                    │
│  检测工具失败 + 用户纠正 → 有毒消息在摘要时被替代/截断           │
├─────────────────────────────────────────────────────────────────┤
│  痛点二: 上下文分心 → FocusManager（焦点管理）                   │
│  动态短期记忆轮数（简单 2 轮 / 复杂 6 轮 / 话题切换重置 2 轮）  │
│  基于重要性评分的智能裁剪（替代粗暴的从前往后删）                │
├─────────────────────────────────────────────────────────────────┤
│  痛点三: 上下文混乱 → ToolBudget（工具预算）                     │
│  MCP 工具参与筛选（不再无条件全保留）                            │
│  分级加载: 核心工具 → 关键词匹配 → 高频使用 → 其他              │
│  总 token 预算: 不超过上下文窗口的 15%                           │
├─────────────────────────────────────────────────────────────────┤
│  痛点四: 上下文冲突 → ConflictDetector（冲突检测）               │
│  检测决策/偏好矛盾（"用方案 A" vs "用方案 B"）                  │
│  以最新用户指令为准，冲突解决提示自动注入摘要                    │
└─────────────────────────────────────────────────────────────────┘
```

#### 3.6.2 可检索记忆索引（MemoryIndex）

被压缩掉的历史消息不是丢掉，而是存入 MemoryIndex，需要时按需检索：

```
自动压缩触发
  │
  ├─ 旧消息 → 摘要化（留在 memory 中）
  ├─ 旧消息 → 存入 MemoryIndex（完整保留，可检索）
  │
  ▼ 后续对话
  │
  ├─ 用户说 "之前那个方案是什么"
  ├─ needs_retrieval() 检测到回溯信号
  ├─ retrieve() 基于关键词匹配 + 时间衰减 + 重要性评分
  ├─ 检索结果作为 [历史参考] 注入 build_context
  └─ 模型看到相关历史，准确回答
```

检索算法：`相关性 = 关键词重叠度 × 0.6 + 时间衰减 × 0.2 + 重要性 × 0.2`

时间衰减半衰期 1 小时（越新越相关）。

#### 3.6.3 全链路存储上限

```
┌──────────────────────────────────────────────────────────────────┐
│  memory（活跃上下文）                                             │
│  上限: 40 条 → 自动压缩（时间切割 + 摘要化）                     │
│  淘汰: 旧消息 → 摘要 + 存入 MemoryIndex                         │
├──────────────────────────────────────────────────────────────────┤
│  build_context（发给 LLM 的临时上下文）                           │
│  上限: MAX_CONTEXT_TOKENS                                        │
│  淘汰: 重要性评分排序，低分优先删除                               │
├──────────────────────────────────────────────────────────────────┤
│  MemoryIndex（可检索记忆索引）                                    │
│  上限: 每会话 200 条，索引文件最多 30 个                          │
│  淘汰: 加权 LRU（importance × timestamp 双因子排序）              │
├──────────────────────────────────────────────────────────────────┤
│  SessionStore（会话文件）                                         │
│  上限: 50 个文件 / 30 天                                          │
│  淘汰: TTL（超龄删除）+ FIFO（超量删最旧）                       │
├──────────────────────────────────────────────────────────────────┤
│  ToolBudget（工具定义）                                           │
│  上限: 上下文窗口的 15%                                           │
│  淘汰: 优先队列（核心 > 匹配 > 高频 > 其他）                     │
└──────────────────────────────────────────────────────────────────┘
```

#### 3.6.4 上下文管理在 Run Loop 中的集成

```
用户输入
  │
  ├─ ToolBudget.select_tools() — 智能工具筛选（含 MCP 预算控制）
  │
  ▼ 迭代循环
  │
  ├─ build_context() 集成 ContextHygiene:
  │   ├─ FocusManager.adaptive_short_term_turns() — 动态轮数
  │   ├─ MemoryDetox — 有毒消息替代/截断
  │   ├─ _extract_important_info() — 被纠正的内容不再提取
  │   ├─ ConflictDetector — 冲突解决提示注入
  │   └─ _enforce_token_budget() — 重要性评分排序裁剪
  │
  ├─ MemoryIndex.needs_retrieval() — 检测回溯信号
  │   └─ 有信号 → retrieve_as_prompt() → 注入 [历史参考]
  │
  ├─ LLM 调用 → 工具执行
  │   └─ ToolBudget.record_usage() — 记录工具使用频率
  │
  └─ _auto_persist()
      ├─ memory > 40 条 → _auto_compact_memory()
      │   ├─ 旧消息 → MemoryIndex.archive()（存入索引）
      │   ├─ 旧消息 → 摘要化（替换为一条 system 消息）
      │   └─ ContextHygiene.detox.clear()（重置毒性标记）
      └─ SessionStore.save() → _auto_cleanup()（清理旧文件）
```

### 3.7 9 层工具安全管线

```
L1  命令级黑名单     tools.py shell()     rm -rf / → 直接拦截
L2  命令级风险检测   tools.py shell()     sudo → 需确认
L3  Provider 策略    approval.py          ollama 禁止 shell
L4  Tier 级限制      approval.py          Tier 1 禁止 shell/run_python
L5  工具安全等级     approval.py          safe/confirm/dangerous
L6  MCP autoApprove  approval.py          MCP 工具免确认
L7  AUTO_APPROVE     approval.py          环境变量/分类放行
L8  preToolUse Hook  agent.py _emit()     Harness 约束检查
L9  用户交互确认     approval.py input()  最终确认
```

---

## 3.8 Agency Agents 自动匹配

Agency Agents 系统在每次用户输入时自动匹配最合适的专家角色，将其 system prompt 注入到 Agent 上下文中。

### 架构

```
agency-agents/                    agent_core/agency_agents.py
├── engineering/ (26 角色)         ┌─────────────────────────────┐
├── design/ (8 角色)        ──→   │  AgencyAgentsLoader         │
├── marketing/ (29 角色)          │  启动时扫描 .md 文件        │
├── testing/ (8 角色)             │  解析 front-matter + 正文   │
├── ...                           │  构建角色索引（162 个）      │
└── 共 162 个角色                  └──────────┬──────────────────┘
                                              │
                                   ┌──────────▼──────────────────┐
用户输入 ──────────────────────→   │  AgencyMatcher              │
                                   │  1. category 关键词匹配     │
                                   │  2. role 关键词匹配         │
                                   │  3. description 短语匹配    │
                                   │  4. 综合评分 + 防抖          │
                                   └──────────┬──────────────────┘
                                              │
                                   ┌──────────▼──────────────────┐
                                   │  Agent._rebuild_system_prompt│
                                   │  角色正文注入 system prompt  │
                                   │  [专家角色: 🔒 安全工程师]   │
                                   └─────────────────────────────┘
```

### 匹配算法

```
综合分数 = category 分数 × 0.2 + role 分数 × 0.5
         + keyword 分数 × 0.15 + description 分数 × 0.15

匹配阈值: 0.35（低于此分数不匹配）
切换阈值: 0.15（新角色必须比当前角色高出此值才切换）
```

防抖机制防止频繁切换：同一话题的后续对话保持当前角色，只有明确的话题切换（强信号）才触发角色切换。

### 降级策略

找不到 `agency-agents/` 目录时，整条链路静默降级：

```
_find_agents_dir() → None
  → AgencyAgentsLoader._roles = []
    → loader.available = False
      → Agent.run() 中 if 条件为 False，跳过匹配
        → _agency_role_content = ""，不注入任何内容
```

Nexus Agent 的行为与未接入 agency-agents 之前完全一致。

### 设计决策

**为什么用关键词匹配而不是 embedding 向量检索？**

零依赖。embedding 需要额外的模型调用（消耗 token 和延迟），而关键词匹配对"帮我写一个 React 组件"→ 前端开发者这类明确意图的匹配已经足够精准（实测 20/20 场景全部正确匹配）。未来如果需要处理模糊意图，可以在关键词匹配基础上叠加 LLM 分类，而不是替换。

**为什么截取角色正文前 2000 字符？**

Token 预算控制。agency-agents 中的角色文件平均 3000-8000 字符，完整注入会占用大量上下文窗口。前 2000 字符通常包含角色定义、核心使命和关键规则，足以让 Agent 以该角色身份回答。

---

## 4. 驾驭层：Harness Engineering

### 4.1 设计哲学

> 规则靠测试失败执行，不靠自觉。

Harness 层不是"建议"Agent 怎么做，而是在 Agent 做错时**拦截并强制修正**。

### 4.2 四层架构

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: 度量闭环层                                         │
│  MetricsCollector → JSONL 持久化                             │
│  拦截率 / 自修复率 / 逃逸 Bug / 周趋势                      │
│  高频违规 → 自动生成 Steering 增强 → 注入 system prompt      │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: 认知感知层                                         │
│  Scanner → MapGenerator → .harness/map.md                    │
│  Doctor（7 项健康检查）/ PlanGenerator（执行计划）            │
├─────────────────────────────────────────────────────────────┤
│  Layer 1: 约束执行层                                         │
│  Verifier → preToolUse Hook 拦截写操作                       │
│  6 种检查: dependency/naming/forbidden/security/i18n/hallucination │
├─────────────────────────────────────────────────────────────┤
│  OutputGuard: 输出质量守卫                                   │
│  agentStop Hook → 检查 Agent 回复质量                        │
│  URL 幻觉 / 包名幻觉 / 过度输出 / 代码幻觉                  │
│  发现问题 → 记录度量 + 注入修正提示 + 终端警告               │
└─────────────────────────────────────────────────────────────┘
```

### 4.3 OutputGuard 工作原理

这是 Harness 层最重要的创新之一。传统 Agent 框架只管"写操作"的质量，不管"说话"的质量。Nexus Agent 的 OutputGuard 填补了这个盲区。

```
Agent 回复完成
  │
  ▼ agentStop Hook 触发
  │
  ├─ OutputGuard.check(user_input, reply)
  │   ├─ 检查编造 URL（weather-app.com 等）
  │   ├─ 检查编造包名（import weather_api 等）
  │   ├─ 检查简单问题过长回复（"你是谁" → 500 字以内）
  │   └─ 检查非代码问题编造代码（多个代码块）
  │
  ├─ 有问题:
  │   ├─ 记录到 MetricsCollector（output_* 类型）
  │   ├─ 生成修正提示 → 注入 Agent memory
  │   ├─ 终端显示质量警告
  │   └─ 高频问题 → Steering 增强自动生成
  │
  └─ 无问题: 正常结束
```

### 4.4 Steering 增强闭环

```
违规发生 → MetricsCollector 记录
  │
  ├─ 累计 < 5 次: 不触发
  ├─ 累计 >= 5 次: 自动生成 Steering 提示
  │   包含: 违规类型描述 + 历史示例（脱敏）+ 正确做法指导
  │   注入到 system prompt
  │
  └─ 连续 10 次运行未出现: 自动移除 Steering
```

---

## 5. 多 Agent 协作编排器

### 5.1 设计来源

| 概念 | 来源 | Nexus Agent 实现 |
|------|------|--------------|
| Agent 角色（role/goal/backstory） | CrewAI | `AgentRole` 数据类 |
| Task 链（输入/输出自动传递） | CrewAI | `Task.context_keys` → `Task.output_key` |
| Task Delegation | CrewAI | `[DELEGATE:role_name]` 标记 |
| Hierarchical Process | CrewAI | `PipelineMode.HIERARCHICAL` + Manager Agent |
| TypedDict State | LangGraph | `SharedContext`（facts/artifacts/constraints/history） |
| State Reducer | LangGraph | `ReducerStrategy`（OVERWRITE/APPEND/MERGE） |
| Conditional Edges | LangGraph | `ConditionalEdge` + `condition_fn` |
| Checkpointing | LangGraph | `save_checkpoint` / `load_checkpoint` |
| 图可视化 | LangGraph | `Pipeline.to_mermaid()` |

### 5.2 SharedContext 数据模型

```
SharedContext
├── facts: dict[str, Any]        # 关键事实（项目路径、技术栈等）
├── artifacts: dict[str, Any]    # 产出物（代码、分析结果等）
├── constraints: list[str]       # 约束条件（来自 Harness 层）
├── history: list[dict]          # 执行历史（谁做了什么）
└── _reducers: dict[str, ReducerStrategy]  # 合并策略
```

Reducer 策略解决并行 Agent 写同一个 key 的冲突：

```
OVERWRITE: 后写覆盖（默认）
APPEND:    追加到列表（多个 Agent 的发现合并）
MERGE:     字典合并（配置项合并）
```

### 5.3 Pipeline 执行模式

```
Sequential:    T1 → T2 → T3（前置失败自动跳过后续）
                 └─ 支持 ConditionalEdge 条件分支

Parallel:      ┌─ T1 ─┐
           START─ T2 ─FINISH（快照隔离，完成后合并）
               └─ T3 ─┘

Hierarchical:  Manager Agent 查看所有 Task + 可用 Role
               → 动态分配 → 按分配方案顺序执行
```

### 5.4 Task Delegation

当 Agent 的 `allow_delegation=True` 且输出包含 `[DELEGATE:role_name]` 时：

```
Agent A 执行 Task
  │
  ├─ 输出: "这个需要安全专家处理 [DELEGATE:security]"
  │
  ├─ Orchestrator 检测到委托标记
  ├─ 查找已注册的 security 角色
  ├─ 构建委托 prompt（原始任务 + A 的初步分析）
  ├─ 通过 spawn 创建 security Agent 执行
  └─ 用委托结果替换原始输出
```

### 5.5 与 Harness 层的集成

```
Pipeline 启动
  │
  ├─ 自动注入 Harness 约束到 SharedContext.constraints
  │
  ├─ 每个 Task 执行:
  │   ├─ SharedContext 数据注入到 prompt
  │   ├─ 通过 spawn 创建隔离子 Agent
  │   ├─ Task 边界约束检查
  │   └─ 结果写入 SharedContext
  │
  └─ 执行历史记录到 SharedContext.history
```

---

## 6. 扩展层

### 6.1 Skills（技能）

`skills/` 目录下的 `.py` 文件自动发现加载。每个 Skill 通过 `@tool` 装饰器注册：

```python
@tool("name", "description", {json_schema})
def my_tool(arg: str) -> str:
    return result
```

### 6.2 Steering（指导）

Markdown 文件注入 system prompt，三种加载模式：

| 模式 | 触发条件 |
|------|---------|
| `always` | 始终加载（默认） |
| `fileMatch` | 上下文涉及匹配文件时加载 |
| `manual` | `/steer <name>` 手动激活 |

### 6.3 Hooks（事件）

10 种事件类型 × 2 种动作类型：

```
事件: fileEdited/fileCreated/fileDeleted/userTriggered
      promptSubmit/agentStop
      preToolUse/postToolUse（Harness 层在此拦截）
      preTaskExecution/postTaskExecution

动作: askAgent（通过 spawn 子 Agent 执行）
      runCommand（执行 shell 命令）
```

### 6.4 Powers（能力包）

可安装的扩展包，包含文档 + Steering + MCP + Skills：

```
powers/my-power/
├── POWER.md       # 文档（激活时注入上下文）
├── steering/      # Steering 文件
├── mcp.json       # MCP Server 配置
└── skills/        # 自定义工具
```

### 6.5 Custom Agents（自定义 Agent）

JSON 配置文件定义专用 Agent：

```json
{
  "name": "code-reviewer",
  "system_prompt": "你是代码审查专家...",
  "tools": ["read_file", "list_dir"],
  "max_iterations": 5
}
```

---

## 7. 数据流总览

### 7.1 一次完整对话的数据流

```
用户输入 "帮我写一个排序函数"
  │
  ├─ main.py REPL 接收
  ├─ Powers 关键词匹配（无匹配）
  ├─ agent.run("帮我写一个排序函数")
  │
  ├─ Steering fileMatch 检查（无匹配）
  ├─ select_tools → 匹配 coding 关键词 → 保留 shell/read_file/write_file
  │
  ├─ 模型路由: coding 0.9 → deepseek/deepseek-chat
  ├─ build_context: system + 历史摘要 + 最近 4 轮
  │
  ├─ Spinner "思考中" 启动
  ├─ chat_completion_resilient(stream=True)
  │   ├─ deepseek 调用成功
  │   └─ Spinner 停止，开始流式输出
  │
  ├─ LLM 返回 tool_call: write_file
  │   ├─ preToolUse Hook → Harness Verifier 检查
  │   │   ├─ naming 检查 → 通过
  │   │   ├─ security 检查 → 通过
  │   │   └─ 放行
  │   ├─ 9 层安全管线 → safe → 自动放行
  │   ├─ write_file 执行
  │   └─ postToolUse Hook → 自修复检测
  │
  ├─ LLM 返回文本回复
  │   ├─ agentStop Hook → OutputGuard 检查
  │   │   ├─ 无幻觉 URL → 通过
  │   │   ├─ 有代码块但用户要求了代码 → 通过
  │   │   └─ 无问题
  │   └─ 自动持久化到 ~/.nexus-agent/sessions/
  │
  └─ 返回结果给用户
```

### 7.2 持久化数据分布

```
~/.nexus-agent/
├── sessions/              # 会话持久化（JSONL，最多 50 个 / 30 天自动清理）
├── memory_index/          # 可检索记忆索引（按会话隔离，最多 30 个文件）
├── auth_profiles.json     # 多 Key 轮换配置
├── circuit_breaker.json   # 熔断器状态
├── model_history.json     # 模型历史成功率
├── workspaces.json        # 工作区注册表
├── mcp.json               # 全局 MCP 配置
├── agents/                # 全局自定义 Agent
└── powers/                # 全局 Powers

项目目录/
├── .harnessrc.json        # Harness 配置
├── .harness/
│   ├── map.md             # 认知地图
│   ├── metrics/           # 度量数据（JSONL）
│   │   ├── interceptions.jsonl
│   │   ├── self_repairs.jsonl
│   │   └── escaped_bugs.jsonl
│   ├── plans/             # 执行计划
│   └── specs/             # 规范文件
└── agency-agents/         # 162 个专家角色（独立 Git 仓库，运行时只读）
    ├── engineering/       # 26 个工程角色
    ├── design/            # 8 个设计角色
    ├── marketing/         # 29 个营销角色
    └── ...                # 共 13 个分类
```

---

## 8. 测试架构

719 个测试，覆盖所有核心模块：

| 测试文件 | 覆盖模块 | 测试数 |
|---------|---------|--------|
| `test_orchestrator.py` | 编排器（SharedContext/Task/Pipeline/Role/Reducer/Conditional/Hierarchical/Delegation/Mermaid） | 54 |
| `test_harness_layer.py` | HarnessLayer（含 Hypothesis 属性测试） | 20 |
| `test_output_guard.py` | OutputGuard（幻觉/跑题/过度输出/代码幻觉） | 18 |
| `test_agent.py` | Agent 引擎 | 19 |
| `test_model_routing_integration.py` | 模型路由集成 | 87 |
| `test_task_harness.py` | 长任务控制框架 | 86 |
| `test_harness_*.py` | Harness 子模块（config/verifier/scanner/map/doctor/plan/metrics/steering） | ~120 |
| 其他 | hooks/llm/mcp/tools/approval/steering/workspace/powers/skills | ~315 |

属性测试（Hypothesis）覆盖：
- 拦截与放行的一致性（Violation 非空 → 阻止，空 → 放行）
- 启用/禁用切换（任意 toggle 序列后行为与最终状态一致）

---

## 9. 设计决策记录

### 为什么用 Mixin 而不是组合模式？

Agent 的各个职责（会话、子 Agent、场景路由）需要访问 Agent 的内部状态（memory、_provider、_total_tokens）。Mixin 通过 `self` 直接访问，比组合模式（需要传递引用或回调）更简洁。Python 的 MRO 保证了方法解析的确定性。

### 为什么 Orchestrator 不替换旧的 spawn？

向后兼容。spawn 是简单的 fire-and-forget 模式，适合单步任务。Orchestrator 是高级编排层，适合多步骤协作。两者共存，用户按需选择。

### 为什么 OutputGuard 用规则而不是 LLM 检测？

成本和延迟。每次 Agent 回复后再调一次 LLM 做质量检查，会让响应时间翻倍。规则检测是零成本的，且对常见问题（编造 URL、过度输出）足够有效。未来可以在规则检测发现问题后，再用 LLM 做深度分析。

### 为什么 Harness 度量用 JSONL 而不是 SQLite？

追加友好。JSONL 是 append-only 的，不需要事务、锁、schema migration。Agent 运行时只做追加写入，查询只在 `/harness metrics` 时发生，JSONL 的全量扫描完全够用。

### 为什么 LLM 层的状态输出走 stderr？

避免和 Agent 的流式输出混淆。Agent 的回复内容走 stdout（流式逐字输出），而重试、fallback、熔断等状态信息走 stderr，两者不会交叉。

### 为什么 MemoryIndex 用关键词检索而不是向量数据库？

零依赖。向量数据库（FAISS、ChromaDB）需要额外安装，且 embedding 模型本身也消耗 token。关键词检索对"之前那个方案""上次讨论的文件"这类回溯查询已经足够有效。未来如果需要语义检索，可以在关键词检索基础上叠加 embedding 层，而不是替换。

### 为什么 memory 压缩用时间切割而不是重要性排序？

简单可靠。memory 压缩的目标是控制 `self.memory` 列表的长度，而不是精选内容。被切掉的消息会存入 MemoryIndex（可检索），所以不算真正丢失。如果用重要性排序来决定保留哪些消息，会破坏对话的时间连续性，模型可能看到跳跃的上下文。

### 为什么上下文卫生系统不用 LLM 做检测？

和 OutputGuard 同理——成本和延迟。毒性检测（工具失败、用户纠正）、冲突检测（正则匹配决策/偏好模式）、话题切换检测都是规则可解的问题，不需要调 LLM。每轮对话都调 LLM 做上下文分析会让响应时间翻倍。

### 为什么 Agency Agents 用关键词匹配而不是 LLM 分类？

零延迟、零成本。每次用户输入都要做角色匹配，如果用 LLM 分类，每轮对话多一次 API 调用（~500ms + token 消耗）。关键词匹配在 <1ms 内完成，对"帮我写一个 React 组件"→ 前端开发者这类明确意图的匹配已经足够精准。实测 20 个场景全部正确匹配。未来如果需要处理模糊意图（如"帮我优化这个"不知道优化什么），可以在关键词匹配分数低于阈值时触发 LLM 回退，而不是每次都调。

### 为什么 Agency Agents 截取角色正文前 2000 字符？

Token 预算控制。agency-agents 中的角色文件平均 3000-8000 字符，完整注入会占用 1000-3000 tokens 的上下文窗口（`MAX_CONTEXT_TOKENS` 默认 4000）。前 2000 字符通常包含角色定义、核心使命和关键规则，这是角色身份的核心部分。代码示例和交付物模板在后半部分，对回答质量的影响较小。

### 为什么 Agency Agents 有防抖机制？

用户体验。如果用户连续问"帮我写一个 React 组件"→"给这个组件加上 props 类型"→"再加一个 loading 状态"，三次输入应该保持同一个前端开发者角色，而不是每次重新匹配。防抖通过 `SWITCH_MARGIN = 0.15` 实现：新角色的分数必须比当前角色高出 0.15 才会切换。这避免了弱信号导致的频繁角色跳动。
