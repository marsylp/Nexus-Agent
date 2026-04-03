# Changelog

## v0.0.2 (2026-04-03)

### 新增
- **协作模式（Hierarchical）** — 多 Agent 协同工作
  - 页头 🤝 按钮开启协作模式
  - Manager Agent 自动分析任务，生成分配方案（2-4 个子任务）
  - 前端展示方案，用户确认后才执行
  - 各子 Agent 隔离执行，实时推送进度
  - 风格统一后处理，整合为连贯输出
  - 上下文完全隔离（每个子 Agent 独立 memory）

## v0.0.1 (2026-04-03)

### 新增
- **Agency Agents 自动匹配** — 集成 162 个专家角色，对话时自动匹配最合适的角色
- **角色中文翻译** — 全部 162 个角色名称翻译为中文
- **Web 交互模式** — FastAPI + WebSocket 实时对话，浏览器打开 localhost:8000
- **角色选择面板** — 按意图分 5 组浏览，支持搜索过滤和手动选择
- **模型选择器** — 支持 20 个大模型 Provider 的切换
- **API Key 设置** — 浏览器端配置，支持 19 个云端 Provider + Ollama 本地模型
- **终端 Markdown 渲染** — 代码块、标题、列表、表格的终端彩色渲染
- **亮色/暗色主题** — Web 界面支持主题切换，localStorage 持久化
- **品牌重塑** — 从 OX Agent 更名为 Nexus Agent

### 扩展
- **20 个 LLM Provider** — OpenAI、Anthropic、Google、DeepSeek、智谱、通义千问、豆包、Moonshot、Groq、Mistral、xAI、Cohere、百川、MiniMax、阶跃星辰、零一万物、SiliconFlow、OpenRouter、Together、Ollama
- **模型路由增强** — 补充 Android/iOS/Vue/React/Spring 等框架术语的分类关键词
- **工具筛选优化** — web_search 不再无条件注入，减少不必要的搜索调用

### 修复
- **终端中文删除** — 用 prompt_toolkit 替代 readline，修复 CJK 字符光标错位
- **面板关闭** — 重写 HTML 结构，修复 `</body>` 后内容导致的 DOM 异常

## v0.5.5

- Harness Engineering 驾驭层
- 多 Agent 协作编排器
- 长任务控制框架
- 上下文卫生系统
- 可检索记忆索引
- 449 个测试
