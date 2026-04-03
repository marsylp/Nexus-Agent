"""Powers 系统测试"""
import os, shutil, tempfile, pytest
from agent_core.powers import PowersManager, Power, _parse_power_md


class TestParsePowerMd:
    def test_parse(self):
        body, desc, kws = _parse_power_md("powers/example-power/POWER.md")
        assert len(body) > 0
        assert len(desc) > 0
        assert len(kws) >= 1


class TestPowersManager:
    def test_discover(self):
        pm = PowersManager(["powers"])
        powers = pm.list_powers()
        assert len(powers) >= 1
        names = [p["name"] for p in powers]
        assert "example-power" in names

    def test_get(self):
        pm = PowersManager(["powers"])
        p = pm.get("example-power")
        assert p is not None
        assert p.has_doc() is True
        assert p.has_steering() is True

    def test_get_nonexistent(self):
        pm = PowersManager(["powers"])
        assert pm.get("nonexistent") is None


class TestPowerActivation:
    def test_activate_deactivate(self):
        pm = PowersManager(["powers"])
        p = pm.activate("example-power")
        assert p is not None
        assert p.activated is True
        assert len(p.doc_content) > 0
        assert len(p.steering_files) >= 1
        activated = pm.get_activated()
        assert len(activated) >= 1
        assert pm.deactivate("example-power") is True
        assert pm.get("example-power").activated is False

    def test_doc_content(self):
        pm = PowersManager(["powers"])
        pm.activate("example-power")
        doc = pm.get_all_doc_content()
        assert "example-power" in doc
        steering = pm.get_all_steering_files()
        assert len(steering) >= 1
        pm.deactivate("example-power")


class TestPowerKeyword:
    def test_find_by_keyword(self):
        pm = PowersManager(["powers"])
        matched = pm.find_by_keyword("这是一个示例")
        assert len(matched) >= 1
        assert matched[0].name == "example-power"


class TestPowerInstallUninstall:
    def test_install_uninstall(self):
        tmpdir = tempfile.mkdtemp()
        try:
            src = os.path.join(tmpdir, "test-power")
            os.makedirs(src)
            with open(os.path.join(src, "POWER.md"), "w") as f:
                f.write("---\ndescription: test\nkeywords: test\n---\n# Test Power")
            target = os.path.join(tmpdir, "installed")
            pm = PowersManager([target])
            p = pm.install(src, target)
            assert p is not None
            assert pm.get("test-power") is not None
            assert pm.uninstall("test-power") is True
            assert pm.get("test-power") is None
        finally:
            shutil.rmtree(tmpdir)
