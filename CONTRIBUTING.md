# 贡献指南

感谢你对 Nexus Agent 的关注。

## 开发环境

```bash
git clone https://github.com/yourname/nexus-agent.git
cd nexus-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pip install pytest hypothesis
cp .env.local.example .env.local
# 编辑 .env.local 填入 API Key
```

## 运行测试

```bash
pytest                    # 全部测试
pytest tests/test_agent.py  # 单个模块
pytest -k "agency"        # 按关键词
```

## 提交规范

使用中文 conventional commits：

```
feat: 新增用户登录功能
fix: 修复角色匹配阈值过低的问题
docs: 更新 README 模型列表
refactor: 重构 token_optimizer 压缩逻辑
test: 补充 agency_agents 匹配测试
```

## 目录结构

- `agent_core/` — 核心框架，修改前请先跑测试
- `server/` — Web 模式，修改 HTML 后刷新浏览器验证
- `skills/` — 工具扩展，新增 skill 会自动发现加载
- `agency-agents/` — 独立 Git 仓库，不要直接修改
- `tests/` — 测试，新功能必须附带测试

## 安全

- 不要在代码中硬编码 API Key
- `.env.local` 已在 `.gitignore` 中，不会被提交
- 提交前运行 `grep -rn "sk-" --include="*.py"` 检查
