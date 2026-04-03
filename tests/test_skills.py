"""Skills 测试"""
import os, json, pytest
from agent_core.tools import call_tool
from skills import load_all_skills


class TestSkillLoading:
    def test_auto_load(self):
        skills = load_all_skills()
        assert len(skills) >= 7
        expected = ["code_runner", "data_process", "datetime_skill",
                     "file_ops", "http_request", "system_info", "text_process"]
        for e in expected:
            assert e in skills


class TestDatetime:
    def test_current_time(self):
        result = call_tool("current_time", {})
        assert "2" in result  # 年份


class TestFileOps:
    def test_read_write_list(self, tmp_path):
        path = str(tmp_path / "test.txt")
        r = call_tool("write_file", {"path": path, "content": "hello test"})
        assert "已写入" in r
        r = call_tool("read_file", {"path": path})
        assert "hello test" in r
        r = call_tool("list_dir", {"path": str(tmp_path)})
        assert "test.txt" in r

    def test_delete_file(self, tmp_path):
        path = str(tmp_path / "to_delete.txt")
        call_tool("write_file", {"path": path, "content": "temp"})
        r = call_tool("delete_file", {"path": path})
        assert "已删除" in r
        assert not os.path.exists(path)

    def test_delete_nonexistent(self):
        r = call_tool("delete_file", {"path": "/tmp/nonexistent_mini_agent_test_xyz.txt"})
        assert "不存在" in r


class TestTextProcess:
    def test_json_parse(self):
        r = call_tool("json_parse", {"text": '{"name":"test","value":42}', "path": "name"})
        assert "test" in r

    def test_regex_match(self):
        r = call_tool("regex_match", {"text": "hello 123 world 456", "pattern": r"\d+"})
        assert "123" in r and "456" in r

    def test_encode_decode(self):
        encoded = call_tool("encode_decode", {"text": "hello", "action": "base64_encode"})
        decoded = call_tool("encode_decode", {"text": encoded, "action": "base64_decode"})
        assert decoded == "hello"


class TestDataProcess:
    def test_sort(self):
        r = call_tool("sort_data", {"data": "[3,1,2]"})
        assert json.loads(r) == [1, 2, 3]

    def test_deduplicate(self):
        r = call_tool("deduplicate", {"data": '[1,2,2,3,3,3]'})
        assert "3 项" in r

    def test_csv_parse(self):
        r = call_tool("csv_parse", {"text": "name,age\nAlice,30\nBob,25"})
        assert "Alice" in r and "Bob" in r
