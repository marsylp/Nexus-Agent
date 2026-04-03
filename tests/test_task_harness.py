"""长任务控制框架测试 — 对标 Anthropic "Effective Harnesses for Long-Running Agents"

覆盖 17 项修改点:
#1  TaskHarness 核心类
#2  init.sh 生成
#3  feature_list.json 生成
#4  progress.txt 创建与管理
#5  初始 git commit
#6  Session 启动 Get Up to Speed
#7  单 feature 增量执行
#8  环境清洁退出
#9  Feature 状态管理
#10 验证引擎
#11 健康检查 Smoke Test
#12 Progress 结构化读写
#13 Git 历史集成
#14 与 Agent.run() 集成
#15 /task CLI 命令
#16 Prompt 模板
#17 可配置验证策略
"""
import json, os, time, tempfile, shutil
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from agent_core.task_harness import (
    Feature, FeatureCategory, FeatureManager, ProgressTracker, ProgressEntry,
    GitManager, VerificationEngine, TaskHarness, TaskConfig,
    INITIALIZER_PROMPT, CODING_AGENT_PROMPT, VERIFICATION_PROMPT,
)


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="task_harness_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_features():
    return [
        Feature(id=1, category="functional", description="用户登录",
                steps=["打开登录页", "输入用户名密码", "点击登录", "验证跳转"], priority=1),
        Feature(id=2, category="functional", description="用户注册",
                steps=["打开注册页", "填写信息", "提交"], priority=1),
        Feature(id=3, category="ui", description="响应式布局",
                steps=["缩小窗口", "检查布局适配"], priority=2),
        Feature(id=4, category="performance", description="页面加载 < 2s",
                steps=["打开首页", "检查加载时间"], priority=3),
    ]


@pytest.fixture
def feature_mgr(tmp_dir, sample_features):
    mgr = FeatureManager(os.path.join(tmp_dir, "feature_list.json"))
    mgr.set_features(sample_features)
    return mgr


@pytest.fixture
def progress_tracker(tmp_dir):
    tracker = ProgressTracker(os.path.join(tmp_dir, "task_progress.txt"))
    tracker.init_file("测试需求")
    return tracker


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.spawn.return_value = "任务完成 PASS"
    agent.run.return_value = "代码已实现"
    agent._emit.return_value = True
    return agent


@pytest.fixture
def task_config(tmp_dir):
    return TaskConfig(
        project_dir=tmp_dir,
        requirement="构建一个用户管理系统",
    )



# ══════════════════════════════════════════════════════════════
# #9 Feature 状态管理
# ══════════════════════════════════════════════════════════════

class TestFeature:
    """Feature 数据结构"""

    def test_feature_to_dict(self):
        ft = Feature(id=1, category="functional", description="登录",
                     steps=["步骤1"], priority=1)
        d = ft.to_dict()
        assert d["id"] == 1
        assert d["description"] == "登录"
        assert d["passes"] is False

    def test_feature_from_dict(self):
        d = {"id": 2, "category": "ui", "description": "布局",
             "steps": ["检查"], "passes": True, "priority": 2}
        ft = Feature.from_dict(d)
        assert ft.id == 2
        assert ft.passes is True

    def test_feature_from_dict_extra_fields(self):
        """忽略未知字段"""
        d = {"id": 1, "category": "functional", "description": "x",
             "steps": [], "unknown_field": "ignored"}
        ft = Feature.from_dict(d)
        assert ft.id == 1

    def test_feature_category_enum(self):
        assert FeatureCategory.FUNCTIONAL == "functional"
        assert FeatureCategory.SECURITY == "security"


class TestFeatureManager:
    """Feature 列表管理 (#3 #9)"""

    def test_save_and_load(self, feature_mgr, tmp_dir):
        """保存后重新加载"""
        mgr2 = FeatureManager(os.path.join(tmp_dir, "feature_list.json"))
        features = mgr2.load()
        assert len(features) == 4
        assert features[0].description == "用户登录"

    def test_mark_passed(self, feature_mgr):
        """标记 feature 通过"""
        assert feature_mgr.mark_passed(1, "S001")
        feature_mgr.load()
        ft = [f for f in feature_mgr.features if f.id == 1][0]
        assert ft.passes is True
        assert ft.session_id == "S001"
        assert ft.verified_at != ""

    def test_mark_failed(self, feature_mgr):
        """标记 feature 失败（回退）"""
        feature_mgr.mark_passed(1, "S001")
        feature_mgr.mark_failed(1)
        feature_mgr.load()
        ft = [f for f in feature_mgr.features if f.id == 1][0]
        assert ft.passes is False
        assert ft.verified_at == ""

    def test_mark_nonexistent(self, feature_mgr):
        """标记不存在的 feature"""
        assert feature_mgr.mark_passed(999) is False

    def test_get_next_feature(self, feature_mgr):
        """获取下一个待完成 feature（按优先级）"""
        nxt = feature_mgr.get_next_feature()
        assert nxt is not None
        assert nxt.priority == 1  # 最高优先级

    def test_get_next_feature_skips_passed(self, feature_mgr):
        """跳过已完成的 feature"""
        feature_mgr.mark_passed(1)
        feature_mgr.mark_passed(2)
        nxt = feature_mgr.get_next_feature()
        assert nxt.id == 3  # 下一个未完成的

    def test_get_next_feature_all_done(self, feature_mgr):
        """所有 feature 都完成"""
        for ft in feature_mgr.features:
            feature_mgr.mark_passed(ft.id)
        assert feature_mgr.get_next_feature() is None

    def test_get_stats(self, feature_mgr):
        """统计信息"""
        stats = feature_mgr.get_stats()
        assert stats["total"] == 4
        assert stats["passed"] == 0
        assert stats["remaining"] == 4
        assert stats["progress_pct"] == 0.0

        feature_mgr.mark_passed(1)
        stats = feature_mgr.get_stats()
        assert stats["passed"] == 1
        assert stats["progress_pct"] == 25.0

    def test_validate_integrity_ok(self, feature_mgr, sample_features):
        """完整性验证 — 正常情况"""
        errors = feature_mgr.validate_integrity(sample_features)
        assert errors == []

    def test_validate_integrity_deleted(self, feature_mgr, sample_features):
        """完整性验证 — feature 被删除"""
        feature_mgr._features = feature_mgr._features[:2]
        feature_mgr.save()
        feature_mgr.load()
        errors = feature_mgr.validate_integrity(sample_features)
        assert any("被删除" in e for e in errors)

    def test_validate_integrity_modified(self, feature_mgr, sample_features):
        """完整性验证 — description 被修改"""
        import copy
        original = copy.deepcopy(sample_features)
        feature_mgr._features[0].description = "被篡改的描述"
        errors = feature_mgr.validate_integrity(original)
        assert any("description 被修改" in e for e in errors)

    def test_validate_integrity_steps_modified(self, feature_mgr, sample_features):
        """完整性验证 — steps 被修改"""
        import copy
        original = copy.deepcopy(sample_features)
        feature_mgr._features[0].steps = ["被篡改的步骤"]
        errors = feature_mgr.validate_integrity(original)
        assert any("steps 被修改" in e for e in errors)

    def test_empty_feature_list(self, tmp_dir):
        """空 feature 列表"""
        mgr = FeatureManager(os.path.join(tmp_dir, "empty.json"))
        features = mgr.load()
        assert features == []
        assert mgr.get_stats()["total"] == 0

    def test_json_format_integrity(self, feature_mgr, tmp_dir):
        """JSON 格式正确性 — 对标 Anthropic 用 JSON 而非 Markdown"""
        path = os.path.join(tmp_dir, "feature_list.json")
        with open(path, "r") as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert all("description" in item for item in data)
        assert all("passes" in item for item in data)
        assert all("steps" in item for item in data)



# ══════════════════════════════════════════════════════════════
# #4 #12 Progress 管理
# ══════════════════════════════════════════════════════════════

class TestProgressTracker:
    """进度日志 (#4 #12)"""

    def test_init_file(self, progress_tracker, tmp_dir):
        """初始化进度文件"""
        path = os.path.join(tmp_dir, "task_progress.txt")
        assert os.path.exists(path)
        with open(path) as f:
            content = f.read()
        assert "测试需求" in content

    def test_append_and_load(self, progress_tracker):
        """追加并加载进度"""
        progress_tracker.append("S001", "init", summary="初始化完成")
        progress_tracker.append("S001", "feature_start", feature_id=1, summary="开始登录")
        progress_tracker.append("S001", "feature_done", feature_id=1, summary="登录完成")

        entries = progress_tracker.load()
        assert len(entries) == 3
        assert entries[0].action == "init"
        assert entries[1].feature_id == 1
        assert entries[2].action == "feature_done"

    def test_get_recent(self, progress_tracker):
        """获取最近 N 条"""
        for i in range(15):
            progress_tracker.append("S001", "test", feature_id=i)
        entries = progress_tracker.get_recent(5)
        assert len(entries) == 5

    def test_get_summary(self, progress_tracker):
        """生成摘要"""
        progress_tracker.append("S001", "init", summary="初始化")
        progress_tracker.append("S001", "feature_done", feature_id=1, summary="登录完成")
        progress_tracker.append("S001", "bug_fix", summary="修复空指针")

        summary = progress_tracker.get_summary()
        assert "初始化" in summary
        assert "Feature #1" in summary
        assert "修复" in summary

    def test_empty_summary(self, tmp_dir):
        """空进度摘要"""
        tracker = ProgressTracker(os.path.join(tmp_dir, "empty.txt"))
        assert "无历史" in tracker.get_summary()

    def test_append_with_git_commit(self, progress_tracker):
        """带 git commit hash 的进度"""
        progress_tracker.append("S001", "feature_done", feature_id=1,
                                summary="完成", git_commit="abc1234")
        entries = progress_tracker.load()
        # git commit 信息在 summary 后面
        assert len(entries) >= 1



# ══════════════════════════════════════════════════════════════
# #5 #8 #13 Git 集成
# ══════════════════════════════════════════════════════════════

class TestGitManager:
    """Git 操作 (#5 #8 #13)"""

    def test_init_repo(self, tmp_dir):
        git = GitManager(tmp_dir)
        assert git.init() is True
        assert git.is_repo() is True

    def test_commit(self, tmp_dir):
        git = GitManager(tmp_dir)
        git.init()
        # 创建文件
        with open(os.path.join(tmp_dir, "test.txt"), "w") as f:
            f.write("hello")
        ok, hash_val = git.commit("test commit")
        assert ok is True
        assert len(hash_val) > 0

    def test_log(self, tmp_dir):
        git = GitManager(tmp_dir)
        git.init()
        with open(os.path.join(tmp_dir, "a.txt"), "w") as f:
            f.write("a")
        git.commit("first")
        log = git.log(5)
        assert "first" in log

    def test_has_changes(self, tmp_dir):
        git = GitManager(tmp_dir)
        git.init()
        with open(os.path.join(tmp_dir, "x.txt"), "w") as f:
            f.write("x")
        git.commit("init")
        assert git.has_changes() is False
        with open(os.path.join(tmp_dir, "y.txt"), "w") as f:
            f.write("y")
        assert git.has_changes() is True

    def test_get_current_hash(self, tmp_dir):
        git = GitManager(tmp_dir)
        git.init()
        with open(os.path.join(tmp_dir, "z.txt"), "w") as f:
            f.write("z")
        git.commit("init")
        h = git.get_current_hash()
        assert len(h) >= 7

    def test_not_a_repo(self, tmp_dir):
        git = GitManager(tmp_dir)
        assert git.is_repo() is False
        assert git.log() == ""

    def test_double_init(self, tmp_dir):
        git = GitManager(tmp_dir)
        assert git.init() is True
        assert git.init() is True  # 幂等



# ══════════════════════════════════════════════════════════════
# #10 #11 #17 验证引擎
# ══════════════════════════════════════════════════════════════

class TestVerificationEngine:
    """验证引擎 (#10 #11 #17)"""

    def test_agent_verify_pass(self, mock_agent, task_config):
        """Agent 自验证 — 通过"""
        mock_agent.spawn.return_value = "步骤1 ✅\n步骤2 ✅\n最终结论: PASS"
        engine = VerificationEngine(mock_agent, task_config)
        ft = Feature(id=1, category="functional", description="登录",
                     steps=["打开页面", "输入密码"])
        passed, detail = engine.verify_feature(ft)
        assert passed is True

    def test_agent_verify_fail(self, mock_agent, task_config):
        """Agent 自验证 — 失败"""
        mock_agent.spawn.return_value = "步骤1 ✅\n步骤2 ❌ 按钮不存在\n最终结论: FAIL"
        engine = VerificationEngine(mock_agent, task_config)
        ft = Feature(id=1, category="functional", description="登录",
                     steps=["打开页面", "点击按钮"])
        passed, detail = engine.verify_feature(ft)
        assert passed is False

    def test_smoke_test_no_command(self, mock_agent, task_config):
        """健康检查 — 无命令时跳过"""
        engine = VerificationEngine(mock_agent, task_config)
        ok, msg = engine.smoke_test()
        assert ok is True
        assert "跳过" in msg

    def test_smoke_test_with_command_pass(self, mock_agent, tmp_dir):
        """健康检查 — 命令通过"""
        config = TaskConfig(project_dir=tmp_dir, requirement="test",
                            smoke_test_command="echo ok")
        engine = VerificationEngine(mock_agent, config)
        ok, msg = engine.smoke_test()
        assert ok is True

    def test_smoke_test_with_command_fail(self, mock_agent, tmp_dir):
        """健康检查 — 命令失败"""
        config = TaskConfig(project_dir=tmp_dir, requirement="test",
                            smoke_test_command="exit 1")
        engine = VerificationEngine(mock_agent, config)
        ok, msg = engine.smoke_test()
        assert ok is False

    def test_custom_verify_command(self, mock_agent, tmp_dir):
        """可配置验证命令 (#17)"""
        config = TaskConfig(project_dir=tmp_dir, requirement="test",
                            verify_command="echo 'testing feature {feature_id}'")
        engine = VerificationEngine(mock_agent, config)
        # Agent 也返回 PASS
        mock_agent.spawn.return_value = "PASS"
        ft = Feature(id=42, category="functional", description="test",
                     steps=["验证"])
        passed, detail = engine.verify_feature(ft)
        assert passed is True

    def test_verify_command_fail_blocks_pass(self, mock_agent, tmp_dir):
        """验证命令失败时即使 Agent 说 PASS 也不通过"""
        config = TaskConfig(project_dir=tmp_dir, requirement="test",
                            verify_command="exit 1")
        engine = VerificationEngine(mock_agent, config)
        mock_agent.spawn.return_value = "PASS"
        ft = Feature(id=1, category="functional", description="test",
                     steps=["验证"])
        passed, detail = engine.verify_feature(ft)
        assert passed is False



# ══════════════════════════════════════════════════════════════
# #16 Prompt 模板
# ══════════════════════════════════════════════════════════════

class TestPromptTemplates:
    """Prompt 模板 (#16)"""

    def test_initializer_prompt_has_placeholders(self):
        assert "{requirement}" in INITIALIZER_PROMPT
        assert "{project_dir}" in INITIALIZER_PROMPT
        assert "feature_list" in INITIALIZER_PROMPT.lower() or "feature" in INITIALIZER_PROMPT.lower()

    def test_coding_agent_prompt_has_placeholders(self):
        assert "{project_dir}" in CODING_AGENT_PROMPT
        assert "{progress_file}" in CODING_AGENT_PROMPT
        assert "{feature_file}" in CODING_AGENT_PROMPT

    def test_coding_agent_prompt_incremental_rules(self):
        """Coding Agent prompt 包含增量开发规则"""
        assert "一次只做一个" in CODING_AGENT_PROMPT or "增量" in CODING_AGENT_PROMPT

    def test_coding_agent_prompt_prohibitions(self):
        """Coding Agent prompt 包含禁止规则"""
        assert "禁止" in CODING_AGENT_PROMPT or "不要删除" in CODING_AGENT_PROMPT

    def test_verification_prompt_has_placeholders(self):
        assert "{feature_description}" in VERIFICATION_PROMPT
        assert "{verification_steps}" in VERIFICATION_PROMPT

    def test_initializer_prompt_format(self):
        """Initializer prompt 可以正确格式化"""
        formatted = INITIALIZER_PROMPT.format(
            requirement="测试需求",
            project_dir="/tmp/test",
            min_features=10,
            max_features=50,
        )
        assert "测试需求" in formatted
        assert "/tmp/test" in formatted

    def test_coding_prompt_format(self):
        """Coding prompt 可以正确格式化"""
        formatted = CODING_AGENT_PROMPT.format(
            project_dir="/tmp/test",
            progress_file="task_progress.txt",
            feature_file="feature_list.json",
            init_script="init.sh",
            smoke_test_section="",
        )
        assert "/tmp/test" in formatted



# ══════════════════════════════════════════════════════════════
# #1 #6 #7 #8 #14 TaskHarness 核心
# ══════════════════════════════════════════════════════════════

class TestTaskHarness:
    """TaskHarness 核心调度器"""

    def test_not_initialized(self, mock_agent, task_config):
        """未初始化状态"""
        harness = TaskHarness(mock_agent, task_config)
        assert harness.is_initialized() is False

    def test_initialize_creates_files(self, mock_agent, task_config):
        """初始化创建必要文件 (#1 #2 #3 #4 #5)"""
        # Mock agent 返回包含 feature JSON 的输出
        features_json = json.dumps([
            {"id": 1, "category": "functional", "description": "登录功能",
             "steps": ["打开登录页", "输入密码", "点击登录"], "priority": 1},
            {"id": 2, "category": "functional", "description": "注册功能",
             "steps": ["打开注册页", "填写信息"], "priority": 1},
        ])
        mock_agent.spawn.return_value = "```json\n{}\n```".format(features_json)

        harness = TaskHarness(mock_agent, task_config)
        result = harness.initialize()

        # 验证文件创建
        assert result["features"] == 2
        assert os.path.exists(os.path.join(
            task_config.project_dir, "feature_list.json"))
        assert os.path.exists(os.path.join(
            task_config.project_dir, "task_progress.txt"))
        assert os.path.exists(os.path.join(
            task_config.project_dir, "init.sh"))
        assert harness.is_initialized() is True

    def test_initialize_fallback_single_feature(self, mock_agent, task_config):
        """初始化失败时回退到单个 feature"""
        mock_agent.spawn.return_value = "无法解析的输出"
        harness = TaskHarness(mock_agent, task_config)
        result = harness.initialize()
        assert result["features"] == 1  # 回退

    def test_run_session_not_initialized(self, mock_agent, task_config):
        """未初始化时运行 session"""
        harness = TaskHarness(mock_agent, task_config)
        result = harness.run_session()
        assert result.get("error") == "not_initialized"

    def test_run_session_completes_feature(self, mock_agent, task_config):
        """运行 session 完成一个 feature (#6 #7 #8)"""
        # 先初始化
        features_json = json.dumps([
            {"id": 1, "category": "functional", "description": "登录",
             "steps": ["验证登录"], "priority": 1},
        ])
        mock_agent.spawn.return_value = "```json\n{}\n```".format(features_json)
        harness = TaskHarness(mock_agent, task_config)
        harness.initialize()

        # 运行 session — Agent 验证返回 PASS
        mock_agent.spawn.return_value = "所有步骤 ✅ PASS"
        mock_agent.run.return_value = "代码已实现"
        result = harness.run_session()

        assert result["feature_id"] == 1
        assert result["passed"] is True

    def test_run_session_all_done(self, mock_agent, task_config):
        """所有 feature 完成后 session 返回 all_done"""
        features_json = json.dumps([
            {"id": 1, "category": "functional", "description": "唯一功能",
             "steps": ["验证"], "priority": 1},
        ])
        mock_agent.spawn.return_value = "```json\n{}\n```".format(features_json)
        harness = TaskHarness(mock_agent, task_config)
        harness.initialize()

        # 手动标记完成
        harness.feature_mgr.mark_passed(1, "test")
        result = harness.run_session()
        assert result["summary"] == "all_done"

    def test_status(self, mock_agent, task_config):
        """状态查询"""
        features_json = json.dumps([
            {"id": 1, "category": "functional", "description": "功能A",
             "steps": ["验证"], "priority": 1},
            {"id": 2, "category": "functional", "description": "功能B",
             "steps": ["验证"], "priority": 2},
        ])
        mock_agent.spawn.return_value = "```json\n{}\n```".format(features_json)
        harness = TaskHarness(mock_agent, task_config)
        harness.initialize()

        status = harness.status()
        assert status["initialized"] is True
        assert status["stats"]["total"] == 2
        assert status["next_feature"] is not None

    def test_get_up_to_speed(self, mock_agent, task_config):
        """Get Up to Speed 流程 (#6)"""
        features_json = json.dumps([
            {"id": 1, "category": "functional", "description": "功能",
             "steps": ["验证"], "priority": 1},
        ])
        mock_agent.spawn.return_value = "```json\n{}\n```".format(features_json)
        harness = TaskHarness(mock_agent, task_config)
        harness.initialize()
        harness.feature_mgr.load()
        harness.progress.load()

        context = harness._get_up_to_speed()
        assert "Feature 状态" in context
        assert "待完成" in context

    def test_clean_exit_with_git(self, mock_agent, task_config):
        """清洁退出 — git commit (#8)"""
        harness = TaskHarness(mock_agent, task_config)
        harness.git.init()
        # 创建变更
        with open(os.path.join(task_config.project_dir, "code.py"), "w") as f:
            f.write("print('hello')")

        ft = Feature(id=1, category="functional", description="测试功能",
                     steps=["验证"])
        commit_hash = harness._clean_exit(ft, verified=True)
        assert len(commit_hash) > 0

    def test_parse_features_from_json(self, mock_agent, task_config):
        """从 Agent 输出解析 feature 列表"""
        harness = TaskHarness(mock_agent, task_config)
        output = '''这是分析结果:
```json
[
  {"id": 1, "category": "functional", "description": "用户登录", "steps": ["步骤1"], "priority": 1},
  {"id": 2, "category": "ui", "description": "响应式", "steps": ["步骤1"], "priority": 2}
]
```'''
        features = harness._parse_features_from_output(output)
        assert len(features) == 2
        assert features[0].description == "用户登录"

    def test_parse_features_fallback(self, mock_agent, task_config):
        """无法解析时返回空列表"""
        harness = TaskHarness(mock_agent, task_config)
        features = harness._parse_features_from_output("无法解析的文本")
        assert features == []

    def test_generate_init_script(self, mock_agent, task_config):
        """生成 init.sh (#2)"""
        harness = TaskHarness(mock_agent, task_config)
        output = '''```bash
#!/bin/bash
echo "启动项目"
pip install -r requirements.txt
```'''
        harness._generate_init_script(output)
        init_path = os.path.join(task_config.project_dir, "init.sh")
        assert os.path.exists(init_path)
        assert os.access(init_path, os.X_OK)  # 可执行

    def test_generate_init_script_default(self, mock_agent, task_config):
        """无法提取时生成默认 init.sh"""
        harness = TaskHarness(mock_agent, task_config)
        harness._generate_init_script("没有代码块的输出")
        init_path = os.path.join(task_config.project_dir, "init.sh")
        assert os.path.exists(init_path)
        with open(init_path) as f:
            content = f.read()
        assert "#!/bin/bash" in content



# ══════════════════════════════════════════════════════════════
# #1 TaskConfig
# ══════════════════════════════════════════════════════════════

class TestTaskConfig:
    """任务配置"""

    def test_default_config(self, tmp_dir):
        config = TaskConfig(project_dir=tmp_dir, requirement="测试")
        assert config.init_script == "init.sh"
        assert config.feature_file == "feature_list.json"
        assert config.progress_file == "task_progress.txt"
        assert config.auto_commit is True
        assert config.max_features_per_session == 1

    def test_custom_config(self, tmp_dir):
        config = TaskConfig(
            project_dir=tmp_dir, requirement="测试",
            verify_command="pytest", smoke_test_command="curl localhost:8080",
            auto_commit=False, max_features_per_session=3,
        )
        assert config.verify_command == "pytest"
        assert config.auto_commit is False


# ══════════════════════════════════════════════════════════════
# 集成: run_all 连续执行
# ══════════════════════════════════════════════════════════════

class TestRunAll:
    """连续运行多个 session"""

    def test_run_all_completes(self, mock_agent, task_config):
        """run_all 连续完成所有 feature"""
        features_json = json.dumps([
            {"id": 1, "category": "functional", "description": "F1",
             "steps": ["验证"], "priority": 1},
            {"id": 2, "category": "functional", "description": "F2",
             "steps": ["验证"], "priority": 1},
        ])
        mock_agent.spawn.return_value = "```json\n{}\n```".format(features_json)

        harness = TaskHarness(mock_agent, task_config)
        harness.initialize()

        # 后续 session 中 Agent 验证返回 PASS
        mock_agent.spawn.return_value = "PASS"
        mock_agent.run.return_value = "done"

        result = harness.run_all(max_sessions=10)
        assert result["stats"]["passed"] == 2
        assert result["sessions"] <= 10

    def test_run_all_max_sessions_limit(self, mock_agent, task_config):
        """run_all 受 max_sessions 限制"""
        features_json = json.dumps([
            {"id": i, "category": "functional", "description": f"F{i}",
             "steps": ["验证"], "priority": 1}
            for i in range(1, 20)
        ])
        mock_agent.spawn.return_value = "```json\n{}\n```".format(features_json)

        harness = TaskHarness(mock_agent, task_config)
        harness.initialize()

        # Agent 验证总是 FAIL
        mock_agent.spawn.return_value = "FAIL 验证失败"
        mock_agent.run.return_value = "done"

        result = harness.run_all(max_sessions=3)
        assert result["sessions"] == 3


# ══════════════════════════════════════════════════════════════
# #14 与 Agent 集成
# ══════════════════════════════════════════════════════════════

class TestAgentIntegration:
    """与 Agent.run() 集成 (#14)"""

    def test_harness_uses_agent_spawn(self, mock_agent, task_config):
        """TaskHarness 使用 agent.spawn 调用子 Agent"""
        features_json = json.dumps([
            {"id": 1, "category": "functional", "description": "测试",
             "steps": ["验证"], "priority": 1},
        ])
        mock_agent.spawn.return_value = "```json\n{}\n```".format(features_json)
        harness = TaskHarness(mock_agent, task_config)
        harness.initialize()
        assert mock_agent.spawn.called

    def test_harness_uses_agent_run(self, mock_agent, task_config):
        """Coding session 使用 agent.run"""
        features_json = json.dumps([
            {"id": 1, "category": "functional", "description": "测试",
             "steps": ["验证"], "priority": 1},
        ])
        mock_agent.spawn.return_value = "```json\n{}\n```".format(features_json)
        harness = TaskHarness(mock_agent, task_config)
        harness.initialize()

        mock_agent.spawn.return_value = "PASS"
        mock_agent.run.return_value = "实现完成"
        harness.run_session()
        assert mock_agent.run.called


# ══════════════════════════════════════════════════════════════
# #15 版本号
# ══════════════════════════════════════════════════════════════

class TestVersion:
    def test_version_bump(self):
        from agent_core import __version__
        assert __version__ == "0.0.2"


# ══════════════════════════════════════════════════════════════
# Findings 系统（借鉴 Planning with Files）
# ══════════════════════════════════════════════════════════════

class TestFindings:
    """Findings 记录与查询 — 借鉴 Planning with Files 的 findings.md 概念"""

    def test_record_finding(self, progress_tracker):
        """记录 finding 条目"""
        progress_tracker.append("S001", "finding", feature_id=1,
                                summary="SQLite WAL 模式支持读写并发")
        entries = progress_tracker.load()
        findings = [e for e in entries if e.action == "finding"]
        assert len(findings) == 1
        assert findings[0].summary == "SQLite WAL 模式支持读写并发"
        assert findings[0].feature_id == 1

    def test_get_findings_empty(self, progress_tracker):
        """无 findings 时返回空列表"""
        progress_tracker.load()
        assert progress_tracker.get_findings() == []

    def test_get_findings_filters_correctly(self, progress_tracker):
        """get_findings 只返回 finding 类型"""
        progress_tracker.append("S001", "init", summary="初始化")
        progress_tracker.append("S001", "finding", summary="发现1")
        progress_tracker.append("S001", "feature_done", feature_id=1, summary="完成")
        progress_tracker.append("S001", "finding", summary="发现2")
        progress_tracker.append("S001", "bug_fix", summary="修复")
        progress_tracker.load()
        findings = progress_tracker.get_findings()
        assert len(findings) == 2
        assert findings[0].summary == "发现1"
        assert findings[1].summary == "发现2"

    def test_get_findings_summary(self, progress_tracker):
        """findings 摘要生成"""
        progress_tracker.append("S001", "finding", feature_id=1,
                                summary="API 限制每秒 10 次请求")
        progress_tracker.append("S001", "finding", feature_id=0,
                                summary="Python 3.12 不兼容旧版 asyncio")
        progress_tracker.load()
        summary = progress_tracker.get_findings_summary()
        assert "关键发现" in summary
        assert "API 限制" in summary
        assert "Python 3.12" in summary
        assert "[Feature #1]" in summary

    def test_get_findings_summary_empty(self, progress_tracker):
        """无 findings 时摘要为空"""
        progress_tracker.load()
        assert progress_tracker.get_findings_summary() == ""

    def test_findings_in_progress_summary(self, progress_tracker):
        """findings 出现在进度摘要中"""
        progress_tracker.append("S001", "finding", summary="重要发现")
        progress_tracker.load()
        summary = progress_tracker.get_summary()
        assert "💡" in summary
        assert "重要发现" in summary

    def test_harness_record_finding(self, mock_agent, task_config):
        """TaskHarness.record_finding 便捷方法"""
        harness = TaskHarness(mock_agent, task_config)
        os.makedirs(task_config.project_dir, exist_ok=True)
        harness.progress.init_file("测试")
        harness.record_finding("Docker 网络隔离需要 --network=none", feature_id=3)
        harness.progress.load()
        findings = harness.progress.get_findings()
        assert len(findings) == 1
        assert findings[0].feature_id == 3

    def test_findings_in_get_up_to_speed(self, mock_agent, task_config):
        """findings 出现在 Get Up to Speed 上下文中"""
        features_json = json.dumps([
            {"id": 1, "category": "functional", "description": "功能",
             "steps": ["验证"], "priority": 1},
        ])
        mock_agent.spawn.return_value = "```json\n{}\n```".format(features_json)
        harness = TaskHarness(mock_agent, task_config)
        harness.initialize()
        harness.record_finding("关键技术决策: 选择 FastAPI 而非 Flask")
        harness.feature_mgr.load()
        harness.progress.load()
        context = harness._get_up_to_speed()
        assert "关键发现" in context
        assert "FastAPI" in context

    def test_findings_in_status(self, mock_agent, task_config):
        """status 包含 findings 计数"""
        features_json = json.dumps([
            {"id": 1, "category": "functional", "description": "功能",
             "steps": ["验证"], "priority": 1},
        ])
        mock_agent.spawn.return_value = "```json\n{}\n```".format(features_json)
        harness = TaskHarness(mock_agent, task_config)
        harness.initialize()
        harness.record_finding("发现1")
        harness.record_finding("发现2")
        status = harness.status()
        assert status["findings_count"] == 2

    def test_coding_prompt_mentions_findings(self):
        """Coding Agent prompt 包含 findings 指引"""
        assert "finding" in CODING_AGENT_PROMPT.lower() or "发现" in CODING_AGENT_PROMPT


# ══════════════════════════════════════════════════════════════
# Steering 方法论文件
# ══════════════════════════════════════════════════════════════

class TestSteeringMethodology:
    """验证 Superpowers 方法论 Steering 文件存在且格式正确"""

    def test_tdd_steering_exists(self):
        """TDD steering 文件存在"""
        assert os.path.exists("steering/tdd.md")

    def test_debugging_steering_exists(self):
        """调试方法论 steering 文件存在"""
        assert os.path.exists("steering/debugging.md")

    def test_planning_steering_exists(self):
        """规划方法论 steering 文件存在"""
        assert os.path.exists("steering/planning.md")

    def test_tdd_steering_has_front_matter(self):
        """TDD steering 有正确的 front-matter"""
        with open("steering/tdd.md", "r") as f:
            content = f.read()
        assert content.startswith("---")
        assert "inclusion:" in content
        assert "fileMatch" in content
        assert "*.py" in content

    def test_debugging_steering_is_manual(self):
        """调试 steering 是手动模式"""
        with open("steering/debugging.md", "r") as f:
            content = f.read()
        assert "inclusion: manual" in content

    def test_planning_steering_is_manual(self):
        """规划 steering 是手动模式"""
        with open("steering/planning.md", "r") as f:
            content = f.read()
        assert "inclusion: manual" in content

    def test_tdd_steering_content(self):
        """TDD steering 包含关键方法论"""
        with open("steering/tdd.md", "r") as f:
            content = f.read()
        assert "RED" in content
        assert "GREEN" in content
        assert "REFACTOR" in content

    def test_debugging_steering_content(self):
        """调试 steering 包含 4 阶段"""
        with open("steering/debugging.md", "r") as f:
            content = f.read()
        assert "复现" in content
        assert "采集" in content
        assert "分析" in content
        assert "验证" in content

    def test_planning_steering_content(self):
        """规划 steering 包含三阶段 + findings"""
        with open("steering/planning.md", "r") as f:
            content = f.read()
        assert "Brainstorm" in content
        assert "Plan" in content
        assert "Execute" in content
        assert "findings" in content.lower() or "Findings" in content

    def test_steering_loads_correctly(self):
        """Steering 管理器能正确加载新文件"""
        from agent_core.steering import SteeringManager
        mgr = SteeringManager("steering")
        files = mgr.list_files()
        names = [f["name"] for f in files]
        assert "tdd.md" in names
        assert "debugging.md" in names
        assert "planning.md" in names

    def test_tdd_auto_loads_for_python(self):
        """TDD steering 在 Python 文件上下文中自动加载"""
        from agent_core.steering import SteeringManager
        mgr = SteeringManager("steering")
        content = mgr.get_active_content(context_files=["main.py"])
        assert "RED" in content or "TDD" in content
