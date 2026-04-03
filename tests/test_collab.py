"""协作模式引擎测试"""
import pytest
from server.collab import (
    _extract_json, SubTask, CollabPlan,
)


class TestExtractJson:
    """风险 #1：JSON 解析加固测试"""

    def test_clean_json(self):
        result = _extract_json('{"summary": "测试", "subtasks": []}')
        assert result is not None
        assert result["summary"] == "测试"

    def test_json_with_markdown_fence(self):
        text = '```json\n{"summary": "测试", "subtasks": []}\n```'
        result = _extract_json(text)
        assert result is not None
        assert result["summary"] == "测试"

    def test_json_with_plain_fence(self):
        text = '```\n{"summary": "测试", "subtasks": []}\n```'
        result = _extract_json(text)
        assert result is not None

    def test_json_with_surrounding_text(self):
        """LLM 在 JSON 前后加了废话"""
        text = '好的，这是分配方案：\n{"summary": "测试", "subtasks": [{"role": "A", "description": "做A"}]}\n希望对你有帮助！'
        result = _extract_json(text)
        assert result is not None
        assert result["summary"] == "测试"
        assert len(result["subtasks"]) == 1

    def test_empty_input(self):
        assert _extract_json("") is None
        assert _extract_json(None) is None

    def test_no_json(self):
        assert _extract_json("这不是 JSON") is None

    def test_invalid_json(self):
        assert _extract_json("{invalid}") is None

    def test_nested_braces(self):
        text = '{"summary": "测试", "subtasks": [{"role": "A", "description": "做 {事情}"}]}'
        result = _extract_json(text)
        assert result is not None

    def test_json_with_newlines(self):
        text = '''```json
{
  "summary": "多行测试",
  "subtasks": [
    {"role": "Frontend Developer", "description": "写前端"},
    {"role": "Backend Architect", "description": "写后端"}
  ]
}
```'''
        result = _extract_json(text)
        assert result is not None
        assert len(result["subtasks"]) == 2


class TestSubTask:
    def test_defaults(self):
        st = SubTask(role_name="test", role_name_zh="测试", emoji="🤖", description="做事")
        assert st.status == "pending"
        assert st.result == ""
        assert st.duration == 0.0


class TestCollabPlan:
    def test_creation(self):
        plan = CollabPlan(task="测试任务", summary="分析")
        assert plan.task == "测试任务"
        assert len(plan.subtasks) == 0

    def test_with_subtasks(self):
        plan = CollabPlan(task="测试", summary="分析", subtasks=[
            SubTask("A", "角色A", "🅰️", "做A"),
            SubTask("B", "角色B", "🅱️", "做B"),
        ])
        assert len(plan.subtasks) == 2
