# 自定义 Agent

在此目录放置 `.json` 文件，定义专用子 Agent。

与 agency-agents 角色匹配的区别：
- 自定义 Agent 通过 `/agent invoke <名称>` 手动调用
- 可以限定工具列表、迭代次数、自动放行策略
- 适合需要隔离执行的专用任务（如代码审查只给读权限）

示例配置：

```json
{
  "name": "my-agent",
  "description": "描述",
  "system_prompt": "你是...",
  "tools": ["read_file", "list_dir"],
  "max_iterations": 5,
  "auto_approve": ["read_file"]
}
```

管理命令：
- `/agent list` — 查看所有自定义 Agent
- `/agent create` — 创建新 Agent
- `/agent invoke <名称> <任务>` — 调用执行
