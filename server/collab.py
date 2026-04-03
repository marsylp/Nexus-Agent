"""协作模式引擎 — Hierarchical / Supervisor 多 Agent 协作

风险修复：
1. Manager JSON 输出 — 加固解析 + 重试 + 强制 reasoning 模型
2. 子 Agent 超时 — 独立 try/catch + 超时后跳过不阻塞
3. 风格统一失败 — fallback 到结构化拼接
4. 并发资源耗尽 — 使用 spawn_parallel 受 Semaphore 保护
"""
from __future__ import annotations

import json, re, time
from dataclasses import dataclass, field
from agent_core.agent import Agent
from agent_core.agency_agents import get_agency_loader
from agent_core.agency_i18n import get_zh_name


@dataclass
class SubTask:
    """子任务"""
    role_name: str
    role_name_zh: str
    emoji: str
    description: str
    status: str = "pending"
    result: str = ""
    duration: float = 0.0


@dataclass
class CollabPlan:
    """协作方案"""
    task: str
    summary: str
    subtasks: list[SubTask] = field(default_factory=list)
    final_result: str = ""
    total_tokens: int = 0


MANAGER_PROMPT = """你是任务分配专家。将用户任务拆分为 2-4 个子任务，分配给最合适的专家。

可用角色：
{roles_desc}

用户任务：{task}

只输出 JSON，格式如下：
{{"summary": "一句话描述", "subtasks": [{{"role": "角色英文名", "description": "具体任务"}}]}}"""


def _extract_json(text: str) -> dict | None:
    """从 LLM 输出中提取 JSON，容忍各种格式问题

    策略：
    1. 直接解析
    2. 去掉 markdown 代码块标记后解析
    3. 用正则提取第一个 {...} 子串后解析
    """
    if not text:
        return None

    clean = text.strip()

    # 策略 1：直接解析
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # 策略 2：去掉 markdown 代码块
    if "```" in clean:
        # 提取 ``` 之间的内容
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', clean, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass

    # 策略 3：提取第一个 {...} 子串（处理 LLM 在 JSON 前后加了废话的情况）
    brace_start = clean.find('{')
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(clean)):
            if clean[i] == '{':
                depth += 1
            elif clean[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(clean[brace_start:i + 1])
                    except json.JSONDecodeError:
                        break

    return None


def generate_plan(agent: Agent, task: str, max_retries: int = 2) -> CollabPlan | None:
    """Manager Agent 生成协作方案

    风险修复 #1：JSON 解析加固 + 重试
    """
    loader = get_agency_loader()
    if not loader.available:
        return None

    roles_desc = "\n".join(
        f"- {r.name} ({get_zh_name(r.name)}): {r.description[:60]}"
        for r in loader.roles[:30]
    )
    prompt = MANAGER_PROMPT.format(roles_desc=roles_desc, task=task)

    for attempt in range(max_retries):
        try:
            raw = agent.spawn(
                prompt,
                system_prompt="你是任务分配专家。只输出合法 JSON，不要任何其他文字。",
                timeout=30,
            )
        except Exception:
            continue

        data = _extract_json(raw)
        if not data or "subtasks" not in data:
            continue

        plan = CollabPlan(task=task, summary=data.get("summary", "任务分析"))

        for st in data.get("subtasks", []):
            role_name = st.get("role", "")
            role = loader.get_role(role_name)
            if role:
                plan.subtasks.append(SubTask(
                    role_name=role.name,
                    role_name_zh=get_zh_name(role.name),
                    emoji=role.emoji,
                    description=st.get("description", ""),
                ))

        if len(plan.subtasks) >= 2:
            return plan

    return None


def execute_subtask(agent: Agent, subtask: SubTask) -> str:
    """执行单个子任务

    风险修复 #2：独立 try/catch，超时不阻塞其他子任务
    """
    loader = get_agency_loader()
    role = loader.get_role(subtask.role_name)
    if not role:
        return f"[错误] 未找到角色: {subtask.role_name}"

    role_body = role.body[:2000]
    system_prompt = f"[专家角色: {role.emoji} {role.name}]\n{role_body}"

    start = time.time()
    try:
        result = agent.spawn(
            subtask.description,
            system_prompt=system_prompt,
            timeout=60,
        )
        subtask.duration = time.time() - start
        subtask.status = "done"
        subtask.result = result
        return result
    except Exception as e:
        subtask.duration = time.time() - start
        subtask.status = "failed"
        subtask.result = f"[超时或失败] {e}"
        return subtask.result


def execute_subtasks_parallel(agent: Agent, subtasks: list[SubTask]) -> list[tuple[str, str]]:
    """并行执行多个子任务

    风险修复 #4：使用 spawn_parallel，受 Semaphore 保护（默认最多 3 并发）
    """
    loader = get_agency_loader()
    tasks = []
    for st in subtasks:
        role = loader.get_role(st.role_name)
        if not role:
            continue
        role_body = role.body[:2000]
        tasks.append({
            "task": st.description,
            "system_prompt": f"[专家角色: {role.emoji} {role.name}]\n{role_body}",
            "timeout": 60,
        })

    if not tasks:
        return []

    results_raw = agent.spawn_parallel(tasks)

    results = []
    for i, raw in enumerate(results_raw):
        if i < len(subtasks):
            subtasks[i].status = "done" if not raw.startswith("[") else "failed"
            subtasks[i].result = raw
            results.append((subtasks[i].role_name_zh, raw))

    return results


def unify_style(agent: Agent, task: str, results: list[tuple[str, str]]) -> str:
    """风格统一

    风险修复 #3：失败时 fallback 到结构化拼接（不丢失内容）
    """
    if not results:
        return "[无结果]"

    # 过滤掉失败的结果
    valid = [(name, r) for name, r in results if not r.startswith("[超时") and not r.startswith("[错误")]
    if not valid:
        return "\n\n".join(f"**{name}**: {r}" for name, r in results)

    # 只有一个有效结果，直接返回
    if len(valid) == 1:
        return valid[0][1]

    parts = "\n\n".join(f"## {name}\n{result}" for name, result in valid)

    prompt = f"""整合以下专家的分析结果为一份统一回答。

任务：{task}

各专家输出：
{parts}

要求：整合所有观点，统一语气，使用 Markdown 格式。"""

    try:
        return agent.spawn(
            prompt,
            system_prompt="你是内容整合专家。",
            timeout=60,
        )
    except Exception:
        # 风格统一失败，fallback 到结构化拼接
        output = f"# {task}\n\n"
        for name, result in valid:
            output += f"## {name} 的分析\n\n{result}\n\n---\n\n"
        return output
