"""长任务控制框架 — 对标 Anthropic "Effective Harnesses for Long-Running Agents"

核心机制:
1. Initializer Agent: 首次运行建立环境（feature_list.json + progress.txt + init.sh + git init）
2. Coding Agent: 每个 session 增量完成一个 feature，保持环境干净
3. Verification: 强制验证后才能标记 feature 完成
4. Session Bridge: 跨 session 上下文桥接（progress + git log）

解决的 4 个失败模式:
F1: Agent 试图一次性完成所有事 → feature_list + 强制增量
F2: Context 耗尽后下个 session 接手半成品 → progress + git commit + 清洁退出
F3: 看到已有进展就宣布完成 → feature_list 未完成项强制可见
F4: 标记完成但没真正测试 → 强制验证后才能标 passes
"""
from __future__ import annotations
import json, os, time, subprocess, re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import TYPE_CHECKING

from agent_core.harness.layer import HarnessLayer

if TYPE_CHECKING:
    from agent_core.agent import Agent


# ── 数据结构 ─────────────────────────────────────────────────

class FeatureCategory(str, Enum):
    FUNCTIONAL = "functional"
    UI = "ui"
    PERFORMANCE = "performance"
    SECURITY = "security"
    TESTING = "testing"
    INFRASTRUCTURE = "infrastructure"


@dataclass
class Feature:
    """单个 feature 定义 — 对标 Anthropic feature_list.json 格式"""
    id: int
    category: str
    description: str
    steps: list[str] = field(default_factory=list)
    passes: bool = False
    priority: int = 1          # 1=最高优先级
    verified_at: str = ""      # 验证通过的时间戳
    session_id: str = ""       # 哪个 session 完成的

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Feature":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ProgressEntry:
    """进度日志条目"""
    timestamp: str
    session_id: str
    action: str          # init / feature_start / feature_done / bug_fix / session_end / finding
    feature_id: int = 0
    summary: str = ""
    git_commit: str = ""


@dataclass
class TaskConfig:
    """长任务配置"""
    project_dir: str                    # 项目工作目录
    requirement: str                    # 用户原始需求
    init_script: str = "init.sh"       # 启动脚本名
    feature_file: str = "feature_list.json"
    progress_file: str = "task_progress.txt"
    verify_command: str = ""            # 可配置验证命令
    max_features_per_session: int = 1   # 每 session 最多完成几个 feature
    auto_commit: bool = True            # 自动 git commit
    smoke_test_command: str = ""        # 健康检查命令



# ── Prompt 模板 (#16) ────────────────────────────────────────

INITIALIZER_PROMPT = """你是一个项目初始化专家。你的任务是为一个长期运行的开发项目建立完整的环境基础。

用户需求: {requirement}
项目目录: {project_dir}

你必须完成以下步骤:

1. **分析需求**: 将用户的高层需求分解为具体的、可测试的 feature 列表
   - 每个 feature 必须有 category、description、steps（验证步骤）
   - 所有 feature 的 passes 字段初始为 false
   - 按优先级排序（核心功能优先）
   - 目标: 生成 {min_features}~{max_features} 个 feature

2. **生成 feature_list.json**: 严格按以下 JSON 格式:
   [
     {{
       "id": 1,
       "category": "functional",
       "description": "具体功能描述",
       "steps": ["步骤1", "步骤2", "步骤3"],
       "passes": false,
       "priority": 1
     }}
   ]

3. **生成 init.sh**: 项目启动脚本，包含:
   - 环境依赖安装
   - 开发服务器启动（如适用）
   - 基础健康检查命令

4. **创建初始项目结构**: 如果项目目录为空，创建基础骨架

5. **写入 progress 日志**: 记录初始化完成

重要规则:
- feature 数量要全面覆盖需求，不要遗漏
- 每个 feature 必须是独立可验证的
- 优先级 1 = 最高（核心功能），2 = 中等，3 = 低（优化/美化）
- 只返回需要执行的工具调用，不要返回大段解释"""

CODING_AGENT_PROMPT = """你是一个增量开发专家。你的任务是在每个 session 中完成一个 feature，并保持代码库干净。

项目目录: {project_dir}

## 启动流程（必须按顺序执行）

1. 运行 `pwd` 确认工作目录
2. 读取 {progress_file} 了解最近的工作进展
3. 读取 {feature_file} 查看所有 feature 状态
4. 运行 `git log --oneline -20` 查看最近的提交历史
5. 如果有 {init_script}，运行它启动开发环境
6. 运行基础健康检查，确认现有功能正常{smoke_test_section}

## 工作规则

1. **选择 feature**: 从 feature_list.json 中选择优先级最高的、passes=false 的 feature
2. **增量开发**: 一次只做一个 feature，不要试图同时做多个
3. **验证**: 完成后必须按 feature 的 steps 逐步验证，确认功能正常
4. **标记完成**: 只有验证通过后才能将 passes 改为 true
5. **Git 提交**: 每完成一个 feature 就 commit，消息格式: "feat: [feature描述]"
6. **更新进度**: 在 {progress_file} 中记录完成情况
7. **清洁退出**: 确保代码无明显 bug，可以直接交给下一个 session

## 绝对禁止

- ❌ 不要删除或修改 feature_list.json 中的 description 和 steps
- ❌ 不要在未验证的情况下标记 feature 为 passes=true
- ❌ 不要试图一次完成所有 feature
- ❌ 不要留下半成品代码（如果做不完，revert 到上一个 commit）

## 如果发现 bug

1. 优先修复 bug（在开始新 feature 之前）
2. 修复后 commit: "fix: [bug描述]"
3. 在 progress 中记录 bug 修复

## 记录关键发现 (Findings)

在开发过程中发现的重要信息必须记录为 finding:
- 技术决策及其理由（为什么选 A 不选 B）
- 发现的约束条件或限制（API 限制、性能瓶颈等）
- 可复用的代码模式或配置
- 踩过的坑和解决方案
记录方式: 在 {progress_file} 中追加 finding 类型条目"""

VERIFICATION_PROMPT = """你是一个严格的 QA 验证专家。你的任务是验证一个 feature 是否真正完成。

Feature: {feature_description}
验证步骤:
{verification_steps}

请严格按照验证步骤逐一执行，每一步都要有明确的通过/失败结论。
{custom_verify_section}

输出格式:
- 每个步骤: ✅ 通过 / ❌ 失败 + 原因
- 最终结论: PASS 或 FAIL
- 如果 FAIL，说明具体哪里不对

你必须像真实用户一样测试，不能只看代码就判断通过。"""



# ── Feature 状态管理 (#9) ────────────────────────────────────

class FeatureManager:
    """feature_list.json 的读写管理

    核心规则: 只允许修改 passes/verified_at/session_id 字段，
    禁止删除或修改 description/steps（对标 Anthropic 的强约束）
    """

    def __init__(self, feature_file: str):
        self._path = feature_file
        self._features: list[Feature] = []

    @property
    def features(self) -> list[Feature]:
        return self._features

    def load(self) -> list[Feature]:
        """加载 feature_list.json"""
        if not os.path.exists(self._path):
            self._features = []
            return self._features
        with open(self._path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._features = [Feature.from_dict(d) for d in data]
        return self._features

    def save(self):
        """保存 feature_list.json"""
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump([ft.to_dict() for ft in self._features], f,
                      ensure_ascii=False, indent=2)

    def set_features(self, features: list[Feature]):
        """初始化时设置 feature 列表"""
        self._features = features
        self.save()

    def mark_passed(self, feature_id: int, session_id: str = "") -> bool:
        """标记 feature 为通过 — 只改 passes/verified_at/session_id"""
        for ft in self._features:
            if ft.id == feature_id:
                ft.passes = True
                ft.verified_at = time.strftime("%Y-%m-%d %H:%M:%S")
                ft.session_id = session_id
                self.save()
                return True
        return False

    def mark_failed(self, feature_id: int) -> bool:
        """标记 feature 为未通过（回退）"""
        for ft in self._features:
            if ft.id == feature_id:
                ft.passes = False
                ft.verified_at = ""
                ft.session_id = ""
                self.save()
                return True
        return False

    def get_next_feature(self) -> Feature | None:
        """获取下一个待完成的 feature（按优先级排序）"""
        pending = [ft for ft in self._features if not ft.passes]
        if not pending:
            return None
        pending.sort(key=lambda f: (f.priority, f.id))
        return pending[0]

    def get_stats(self) -> dict:
        """获取完成统计"""
        total = len(self._features)
        passed = sum(1 for ft in self._features if ft.passes)
        return {
            "total": total,
            "passed": passed,
            "remaining": total - passed,
            "progress_pct": round(passed / total * 100, 1) if total else 0,
        }

    def validate_integrity(self, original_features: list[Feature]) -> list[str]:
        """验证 feature 列表完整性 — 确保 description/steps 未被篡改"""
        errors = []
        orig_map = {ft.id: ft for ft in original_features}
        curr_map = {ft.id: ft for ft in self._features}

        # 检查是否有 feature 被删除
        for fid in orig_map:
            if fid not in curr_map:
                errors.append("Feature #{} 被删除".format(fid))

        # 检查 description/steps 是否被修改
        for fid, orig in orig_map.items():
            curr = curr_map.get(fid)
            if curr and curr.description != orig.description:
                errors.append("Feature #{} description 被修改".format(fid))
            if curr and curr.steps != orig.steps:
                errors.append("Feature #{} steps 被修改".format(fid))

        return errors



# ── Progress 管理 (#4 #12) ───────────────────────────────────

class ProgressTracker:
    """结构化进度日志 — 对标 Anthropic 的 claude-progress.txt"""

    def __init__(self, progress_file: str):
        self._path = progress_file
        self._entries: list[ProgressEntry] = []

    def load(self) -> list[ProgressEntry]:
        """加载进度文件"""
        self._entries = []
        if not os.path.exists(self._path):
            return self._entries
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    parts = line.split(" | ", 4)
                    if len(parts) >= 4:
                        entry = ProgressEntry(
                            timestamp=parts[0].strip(),
                            session_id=parts[1].strip(),
                            action=parts[2].strip(),
                            feature_id=int(parts[3].strip()) if len(parts) > 3 and parts[3].strip().isdigit() else 0,
                            summary=parts[4].strip() if len(parts) > 4 else "",
                        )
                        self._entries.append(entry)
                except (ValueError, IndexError):
                    continue
        return self._entries

    def append(self, session_id: str, action: str, feature_id: int = 0,
               summary: str = "", git_commit: str = ""):
        """追加进度条目"""
        entry = ProgressEntry(
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            session_id=session_id,
            action=action,
            feature_id=feature_id,
            summary=summary,
            git_commit=git_commit,
        )
        self._entries.append(entry)
        with open(self._path, "a", encoding="utf-8") as f:
            parts = [entry.timestamp, entry.session_id, entry.action,
                     str(entry.feature_id), entry.summary]
            if entry.git_commit:
                parts.append("commit:{}".format(entry.git_commit))
            f.write(" | ".join(parts) + "\n")

    def get_recent(self, n: int = 10) -> list[ProgressEntry]:
        """获取最近 N 条进度"""
        return self._entries[-n:]

    def get_summary(self) -> str:
        """生成进度摘要（供 coding agent 快速了解状态）"""
        if not self._entries:
            return "无历史进度记录"
        lines = []
        for entry in self._entries[-10:]:
            if entry.action == "init":
                lines.append("[{}] 项目初始化完成".format(entry.timestamp))
            elif entry.action == "feature_start":
                lines.append("[{}] 开始 Feature #{}".format(entry.timestamp, entry.feature_id))
            elif entry.action == "feature_done":
                lines.append("[{}] ✅ Feature #{} 完成: {}".format(
                    entry.timestamp, entry.feature_id, entry.summary))
            elif entry.action == "finding":
                lines.append("[{}] 💡 发现: {}".format(entry.timestamp, entry.summary))
            elif entry.action == "bug_fix":
                lines.append("[{}] 🔧 修复: {}".format(entry.timestamp, entry.summary))
            elif entry.action == "session_end":
                lines.append("[{}] Session 结束: {}".format(entry.timestamp, entry.summary))
            else:
                lines.append("[{}] {}: {}".format(entry.timestamp, entry.action, entry.summary))
        return "\n".join(lines)

    def get_findings(self) -> list[ProgressEntry]:
        """获取所有 findings 条目（借鉴 Planning with Files 的 findings.md 概念）

        Findings 是执行过程中发现的关键信息:
        - 技术决策及理由
        - 约束条件/限制
        - 可复用的模式
        - 踩过的坑和解决方案
        """
        return [e for e in self._entries if e.action == "finding"]

    def get_findings_summary(self) -> str:
        """生成 findings 摘要（供 coding agent 参考已有发现）"""
        findings = self.get_findings()
        if not findings:
            return ""
        lines = ["## 关键发现 (Findings)"]
        for i, f in enumerate(findings, 1):
            feature_tag = " [Feature #{}]".format(f.feature_id) if f.feature_id else ""
            lines.append("{}. [{}]{} {}".format(i, f.timestamp, feature_tag, f.summary))
        return "\n".join(lines)

    def init_file(self, requirement: str):
        """初始化进度文件"""
        with open(self._path, "w", encoding="utf-8") as f:
            f.write("# Task Progress Log\n")
            f.write("# 需求: {}\n".format(requirement[:200]))
            f.write("# 格式: timestamp | session_id | action | feature_id | summary\n")
            f.write("#" + "=" * 70 + "\n")



# ── Git 集成 (#5 #8 #13) ────────────────────────────────────

class GitManager:
    """Git 操作封装 — commit/log/revert"""

    def __init__(self, project_dir: str):
        self._dir = project_dir

    def _run(self, *args, timeout: int = 30) -> tuple[bool, str]:
        """执行 git 命令"""
        try:
            result = subprocess.run(
                ["git"] + list(args),
                cwd=self._dir, capture_output=True, text=True, timeout=timeout,
            )
            output = result.stdout.strip()
            if result.returncode != 0:
                err = result.stderr.strip()
                return False, err or output
            return True, output
        except subprocess.TimeoutExpired:
            return False, "git 命令超时"
        except FileNotFoundError:
            return False, "git 未安装"

    def is_repo(self) -> bool:
        """检查是否是 git 仓库"""
        ok, _ = self._run("rev-parse", "--is-inside-work-tree")
        return ok

    def init(self) -> bool:
        """初始化 git 仓库"""
        if self.is_repo():
            return True
        ok, _ = self._run("init")
        return ok

    def add_all(self) -> bool:
        ok, _ = self._run("add", "-A")
        return ok

    def commit(self, message: str) -> tuple[bool, str]:
        """提交 — 返回 (成功, commit hash 或错误信息)"""
        self.add_all()
        ok, output = self._run("commit", "-m", message, "--allow-empty")
        if ok:
            # 提取 commit hash — 格式: [branch hash] 或 [branch (root-commit) hash]
            hash_match = re.search(r'\[[\w/-]+(?:\s+\([^)]+\))?\s+([a-f0-9]+)\]', output)
            commit_hash = hash_match.group(1) if hash_match else ""
            return True, commit_hash
        return False, output

    def log(self, n: int = 20) -> str:
        """获取最近 N 条 commit 日志"""
        ok, output = self._run("log", "--oneline", "-{}".format(n))
        return output if ok else ""

    def revert_to(self, commit_hash: str) -> bool:
        """回退到指定 commit"""
        ok, _ = self._run("checkout", commit_hash, "--", ".")
        return ok

    def get_current_hash(self) -> str:
        """获取当前 HEAD 的 commit hash"""
        ok, output = self._run("rev-parse", "--short", "HEAD")
        return output if ok else ""

    def has_changes(self) -> bool:
        """检查是否有未提交的变更"""
        ok, output = self._run("status", "--porcelain")
        return ok and bool(output.strip())

    def diff_stat(self) -> str:
        """获取变更统计"""
        ok, output = self._run("diff", "--stat", "HEAD")
        return output if ok else ""



# ── 验证引擎 (#10 #11 #17) ──────────────────────────────────

class VerificationEngine:
    """Feature 验证引擎 — 强制验证后才能标记完成

    支持三种验证策略:
    1. Agent 自验证: 让 Agent 按 steps 逐步验证（默认）
    2. 命令验证: 运行指定命令（pytest/curl/自定义脚本）
    3. 混合验证: 先运行命令，再让 Agent 确认
    """

    def __init__(self, agent: "Agent", config: TaskConfig):
        self._agent = agent
        self._config = config

    def verify_feature(self, feature: Feature) -> tuple[bool, str]:
        """验证单个 feature — 返回 (通过, 详情)"""
        results = []

        # 策略1: 如果有自定义验证命令，先运行
        if self._config.verify_command:
            cmd_ok, cmd_output = self._run_verify_command(feature)
            results.append(("命令验证", cmd_ok, cmd_output))
            if not cmd_ok:
                return False, "命令验证失败: {}".format(cmd_output)

        # 策略2: Agent 自验证（按 steps 逐步执行）
        agent_ok, agent_output = self._agent_verify(feature)
        results.append(("Agent验证", agent_ok, agent_output))

        # 汇总
        all_passed = all(r[1] for r in results)
        detail = "\n".join("{}: {} — {}".format(
            r[0], "✅" if r[1] else "❌", r[2][:200]) for r in results)
        return all_passed, detail

    def smoke_test(self) -> tuple[bool, str]:
        """健康检查 (#11) — session 开始时运行基础测试"""
        if not self._config.smoke_test_command:
            return True, "无健康检查命令，跳过"
        try:
            result = subprocess.run(
                self._config.smoke_test_command,
                shell=True, cwd=self._config.project_dir,
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                return True, "健康检查通过"
            return False, "健康检查失败: {}".format(
                (result.stderr or result.stdout)[:300])
        except subprocess.TimeoutExpired:
            return False, "健康检查超时"
        except Exception as e:
            return False, "健康检查异常: {}".format(e)

    def _run_verify_command(self, feature: Feature) -> tuple[bool, str]:
        """运行验证命令"""
        cmd = self._config.verify_command
        # 支持模板变量
        cmd = cmd.replace("{feature_id}", str(feature.id))
        cmd = cmd.replace("{feature_desc}", feature.description[:50])
        try:
            result = subprocess.run(
                cmd, shell=True, cwd=self._config.project_dir,
                capture_output=True, text=True, timeout=120,
            )
            output = result.stdout + result.stderr
            return result.returncode == 0, output[:500]
        except subprocess.TimeoutExpired:
            return False, "验证命令超时"
        except Exception as e:
            return False, str(e)

    def _agent_verify(self, feature: Feature) -> tuple[bool, str]:
        """让 Agent 按 steps 验证 feature"""
        steps_text = "\n".join("{}. {}".format(i + 1, s) for i, s in enumerate(feature.steps))
        custom_section = ""
        if self._config.verify_command:
            custom_section = "\n也可以运行验证命令: {}".format(self._config.verify_command)

        prompt = VERIFICATION_PROMPT.format(
            feature_description=feature.description,
            verification_steps=steps_text,
            custom_verify_section=custom_section,
        )
        result = self._agent.spawn(
            prompt,
            system_prompt="你是严格的 QA 验证专家。只关注验证结果，不要做额外开发。",
            timeout=120,
        )
        # 解析结果
        passed = "PASS" in result.upper() and "FAIL" not in result.upper().split("PASS")[-1]
        return passed, result[:500]



# ── TaskHarness 核心调度器 (#1 #6 #7 #8 #14) ────────────────

class TaskHarness:
    """长任务控制框架 — 管理 Initializer → Coding Agent 的两阶段流程

    用法:
        harness = TaskHarness(agent, config)
        harness.initialize()       # 首次: 建立环境
        harness.run_session()      # 后续: 增量完成 feature
        harness.status()           # 查看进度
    """

    def __init__(self, agent: "Agent", config: TaskConfig):
        self.agent = agent
        self.config = config
        self.feature_mgr = FeatureManager(
            os.path.join(config.project_dir, config.feature_file))
        self.progress = ProgressTracker(
            os.path.join(config.project_dir, config.progress_file))
        self.git = GitManager(config.project_dir)
        self.verifier = VerificationEngine(agent, config)
        self._session_id = "S{}".format(time.strftime("%Y%m%d_%H%M%S"))
        self._initialized = False

        # Harness 驾驭层 — Map 感知和约束验证
        self._harness_layer: HarnessLayer | None = None
        try:
            self._harness_layer = HarnessLayer(agent=agent, cwd=config.project_dir)
        except Exception:
            self._harness_layer = None

    # ── 判断是否已初始化 ─────────────────────────────────────

    def is_initialized(self) -> bool:
        """检查项目是否已经过 initializer agent 初始化"""
        feature_path = os.path.join(self.config.project_dir, self.config.feature_file)
        progress_path = os.path.join(self.config.project_dir, self.config.progress_file)
        return os.path.exists(feature_path) and os.path.exists(progress_path)

    # ── 阶段一: Initializer Agent (#1 #2 #3 #4 #5) ─────────

    def initialize(self) -> dict:
        """运行 Initializer Agent — 建立项目环境

        Returns:
            {"features": int, "init_script": bool, "git_commit": str}
        """
        print("\n  🏗️  [TaskHarness] 初始化长任务环境 ...")
        print("  📋 需求: {}".format(self.config.requirement[:100]))

        os.makedirs(self.config.project_dir, exist_ok=True)

        # 1. 初始化 git (#5)
        self.git.init()

        # 2. 初始化 progress 文件 (#4)
        self.progress.init_file(self.config.requirement)

        # 3. 运行 Initializer Agent 生成 feature_list 和 init.sh (#2 #3)
        prompt = INITIALIZER_PROMPT.format(
            requirement=self.config.requirement,
            project_dir=self.config.project_dir,
            min_features=10,
            max_features=200,
        )

        print("  🤖 Initializer Agent 分析需求中 ...")
        result = self.agent.spawn(prompt, system_prompt=prompt, timeout=300)

        # 4. 解析 Agent 输出，提取 feature_list
        features = self._parse_features_from_output(result)
        if not features:
            # 回退: 创建单个 feature
            features = [Feature(id=1, category="functional",
                                description=self.config.requirement,
                                steps=["验证功能是否正常工作"], priority=1)]

        self.feature_mgr.set_features(features)
        print("  📋 生成 {} 个 Feature".format(len(features)))

        # 5. 生成 init.sh (#2)
        self._generate_init_script(result)

        # 6. 记录进度
        self.progress.append(self._session_id, "init",
                             summary="初始化完成，{} 个 feature".format(len(features)))

        # 7. 初始 git commit (#5)
        commit_hash = ""
        if self.config.auto_commit:
            ok, commit_hash = self.git.commit(
                "init: 项目初始化 — {} 个 feature".format(len(features)))
            if ok:
                print("  📦 Git commit: {}".format(commit_hash))

        self._initialized = True
        stats = self.feature_mgr.get_stats()
        print("  ✅ 初始化完成: {} 个 feature 待完成\n".format(stats["total"]))

        return {
            "features": len(features),
            "init_script": os.path.exists(
                os.path.join(self.config.project_dir, self.config.init_script)),
            "git_commit": commit_hash,
            "session_id": self._session_id,
        }

    # ── 阶段二: Coding Agent Session (#6 #7 #8) ────────────

    def run_session(self) -> dict:
        """运行一个 Coding Agent Session — 增量完成 feature

        Returns:
            {"feature_id": int, "passed": bool, "summary": str, "stats": dict}
        """
        if not self.is_initialized():
            print("  ⚠️  项目未初始化，先运行 initialize()")
            return {"error": "not_initialized"}

        self.feature_mgr.load()
        self.progress.load()

        print("\n  🔄 [TaskHarness] Session {} 启动".format(self._session_id))

        # ── Get Up to Speed (#6) ────────────────────────────
        context = self._get_up_to_speed()

        # ── Harness 约束感知 ──────────────────────────────
        if self._harness_layer and self._harness_layer.enabled:
            harness_steering = self._harness_layer.get_steering_enhancement()
            if harness_steering:
                context += "\n\n" + harness_steering

        # ── 健康检查 (#11) ──────────────────────────────────
        smoke_ok, smoke_msg = self.verifier.smoke_test()
        if not smoke_ok:
            print("  ⚠️  健康检查失败: {}".format(smoke_msg))
            print("  🔧 尝试修复 ...")
            self._fix_broken_state(context, smoke_msg)

        # ── 选择下一个 feature (#7) ─────────────────────────
        feature = self.feature_mgr.get_next_feature()
        if not feature:
            print("  🎉 所有 Feature 已完成!")
            return {"feature_id": 0, "passed": True,
                    "summary": "all_done", "stats": self.feature_mgr.get_stats()}

        print("  🎯 Feature #{}: {}".format(feature.id, feature.description))
        self.progress.append(self._session_id, "feature_start",
                             feature_id=feature.id, summary=feature.description[:80])

        # ── 执行 feature (#7) ───────────────────────────────
        coding_prompt = self._build_coding_prompt(feature, context)
        print("  🤖 Coding Agent 开始工作 ...")
        work_result = self.agent.run(coding_prompt)

        # ── 验证 (#10) ─────────────────────────────────────
        print("  🔍 验证 Feature #{} ...".format(feature.id))
        verified, verify_detail = self.verifier.verify_feature(feature)

        if verified:
            self.feature_mgr.mark_passed(feature.id, self._session_id)
            print("  ✅ Feature #{} 验证通过".format(feature.id))
        else:
            print("  ❌ Feature #{} 验证失败: {}".format(feature.id, verify_detail[:100]))

        # ── 清洁退出 (#8) ──────────────────────────────────
        commit_hash = self._clean_exit(feature, verified)

        # 记录进度
        action = "feature_done" if verified else "feature_fail"
        self.progress.append(
            self._session_id, action, feature_id=feature.id,
            summary="{}".format("通过" if verified else "未通过"),
            git_commit=commit_hash,
        )

        stats = self.feature_mgr.get_stats()
        print("  📊 进度: {}/{} ({:.1f}%)\n".format(
            stats["passed"], stats["total"], stats["progress_pct"]))

        return {
            "feature_id": feature.id,
            "passed": verified,
            "summary": feature.description,
            "stats": stats,
            "git_commit": commit_hash,
        }

    def run_all(self, max_sessions: int = 50) -> dict:
        """连续运行多个 session 直到所有 feature 完成

        Returns:
            {"sessions": int, "stats": dict}
        """
        if not self.is_initialized():
            self.initialize()

        sessions = 0
        for _ in range(max_sessions):
            result = self.run_session()
            sessions += 1

            if result.get("error"):
                break
            if result.get("summary") == "all_done":
                break

            # 更新 session ID
            self._session_id = "S{}".format(time.strftime("%Y%m%d_%H%M%S"))

        stats = self.feature_mgr.get_stats()
        print("\n  🏁 长任务完成: {} 个 session, {}/{} feature 通过".format(
            sessions, stats["passed"], stats["total"]))
        return {"sessions": sessions, "stats": stats}

    # ── 状态查询 ─────────────────────────────────────────────

    def status(self) -> dict:
        """查看当前长任务状态"""
        self.feature_mgr.load()
        self.progress.load()
        stats = self.feature_mgr.get_stats()
        recent = self.progress.get_recent(5)
        findings = self.progress.get_findings()
        git_log = self.git.log(5)

        return {
            "initialized": self.is_initialized(),
            "stats": stats,
            "recent_progress": [
                {"time": e.timestamp, "action": e.action,
                 "feature": e.feature_id, "summary": e.summary}
                for e in recent
            ],
            "findings_count": len(findings),
            "git_log": git_log,
            "next_feature": None if not self.feature_mgr.get_next_feature()
                else self.feature_mgr.get_next_feature().to_dict(),
        }

    def record_finding(self, summary: str, feature_id: int = 0):
        """记录关键发现（借鉴 Planning with Files 的 findings.md 概念）

        Findings 是执行过程中发现的重要信息，跨 session 持久化，
        供后续 coding agent 在 Get Up to Speed 时参考。

        Args:
            summary: 发现内容（技术决策/约束/模式/坑）
            feature_id: 关联的 feature ID（可选）
        """
        self.progress.append(
            self._session_id, "finding",
            feature_id=feature_id, summary=summary,
        )

    # ── 内部方法 ─────────────────────────────────────────────

    def _get_up_to_speed(self) -> str:
        """Get Up to Speed 流程 (#6) — 让 coding agent 快速了解状态"""
        parts = []

        # 1. 进度摘要
        progress_summary = self.progress.get_summary()
        if progress_summary:
            parts.append("## 最近进度\n{}".format(progress_summary))

        # 2. Feature 统计
        stats = self.feature_mgr.get_stats()
        parts.append("## Feature 状态\n总计: {} | 已完成: {} | 剩余: {} | 进度: {:.1f}%".format(
            stats["total"], stats["passed"], stats["remaining"], stats["progress_pct"]))

        # 3. Findings — 关键发现（借鉴 Planning with Files）
        findings_summary = self.progress.get_findings_summary()
        if findings_summary:
            parts.append(findings_summary)

        # 4. Git 历史
        git_log = self.git.log(10)
        if git_log:
            parts.append("## 最近 Git 提交\n{}".format(git_log))

        # 5. 未完成 feature 列表（前 5 个）
        pending = [ft for ft in self.feature_mgr.features if not ft.passes]
        if pending:
            pending.sort(key=lambda f: (f.priority, f.id))
            lines = ["## 待完成 Feature (前5)"]
            for ft in pending[:5]:
                lines.append("  #{} [P{}] {}".format(ft.id, ft.priority, ft.description))
            parts.append("\n".join(lines))

        # 6. 认知地图（Harness Map 感知）
        if self._harness_layer:
            map_path = os.path.join(self.config.project_dir, ".harness", "map.md")
            if os.path.isfile(map_path):
                try:
                    with open(map_path, "r", encoding="utf-8") as f:
                        map_content = f.read().strip()
                    if map_content:
                        parts.append("## 项目认知地图\n{}".format(map_content[:2000]))
                except Exception:
                    pass

        context = "\n\n".join(parts)
        print("  📖 上下文加载完成 ({} 字符)".format(len(context)))
        return context

    def _build_coding_prompt(self, feature: Feature, context: str) -> str:
        """构建 Coding Agent 的 prompt"""
        smoke_section = ""
        if self.config.smoke_test_command:
            smoke_section = "\n7. 运行健康检查: `{}`".format(self.config.smoke_test_command)

        base_prompt = CODING_AGENT_PROMPT.format(
            project_dir=self.config.project_dir,
            progress_file=self.config.progress_file,
            feature_file=self.config.feature_file,
            init_script=self.config.init_script,
            smoke_test_section=smoke_section,
        )

        steps_text = "\n".join("  {}. {}".format(i + 1, s) for i, s in enumerate(feature.steps))

        return """{context}

---

{base_prompt}

## 当前任务

Feature #{fid} [优先级 {priority}]: {desc}

验证步骤:
{steps}

请开始工作。完成后我会进行验证。""".format(
            context=context,
            base_prompt=base_prompt,
            fid=feature.id,
            priority=feature.priority,
            desc=feature.description,
            steps=steps_text,
        )

    def _clean_exit(self, feature: Feature, verified: bool) -> str:
        """清洁退出 (#8) — git commit + 确保代码可运行"""
        commit_hash = ""
        if self.config.auto_commit and self.git.has_changes():
            if verified:
                msg = "feat: Feature #{} — {}".format(feature.id, feature.description[:50])
            else:
                msg = "wip: Feature #{} 进行中 — {}".format(feature.id, feature.description[:50])
            ok, commit_hash = self.git.commit(msg)
            if ok:
                print("  📦 Git commit: {} ({})".format(commit_hash, msg[:40]))
        return commit_hash

    def _fix_broken_state(self, context: str, error_msg: str):
        """修复 broken state — 健康检查失败时调用"""
        fix_prompt = """项目健康检查失败，请先修复:

错误信息: {}

{}

请诊断并修复问题，确保基础功能恢复正常。修复后运行健康检查确认。""".format(error_msg, context)

        self.agent.run(fix_prompt)
        # 修复后 commit
        if self.config.auto_commit and self.git.has_changes():
            self.git.commit("fix: 修复健康检查失败")
        self.progress.append(self._session_id, "bug_fix", summary=error_msg[:80])

    def _parse_features_from_output(self, output: str) -> list[Feature]:
        """从 Initializer Agent 输出中解析 feature 列表"""
        # 尝试提取 JSON 数组
        patterns = [
            r'\[[\s\S]*?\{[\s\S]*?"description"[\s\S]*?\}[\s\S]*?\]',
            r'```json\s*(\[[\s\S]*?\])\s*```',
            r'```\s*(\[[\s\S]*?\])\s*```',
        ]
        for pat in patterns:
            match = re.search(pat, output)
            if match:
                try:
                    json_str = match.group(1) if match.lastindex else match.group(0)
                    data = json.loads(json_str)
                    if isinstance(data, list) and data:
                        features = []
                        for i, item in enumerate(data):
                            if isinstance(item, dict) and "description" in item:
                                features.append(Feature(
                                    id=item.get("id", i + 1),
                                    category=item.get("category", "functional"),
                                    description=item["description"],
                                    steps=item.get("steps", ["验证功能正常"]),
                                    priority=item.get("priority", 1),
                                ))
                        if features:
                            return features
                except (json.JSONDecodeError, KeyError):
                    continue
        return []

    def _generate_init_script(self, agent_output: str):
        """生成 init.sh (#2)"""
        init_path = os.path.join(self.config.project_dir, self.config.init_script)

        # 尝试从 Agent 输出中提取 init.sh 内容
        script_match = re.search(
            r'```(?:bash|sh)\s*\n([\s\S]*?)\n```', agent_output)
        if script_match:
            script_content = script_match.group(1)
        else:
            # 默认 init.sh
            script_content = """#!/bin/bash
# 项目启动脚本 — 由 TaskHarness Initializer 生成
# 需求: {req}

echo "🚀 启动项目环境 ..."

# 检查依赖
if [ -f "requirements.txt" ]; then
    echo "📦 安装 Python 依赖 ..."
    pip install -r requirements.txt 2>/dev/null || pip3 install -r requirements.txt
fi

if [ -f "package.json" ]; then
    echo "📦 安装 Node 依赖 ..."
    npm install
fi

echo "✅ 环境就绪"
""".format(req=self.config.requirement[:100])

        with open(init_path, "w", encoding="utf-8") as f:
            f.write(script_content)
        os.chmod(init_path, 0o755)
        print("  📜 生成 {}".format(self.config.init_script))