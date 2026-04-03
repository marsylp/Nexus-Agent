"""SubAgentMixin — 子 Agent 管理职责

从 Agent 类拆分出来的子 Agent 派生功能。
通过全局信号量控制最大并发数（默认 3），避免资源耗尽。

三种派生模式：
- spawn(): 同步阻塞，等待子 Agent 完成
- spawn_async(): 异步非阻塞，返回 SubAgentFuture
- spawn_parallel(): 多个子 Agent 并行执行

每个子 Agent 是独立的 Agent 实例，有自己的 memory，
不会污染父 Agent 的对话历史。
"""
from __future__ import annotations
import os, time
from concurrent.futures import ThreadPoolExecutor, as_completed


class SubAgentMixin:
    """子 Agent 管理 Mixin

    要求宿主类提供:
    - self.system_prompt: str
    - self._tools_filter: list[str] | None
    - self._total_tokens: int
    - self._get_scene_model(scene: str) -> tuple[str, str]
    """

    # 全局并发控制信号量
    _sub_agent_semaphore = None
    _MAX_CONCURRENT_SUBAGENTS = int(os.environ.get("MAX_CONCURRENT_SUBAGENTS", "3"))

    @classmethod
    def _get_semaphore(cls):
        """延迟初始化信号量"""
        if cls._sub_agent_semaphore is None:
            import threading
            cls._sub_agent_semaphore = threading.Semaphore(cls._MAX_CONCURRENT_SUBAGENTS)
        return cls._sub_agent_semaphore

    def spawn(self, task: str, system_prompt: str | None = None,
              tools_filter: list[str] | None = None, timeout: int | None = None) -> str:
        """同步派生子 Agent（带并发控制）"""
        sem = self._get_semaphore()
        acquired = sem.acquire(timeout=timeout or 60)
        if not acquired:
            return "[子 Agent] 并发数已满，等待超时"
        try:
            return self._do_spawn(task, system_prompt, tools_filter, timeout)
        finally:
            sem.release()

    def spawn_async(self, task: str, system_prompt: str | None = None,
                    tools_filter: list[str] | None = None,
                    timeout: int | None = None,
                    callback=None) -> "SubAgentFuture":
        """异步派生子 Agent"""
        from agent_core.agent import SubAgentFuture
        future = SubAgentFuture(self, task, system_prompt, tools_filter, timeout, callback)
        future.start()
        return future

    def spawn_parallel(self, tasks: list[dict]) -> list[str]:
        """并行派生多个子 Agent"""
        if not tasks:
            return []
        if len(tasks) == 1:
            t = tasks[0]
            return [self.spawn(t["task"], t.get("system_prompt"), t.get("tools_filter"), t.get("timeout"))]

        results: list[str | None] = [None] * len(tasks)

        def run_one(idx, t):
            try:
                r = self.spawn(t["task"], t.get("system_prompt"), t.get("tools_filter"), t.get("timeout"))
                return idx, r
            except Exception as e:
                return idx, "[子 Agent 错误] {}".format(e)

        with ThreadPoolExecutor(max_workers=min(len(tasks), self._MAX_CONCURRENT_SUBAGENTS)) as pool:
            futures = {pool.submit(run_one, i, t): i for i, t in enumerate(tasks)}
            for future in as_completed(futures):
                idx, result = future.result()
                results[idx] = result

        return results  # type: ignore[return-value]

    def _do_spawn(self, task: str, system_prompt: str | None = None,
                  tools_filter: list[str] | None = None,
                  timeout: int | None = None) -> str:
        """实际的子 Agent 创建和执行"""
        from agent_core.agent import Agent
        scene_prov, scene_model = self._get_scene_model("subagent")
        sub = Agent(
            model=scene_model,
            system_prompt=system_prompt or self.system_prompt,
            stream=False,
            tools_filter=tools_filter or self._tools_filter,
            timeout=timeout,
            parent=self,
            provider=scene_prov,
        )
        result = sub.run(task)
        self._total_tokens += sub._total_tokens
        return result

    def get_sub_agent_status(self) -> dict:
        """获取子 Agent 并发状态"""
        sem = self._get_semaphore()
        available = getattr(sem, '_value', -1)
        return {
            "max_concurrent": self._MAX_CONCURRENT_SUBAGENTS,
            "available_slots": available,
            "active": self._MAX_CONCURRENT_SUBAGENTS - available if available >= 0 else -1,
        }
