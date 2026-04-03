"""Steering 系统测试"""
import os, shutil, tempfile, pytest
from agent_core.steering import (
    _parse_front_matter, SteeringFile, SteeringManager, load_steering,
)


class TestFrontMatter:
    def test_parse(self):
        content = "---\ninclusion: fileMatch\nfileMatchPattern: '*.py'\n---\n正文内容"
        meta, body = _parse_front_matter(content)
        assert meta["inclusion"] == "fileMatch"
        assert meta["fileMatchPattern"] == "*.py"
        assert body == "正文内容"

    def test_no_frontmatter(self):
        content = "纯文本内容\n没有 front-matter"
        meta, body = _parse_front_matter(content)
        assert meta == {}
        assert body == content


class TestSteeringFile:
    def test_file_match(self):
        sf = SteeringFile("test.md", "/path/test.md",
                          {"inclusion": "fileMatch", "fileMatchPattern": "*.py"}, "body")
        assert sf.matches_file("main.py") is True
        assert sf.matches_file("test.js") is False
        assert sf.inclusion == "filematch"


class TestSteeringManager:
    def test_always_mode(self):
        mgr = SteeringManager("steering")
        content = mgr.get_active_content()
        assert "default.md" in content or len(content) > 0

    def test_filematch_mode(self):
        mgr = SteeringManager("steering")
        content_py = mgr.get_active_content(context_files=["main.py"])
        content_css = mgr.get_active_content(context_files=["style.css"])
        assert "PEP 8" in content_py or "python_style" in content_py
        assert "PEP 8" not in content_css

    def test_manual_mode(self):
        tmpdir = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmpdir, "manual_test.md"), "w") as f:
                f.write("---\ninclusion: manual\n---\n手动内容")
            mgr = SteeringManager(tmpdir)
            assert "手动内容" not in mgr.get_active_content()
            assert mgr.activate_manual("manual_test.md") is True
            assert "手动内容" in mgr.get_active_content()
        finally:
            shutil.rmtree(tmpdir)

    def test_list_files(self):
        mgr = SteeringManager("steering")
        files = mgr.list_files()
        assert len(files) >= 1
        names = [f["name"] for f in files]
        assert "default.md" in names


class TestLegacyInterface:
    def test_load_steering(self):
        content = load_steering()
        assert isinstance(content, str)
        assert len(content) > 0
