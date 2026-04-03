"""Spec 任务系统 — 需求→设计→任务分解→逐步执行

用法:
    spec = Spec(agent)
    spec.load("specs/my_feature.md")  # 或 spec.create("实现用户登录功能")
    spec.run()                         # 逐步执行所有任务
"""
from __future__ import annotations
import json, os, re
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    id: int
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    result: str = ""


@dataclass
class SpecData:
    title: str = ""
    requirements: str = ""
    design: str = ""
    tasks: list[Task] = field(default_factory=list)


class Spec:
    """Spec 任务管理器"""

    def __init__(self, agent):
        self.agent = agent
        self.data = SpecData()

    def create(self, requirement: str) -> SpecData:
        """从需求描述自动生成 Spec（需求→设计→任务列表）"""
        print("  📋 生成 Spec ...")

        # 第一步：需求分析
        self.data.title = requirement[:50]
        self.data.requirements = requirement

        # 第二步：让 LLM 生成设计和任务分解
        prompt = f"""请根据以下需求，生成技术设计和实现任务列表。

需求: {requirement}

请严格按以下 JSON 格式返回:
{{
  "design": "简要技术设计（2-3句话）",
  "tasks": [
    {{"title": "任务标题", "description": "具体要做什么"}},
    ...
  ]
}}

只返回 JSON，不要其他内容。"""

        result = self.agent.spawn(prompt, system_prompt="你是技术架构师，擅长任务分解。只返回 JSON。")

        try:
            # 提取 JSON
            json_match = re.search(r'\{[\s\S]*\}', result)
            if json_match:
                parsed = json.loads(json_match.group())
                self.data.design = parsed.get("design", "")
                for i, t in enumerate(parsed.get("tasks", [])):
                    self.data.tasks.append(Task(
                        id=i + 1,
                        title=t.get("title", f"任务{i+1}"),
                        description=t.get("description", ""),
                    ))
        except (json.JSONDecodeError, Exception) as e:
            print(f"  ⚠️  Spec 解析失败: {e}")
            self.data.tasks.append(Task(id=1, title=requirement, description=requirement))

        self._print_spec()
        return self.data

    def load(self, path: str) -> SpecData:
        """从 Markdown 文件加载 Spec"""
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        self.data.title = path
        sections = re.split(r'^##\s+', content, flags=re.MULTILINE)

        for section in sections:
            lines = section.strip().split("\n", 1)
            if len(lines) < 2:
                continue
            heading = lines[0].strip().lower()
            body = lines[1].strip()

            if "需求" in heading or "requirement" in heading:
                self.data.requirements = body
            elif "设计" in heading or "design" in heading:
                self.data.design = body
            elif "任务" in heading or "task" in heading:
                for i, line in enumerate(body.split("\n")):
                    line = re.sub(r'^[-*\d.]+\s*', '', line).strip()
                    if line:
                        self.data.tasks.append(Task(id=i+1, title=line))

        self._print_spec()
        return self.data

    def run(self) -> list[Task]:
        """逐步执行所有任务"""
        print(f"\n  🚀 开始执行 Spec: {self.data.title}")
        print(f"  共 {len(self.data.tasks)} 个任务\n")

        context = f"项目需求: {self.data.requirements}\n技术设计: {self.data.design}"

        for task in self.data.tasks:
            if task.status == TaskStatus.COMPLETED:
                continue

            task.status = TaskStatus.IN_PROGRESS
            print(f"  ── 任务 {task.id}: {task.title} ──")

            self.agent._emit("preTaskExecution", task=task)

            prompt = f"""{context}

当前任务: {task.title}
{task.description}

请完成这个任务。"""

            try:
                result = self.agent.run(prompt)
                task.result = result
                task.status = TaskStatus.COMPLETED
                print(f"  ✅ 任务 {task.id} 完成\n")
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.result = str(e)
                print(f"  ❌ 任务 {task.id} 失败: {e}\n")

            self.agent._emit("postTaskExecution", task=task)

        done = sum(1 for t in self.data.tasks if t.status == TaskStatus.COMPLETED)
        print(f"  📊 完成 {done}/{len(self.data.tasks)} 个任务")
        return self.data.tasks

    def save(self, path: str):
        """保存 Spec 到 Markdown 文件"""
        lines = [f"# {self.data.title}\n"]
        if self.data.requirements:
            lines.append(f"## 需求\n{self.data.requirements}\n")
        if self.data.design:
            lines.append(f"## 设计\n{self.data.design}\n")
        lines.append("## 任务")
        for t in self.data.tasks:
            status = "✅" if t.status == TaskStatus.COMPLETED else "⬜"
            lines.append(f"- {status} {t.title}")
            if t.description:
                lines.append(f"  {t.description}")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"  💾 Spec 已保存到 {path}")

    def _print_spec(self):
        print(f"\n  📋 Spec: {self.data.title}")
        if self.data.design:
            print(f"  设计: {self.data.design[:100]}")
        print(f"  任务 ({len(self.data.tasks)}):")
        for t in self.data.tasks:
            print(f"    {t.id}. {t.title}")
        print()
