"""Spec 任务系统测试"""
import os, pytest
from agent_core.spec import Spec, SpecData, Task, TaskStatus
from agent_core.agent import Agent


class TestTaskStatus:
    def test_enum_values(self):
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.IN_PROGRESS.value == "in_progress"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"


class TestSpecLoad:
    def test_load_example(self):
        agent = Agent(stream=False)
        spec = Spec(agent)
        data = spec.load("specs/example.md")
        assert data.title == "specs/example.md"
        assert len(data.tasks) >= 3
        assert all(t.status == TaskStatus.PENDING for t in data.tasks)


class TestSpecSave:
    def test_save_and_read(self, tmp_path):
        agent = Agent(stream=False)
        spec = Spec(agent)
        spec.data = SpecData(
            title="测试Spec",
            requirements="测试需求",
            design="测试设计",
            tasks=[Task(id=1, title="任务1", status=TaskStatus.COMPLETED)],
        )
        path = str(tmp_path / "test_spec.md")
        spec.save(path)
        assert os.path.exists(path)
        with open(path) as f:
            content = f.read()
        assert "测试Spec" in content
        assert "✅" in content


class TestSpecData:
    def test_default_values(self):
        data = SpecData()
        assert data.title == ""
        assert data.tasks == []

    def test_task_fields(self):
        task = Task(id=1, title="test", description="desc")
        assert task.status == TaskStatus.PENDING
        assert task.result == ""


class TestSpecEventNames:
    def test_run_emits_correct_events(self):
        """验证 Spec.run() 使用正确的事件名"""
        import inspect
        src = inspect.getsource(Spec.run)
        assert "preTaskExecution" in src
        assert "postTaskExecution" in src
        assert "before_task" not in src
        assert "after_task" not in src
