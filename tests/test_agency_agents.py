"""Agency Agents 自动匹配系统测试"""
import os
import tempfile
import pytest
from agent_core.agency_agents import (
    AgencyAgentsLoader, AgencyMatcher, AgentRole,
    _parse_front_matter,
)


# ── front-matter 解析 ────────────────────────────────────────

class TestParseFrontMatter:
    def test_with_front_matter(self):
        content = "---\nname: 测试\ndescription: 描述\n---\n正文内容"
        meta, body = _parse_front_matter(content)
        assert meta["name"] == "测试"
        assert meta["description"] == "描述"
        assert body == "正文内容"

    def test_without_front_matter(self):
        content = "纯正文内容"
        meta, body = _parse_front_matter(content)
        assert meta == {}
        assert body == "纯正文内容"

    def test_empty_front_matter(self):
        content = "---\n---\n正文"
        meta, body = _parse_front_matter(content)
        assert meta == {}
        assert body == "正文"

    def test_quoted_values(self):
        content = '---\nname: "带引号"\nemoji: \'🎨\'\n---\nbody'
        meta, body = _parse_front_matter(content)
        assert meta["name"] == "带引号"
        assert meta["emoji"] == "🎨"


# ── Loader 测试 ──────────────────────────────────────────────

class TestAgencyAgentsLoader:
    @pytest.fixture
    def temp_agents_dir(self, tmp_path):
        """创建临时 agency-agents 目录结构"""
        eng_dir = tmp_path / "engineering"
        eng_dir.mkdir()
        design_dir = tmp_path / "design"
        design_dir.mkdir()

        # 创建测试角色文件
        (eng_dir / "engineering-frontend-developer.md").write_text(
            "---\nname: 前端开发者\ndescription: 前端开发专家\nemoji: 🖥️\n---\n"
            "# 前端开发者\n你是前端开发专家，精通 React、Vue。",
            encoding="utf-8",
        )
        (eng_dir / "engineering-security-engineer.md").write_text(
            "---\nname: 安全工程师\ndescription: 安全工程专家\nemoji: 🔒\n---\n"
            "# 安全工程师\n你是安全工程专家，精通威胁建模。",
            encoding="utf-8",
        )
        (design_dir / "design-brand-guardian.md").write_text(
            "---\nname: 品牌守护者\ndescription: 品牌策略专家\nemoji: 🎨\n---\n"
            "# 品牌守护者\n你是品牌策略专家。",
            encoding="utf-8",
        )
        return str(tmp_path)

    def test_scan_finds_roles(self, temp_agents_dir):
        loader = AgencyAgentsLoader(temp_agents_dir)
        assert loader.available
        assert len(loader.roles) == 3

    def test_role_attributes(self, temp_agents_dir):
        loader = AgencyAgentsLoader(temp_agents_dir)
        frontend = loader.get_role("前端开发者")
        assert frontend is not None
        assert frontend.name == "前端开发者"
        assert frontend.emoji == "🖥️"
        assert frontend.category == "engineering"
        assert "前端开发专家" in frontend.body

    def test_list_roles(self, temp_agents_dir):
        loader = AgencyAgentsLoader(temp_agents_dir)
        roles = loader.list_roles()
        assert len(roles) == 3
        names = [r["name"] for r in roles]
        assert "前端开发者" in names
        assert "安全工程师" in names

    def test_get_role_by_filename(self, temp_agents_dir):
        loader = AgencyAgentsLoader(temp_agents_dir)
        role = loader.get_role("engineering-frontend-developer")
        assert role is not None
        assert role.name == "前端开发者"

    def test_empty_dir(self, tmp_path):
        loader = AgencyAgentsLoader(str(tmp_path))
        assert not loader.available
        assert len(loader.roles) == 0

    def test_nonexistent_dir(self):
        loader = AgencyAgentsLoader("/nonexistent/path")
        assert not loader.available

    def test_skip_dirs(self, tmp_path):
        """应跳过 .git、examples 等目录"""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "test.md").write_text("---\nname: git\n---\nbody")
        (tmp_path / "examples").mkdir()
        (tmp_path / "examples" / "test.md").write_text("---\nname: example\n---\nbody")
        loader = AgencyAgentsLoader(str(tmp_path))
        assert len(loader.roles) == 0

    def test_reload(self, temp_agents_dir):
        loader = AgencyAgentsLoader(temp_agents_dir)
        assert len(loader.roles) == 3
        # 添加新文件
        eng_dir = os.path.join(temp_agents_dir, "engineering")
        with open(os.path.join(eng_dir, "engineering-new-role.md"), "w") as f:
            f.write("---\nname: 新角色\n---\n正文")
        loader.reload()
        assert len(loader.roles) == 4


# ── Matcher 测试 ─────────────────────────────────────────────

class TestAgencyMatcher:
    @pytest.fixture
    def matcher(self, tmp_path):
        """创建带测试数据的 matcher"""
        eng_dir = tmp_path / "engineering"
        eng_dir.mkdir()
        design_dir = tmp_path / "design"
        design_dir.mkdir()

        (eng_dir / "engineering-frontend-developer.md").write_text(
            "---\nname: 前端开发者\ndescription: 专精于现代 Web 技术、React/Vue 框架\nemoji: 🖥️\n---\n"
            "# 前端开发者\n你是前端开发专家。",
            encoding="utf-8",
        )
        (eng_dir / "engineering-security-engineer.md").write_text(
            "---\nname: 安全工程师\ndescription: 专精于威胁建模、漏洞评估、安全代码审查\nemoji: 🔒\n---\n"
            "# 安全工程师\n你是安全工程专家。",
            encoding="utf-8",
        )
        (eng_dir / "engineering-mobile-app-builder.md").write_text(
            "---\nname: 移动应用开发者\ndescription: Android/iOS 移动应用开发专家\nemoji: 📱\n---\n"
            "# 移动应用开发者\n你是移动开发专家。",
            encoding="utf-8",
        )
        (design_dir / "design-brand-guardian.md").write_text(
            "---\nname: 品牌守护者\ndescription: 品牌策略与守护专家\nemoji: 🎨\n---\n"
            "# 品牌守护者\n你是品牌策略专家。",
            encoding="utf-8",
        )

        loader = AgencyAgentsLoader(str(tmp_path))
        return AgencyMatcher(loader)

    def test_match_frontend(self, matcher):
        role = matcher.match("帮我写一个 React 组件，实现虚拟列表")
        assert role is not None
        assert role.name == "前端开发者"

    def test_match_security(self, matcher):
        role = matcher.match("检查这个 API 的安全漏洞，有没有 XSS 风险")
        assert role is not None
        assert role.name == "安全工程师"

    def test_match_mobile(self, matcher):
        role = matcher.match("帮我写一个 Android Kotlin Compose 页面")
        assert role is not None
        assert role.name == "移动应用开发者"

    def test_match_brand(self, matcher):
        role = matcher.match("帮我设计一套品牌视觉识别系统")
        assert role is not None
        assert role.name == "品牌守护者"

    def test_no_match_for_greeting(self, matcher):
        """简单问候不应匹配任何角色"""
        role = matcher.match("你好")
        # 可能返回 None 或之前的角色
        # 关键是不会错误匹配
        assert role is None or matcher._current_score < 0.5

    def test_switch_prevention(self, matcher):
        """防抖：不应频繁切换角色"""
        role1 = matcher.match("帮我写一个 Vue 前端页面")
        assert role1 is not None
        assert role1.name == "前端开发者"

        # 弱信号不应导致切换
        role2 = matcher.match("这个页面的样式有点问题")
        assert role2.name == "前端开发者"  # 应保持前端角色

    def test_strong_signal_switches(self, matcher):
        """强信号应该能切换角色"""
        matcher.match("帮我写一个 React 组件")
        # 重置后再匹配，模拟新话题
        matcher.reset()
        role = matcher.match("检查这个系统的安全漏洞，做一次完整的渗透测试和威胁建模")
        assert role is not None
        assert role.name == "安全工程师"

    def test_reset(self, matcher):
        matcher.match("帮我写一个 React 组件")
        assert matcher.current_role is not None
        matcher.reset()
        assert matcher.current_role is None

    def test_match_returns_same_role_for_same_topic(self, matcher):
        """同一话题应持续返回同一角色"""
        r1 = matcher.match("帮我写一个 React 组件")
        r2 = matcher.match("给这个组件加上 TypeScript 类型")
        r3 = matcher.match("再加一个 CSS 动画效果")
        assert r1.name == r2.name == r3.name == "前端开发者"


# ── 集成测试 ─────────────────────────────────────────────────

class TestAgencyIntegration:
    """测试与真实 agency-agents 目录的集成（如果存在）"""

    def test_real_agents_dir(self):
        """如果本地有 agency-agents 目录，测试加载"""
        loader = AgencyAgentsLoader()
        if not loader.available:
            pytest.skip("未找到 agency-agents 目录")

        assert len(loader.roles) > 10  # 应该有很多角色
        # 检查关键角色存在
        names = [r.name for r in loader.roles]
        # 至少应该有一些工程类角色
        eng_roles = [r for r in loader.roles if r.category == "engineering"]
        assert len(eng_roles) > 5

    def test_real_matching(self):
        """如果本地有 agency-agents 目录，测试匹配"""
        loader = AgencyAgentsLoader()
        if not loader.available:
            pytest.skip("未找到 agency-agents 目录")

        matcher = AgencyMatcher(loader)

        # 前端问题应匹配前端角色
        role = matcher.match("帮我用 React 写一个带虚拟滚动的表格组件")
        assert role is not None
        assert "前端" in role.name or "frontend" in role.name.lower()
