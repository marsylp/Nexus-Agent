"""Nexus Agent Web Server — FastAPI + WebSocket 实时对话

启动方式：
  python -m server.app
  # 或
  python server/app.py

访问：
  浏览器打开 http://localhost:8000
  API 文档   http://localhost:8000/docs
"""
from __future__ import annotations

import os, sys, json, asyncio, time, uuid
from pathlib import Path
from contextlib import asynccontextmanager

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# Nexus Agent 核心
from agent_core.setup_wizard import check_and_setup
check_and_setup()

from agent_core import Agent, __version__, provider_info, get_tool_specs
from agent_core.llm import list_providers
from agent_core.agency_agents import get_agency_loader, get_agency_matcher, AgencyMatcher
from skills import load_all_skills


# ── 全局状态 ────────────────────────────────────────────────

_skills_loaded = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时加载 Skills"""
    global _skills_loaded
    if not _skills_loaded:
        load_all_skills()
        _skills_loaded = True
    yield


app = FastAPI(
    title="Nexus Agent",
    version=__version__,
    description="Nexus Agent Web 交互界面",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件（前端页面）
_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")



# ── REST API ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    """首页 — 返回 Web 聊天界面"""
    html_path = _static_dir / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Nexus Agent</h1><p>静态文件未找到，请访问 /docs 查看 API</p>")


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": __version__}


@app.get("/api/models")
async def models():
    """获取可用模型列表"""
    providers = list_providers()
    return {"providers": providers}


@app.get("/api/tools")
async def tools():
    """获取已注册工具列表"""
    specs = get_tool_specs()
    return {
        "count": len(specs),
        "tools": [
            {"name": s["function"]["name"], "description": s["function"]["description"]}
            for s in specs
        ],
    }


@app.get("/api/agency/roles")
async def agency_roles(category: str | None = None):
    """获取 agency-agents 角色列表（含中文名）"""
    from agent_core.agency_i18n import get_zh_name

    loader = get_agency_loader()
    if not loader.available:
        return {"available": False, "roles": [], "groups": []}
    roles = loader.roles
    if category:
        roles = [r for r in roles if r.category.startswith(category)]

    def role_dict(r):
        return {"name": r.name, "name_zh": get_zh_name(r.name),
                "category": r.category, "emoji": r.emoji,
                "description": r.description[:80]}

    intent_groups = {
        "💻 开发编码": ["engineering"],
        "🎨 设计创意": ["design"],
        "📝 内容营销": ["marketing", "paid-media"],
        "📊 产品管理": ["product", "project-management", "testing", "sales", "support"],
        "🔧 专业领域": ["game-development", "spatial-computing", "specialized", "academic", "strategy"],
    }
    groups = []
    for group_name, cats in intent_groups.items():
        group_roles = [r for r in loader.roles if r.category.split("/")[0] in cats]
        if group_roles:
            groups.append({
                "name": group_name,
                "count": len(group_roles),
                "roles": [role_dict(r) for r in group_roles],
            })

    return {
        "available": True,
        "count": len(roles),
        "groups": groups,
        "roles": [role_dict(r) for r in roles],
    }


@app.post("/api/chat")
async def chat_sync(body: dict):
    """非流式对话（REST 兼容）"""
    message = body.get("message", "")
    if not message:
        return JSONResponse({"error": "message 不能为空"}, status_code=400)

    agent = Agent(stream=False)
    # Agency Agents 匹配
    matcher = AgencyMatcher(get_agency_loader())
    role = matcher.match(message)
    role_info = None
    if role:
        role_body = role.body[:2000]
        agent._agency_role_content = f"[专家角色: {role.emoji} {role.name}]\n{role_body}"
        agent._rebuild_system_prompt()
        role_info = {"name": role.name, "emoji": role.emoji, "category": role.category}

    reply = agent.run(message)
    return {
        "reply": reply,
        "role": role_info,
        "tokens": agent.estimated_tokens,
    }


# ── WebSocket 实时对话 ──────────────────────────────────────

@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    """WebSocket 实时对话

    客户端发送:
      {"type": "message", "content": "你好"}
      {"type": "select_role", "name": "前端开发者"}   — 手动选择角色
      {"type": "auto_mode"}                           — 恢复自动匹配
      {"type": "set_keys", "keys": {"DEEPSEEK_API_KEY": "sk-xxx"}} — 设置 API Key（仅内存）

    服务端推送:
      {"type": "role", "name": "...", "emoji": "...", "category": "..."}
      {"type": "thinking"}
      {"type": "done", "full_content": "..."}
      {"type": "error", "message": "..."}
      {"type": "keys_set", "providers": ["deepseek"]}
    """
    await websocket.accept()

    agent = Agent(stream=False)
    matcher = AgencyMatcher(get_agency_loader())
    manual_role = None  # 手动选择的角色（None = 自动模式）

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"type": "message", "content": raw}

            msg_type = msg.get("type", "message")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            # 手动选择角色
            if msg_type == "select_role":
                from agent_core.agency_i18n import get_zh_name
                role_name = msg.get("name", "")
                loader = get_agency_loader()
                role = loader.get_role(role_name)
                if role:
                    manual_role = role
                    role_body = role.body[:2000]
                    agent._agency_role_content = f"[专家角色: {role.emoji} {role.name}]\n{role_body}"
                    agent._rebuild_system_prompt()
                    await websocket.send_json({
                        "type": "role",
                        "name": role.name,
                        "name_zh": get_zh_name(role.name),
                        "emoji": role.emoji,
                        "category": role.category,
                        "manual": True,
                    })
                else:
                    await websocket.send_json({"type": "error", "message": f"未找到角色: {role_name}"})
                continue

            # 恢复自动匹配
            if msg_type == "auto_mode":
                manual_role = None
                matcher.reset()
                agent._agency_role_content = ""
                agent._rebuild_system_prompt()
                await websocket.send_json({"type": "role", "name": "自动匹配", "emoji": "🤖", "category": "", "manual": False})
                continue

            # 设置 API Key（仅存内存，不写磁盘）
            if msg_type == "set_keys":
                keys = msg.get("keys", {})
                providers_set = []
                for key_name, key_value in keys.items():
                    if key_value and key_value.strip():
                        os.environ[key_name] = key_value.strip()
                        prov = key_name.replace("_API_KEY", "").lower()
                        providers_set.append(prov)
                # 同步更新 Ollama 配置到 PROVIDERS
                from agent_core.llm import PROVIDERS as _PROVIDERS
                if keys.get("OLLAMA_MODEL"):
                    _PROVIDERS["ollama"]["model"] = keys["OLLAMA_MODEL"].strip()
                if keys.get("OLLAMA_BASE_URL"):
                    _PROVIDERS["ollama"]["base_url"] = keys["OLLAMA_BASE_URL"].strip().rstrip("/") + "/v1"
                await websocket.send_json({"type": "keys_set", "providers": providers_set})
                continue

            # 切换模型
            if msg_type == "set_model":
                from agent_core.llm import PROVIDERS as _PROVIDERS
                provider = msg.get("provider", "")
                if provider == "auto":
                    agent.set_mode("auto")
                    await websocket.send_json({"type": "model_set", "provider": "auto", "model": "自动路由"})
                elif provider in _PROVIDERS:
                    agent.set_mode(provider)
                    model_name = _PROVIDERS[provider]["model"]
                    await websocket.send_json({"type": "model_set", "provider": provider, "model": model_name})
                else:
                    await websocket.send_json({"type": "error", "message": f"未知模型: {provider}"})
                continue

            # 获取可用模型列表
            if msg_type == "get_models":
                await websocket.send_json({"type": "models_list", "providers": list_providers()})
                continue

            content = msg.get("content", "")

            # ── 协作模式（不需要 content）──────────────
            if msg_type == "collab_plan":
                content = content or msg.get("task", "")
                if not content:
                    await websocket.send_json({"type": "error", "message": "请输入任务描述"})
                    continue
                # 生成协作方案
                from server.collab import generate_plan
                await websocket.send_json({"type": "collab_status", "status": "planning", "message": "Manager 正在分析任务..."})
                try:
                    loop = asyncio.get_event_loop()
                    plan = await loop.run_in_executor(None, generate_plan, agent, content)
                    if plan and plan.subtasks:
                        await websocket.send_json({
                            "type": "collab_plan_ready",
                            "summary": plan.summary,
                            "subtasks": [
                                {"role": st.role_name, "role_zh": st.role_name_zh,
                                 "emoji": st.emoji, "description": st.description}
                                for st in plan.subtasks
                            ],
                        })
                    else:
                        await websocket.send_json({"type": "error", "message": "无法生成协作方案，请尝试更具体的任务描述"})
                except Exception as e:
                    await websocket.send_json({"type": "error", "message": f"方案生成失败: {e}"})
                continue

            if msg_type == "collab_execute":
                # 执行已确认的协作方案
                from server.collab import execute_subtasks_parallel, unify_style, SubTask
                subtasks_data = msg.get("subtasks", [])
                task_desc = msg.get("task", "")
                if not subtasks_data:
                    await websocket.send_json({"type": "error", "message": "无子任务"})
                    continue

                # 构建 SubTask 列表
                subtasks = [
                    SubTask(
                        role_name=st["role"],
                        role_name_zh=st.get("role_zh", st["role"]),
                        emoji=st.get("emoji", "🤖"),
                        description=st["description"],
                    )
                    for st in subtasks_data
                ]

                # 通知开始执行
                for i, st in enumerate(subtasks):
                    await websocket.send_json({
                        "type": "collab_progress",
                        "index": i, "total": len(subtasks),
                        "role_zh": st.role_name_zh, "emoji": st.emoji,
                        "status": "running",
                    })

                # 并行执行所有子任务（受 Semaphore 保护）
                try:
                    loop = asyncio.get_event_loop()
                    results = await loop.run_in_executor(
                        None, execute_subtasks_parallel, agent, subtasks)

                    # 推送各子任务完成状态
                    for i, st in enumerate(subtasks):
                        await websocket.send_json({
                            "type": "collab_progress",
                            "index": i, "total": len(subtasks),
                            "role_zh": st.role_name_zh, "emoji": st.emoji,
                            "status": st.status,
                            "preview": st.result[:100] if st.result else "",
                        })
                except Exception as e:
                    results = [(st.role_name_zh, f"[执行失败] {e}") for st in subtasks]

                # 风格统一
                await websocket.send_json({"type": "collab_status", "status": "unifying", "message": "正在整合各专家结果..."})
                try:
                    loop = asyncio.get_event_loop()
                    final = await loop.run_in_executor(None, unify_style, agent, task_desc, results)
                except Exception:
                    final = "\n\n---\n\n".join(f"**{name}**:\n{result}" for name, result in results)

                await websocket.send_json({
                    "type": "collab_done",
                    "full_content": final,
                    "subtask_count": len(subtasks_data),
                    "tokens": agent.estimated_tokens,
                })
                continue

            # ── 普通对话模式 ──────────────────────────────
            if not content:
                continue

            # 角色匹配：手动模式跳过自动匹配
            if not manual_role:
                from agent_core.agency_i18n import get_zh_name
                role = matcher.match(content)
                if role:
                    role_body = role.body[:2000]
                    agent._agency_role_content = f"[专家角色: {role.emoji} {role.name}]\n{role_body}"
                    agent._rebuild_system_prompt()
                    await websocket.send_json({
                        "type": "role",
                        "name": role.name,
                        "name_zh": get_zh_name(role.name),
                        "emoji": role.emoji,
                        "category": role.category,
                        "manual": False,
                    })

            await websocket.send_json({"type": "thinking"})

            try:
                loop = asyncio.get_event_loop()
                reply = await loop.run_in_executor(None, agent.run, content)
                await websocket.send_json({
                    "type": "done",
                    "full_content": reply,
                    "tokens": agent.estimated_tokens,
                })
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": str(e),
                })

    except WebSocketDisconnect:
        pass



# ── 启动入口 ────────────────────────────────────────────────

def main():
    import uvicorn
    port = int(os.environ.get("OX_PORT", "8000"))
    print(f"\n  ◈ Nexus Agent Web Server v{__version__}")
    print(f"  📡 http://localhost:{port}")
    print(f"  📖 http://localhost:{port}/docs")
    print()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
