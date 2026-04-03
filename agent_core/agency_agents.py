"""Agency Agents 自动匹配系统

扫描 agency-agents 仓库中的 .md 角色文件，解析 front-matter 和正文，
在用户对话时自动匹配最合适的 agent 角色，将其 system prompt 注入上下文。

角色来源：
  agency-agents — https://github.com/msitarzewski/agency-agents
  Copyright (c) 2025 AgentLand Contributors, MIT License

工作原理：
1. 启动时扫描 agency-agents 目录，构建角色索引（名称 + 描述 + 关键词）
2. 每次用户输入时，通过加权关键词匹配找到最相关的角色
3. 将匹配到的角色 system prompt 作为 [专家角色] 注入到 Agent 上下文中
4. 匹配结果缓存，相同话题不重复匹配

目录结构:
  agency-agents/
    engineering/
      engineering-frontend-developer.md
      engineering-security-engineer.md
    design/
      design-brand-guardian.md
    ...
"""
from __future__ import annotations

import os
import re
import fnmatch
from dataclasses import dataclass, field
from typing import Optional


def _parse_front_matter(content: str) -> tuple[dict, str]:
    """解析 YAML front-matter，返回 (meta, body)"""
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end == -1:
        return {}, content
    raw = content[3:end].strip()
    body = content[end + 3:].strip()
    meta = {}
    for line in raw.splitlines():
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            val = val.strip().strip('"').strip("'")
            meta[key.strip()] = val
    return meta, body


@dataclass
class AgentRole:
    """一个 agency-agents 角色"""
    name: str               # front-matter 中的 name
    description: str        # front-matter 中的 description
    category: str           # 目录名（engineering/design/marketing...）
    filename: str           # 文件名（不含路径）
    filepath: str           # 完整路径
    emoji: str = ""
    body: str = ""          # 正文（完整 system prompt）
    keywords: list[str] = field(default_factory=list)  # 从 name + description 提取的关键词



# ── 关键词映射表 ────────────────────────────────────────────
# 每个 category + 角色名 对应一组加权关键词
# 格式: (正则模式, 权重)
# 权重越高表示匹配越强

_CATEGORY_KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "engineering": [
        (r"代码|编程|开发|编码|code|coding|develop|program", 0.3),
        (r"bug|debug|修复|fix|error|错误", 0.3),
        (r"部署|deploy|运维|devops|ci/cd|pipeline", 0.3),
        (r"架构|architecture|设计模式|design pattern", 0.3),
        (r"测试|test|单元测试|集成测试", 0.3),
    ],
    "design": [
        (r"设计|design|UI|UX|界面|交互|用户体验", 0.3),
        (r"品牌|brand|视觉|visual|色彩|颜色|字体|typography", 0.3),
        (r"原型|prototype|线框|wireframe|mockup", 0.3),
        (r"图片|image|图标|icon|插画|illustration", 0.3),
    ],
    "marketing": [
        (r"营销|marketing|推广|运营|增长|growth", 0.3),
        (r"SEO|搜索引擎|内容|content|社交媒体|social media", 0.3),
        (r"抖音|douyin|tiktok|小红书|微信|微博|bilibili", 0.3),
        (r"广告|ad|投放|campaign|转化|conversion", 0.3),
    ],
    "product": [
        (r"产品|product|需求|requirement|用户故事|user story", 0.3),
        (r"优先级|priority|迭代|sprint|backlog|路线图|roadmap", 0.3),
        (r"反馈|feedback|用户研究|user research", 0.3),
    ],
    "testing": [
        (r"测试|test|QA|质量|quality|自动化测试", 0.4),
        (r"性能测试|performance|压测|benchmark|负载", 0.3),
        (r"无障碍|accessibility|a11y|WCAG", 0.3),
    ],
    "sales": [
        (r"销售|sales|客户|client|商务|business|deal", 0.3),
        (r"提案|proposal|报价|quote|pipeline", 0.3),
    ],
    "support": [
        (r"支持|support|运维|维护|maintain|监控|monitor", 0.3),
        (r"合规|compliance|法律|legal|财务|finance", 0.3),
    ],
    "project-management": [
        (r"项目管理|project manage|进度|schedule|排期", 0.3),
        (r"jira|看板|kanban|scrum|敏捷|agile", 0.3),
    ],
    "academic": [
        (r"学术|academic|研究|research|论文|paper|历史|history", 0.3),
        (r"心理|psychology|人类学|anthropology|地理|geography", 0.3),
    ],
    "game-development": [
        (r"游戏|game|unity|unreal|godot|blender|roblox", 0.4),
        (r"关卡|level|叙事|narrative|音效|audio|3D", 0.3),
    ],
    "spatial-computing": [
        (r"XR|VR|AR|MR|空间计算|spatial|visionOS|Metal", 0.4),
        (r"沉浸式|immersive|头显|headset|手势|gesture", 0.3),
    ],
    "specialized": [
        (r"区块链|blockchain|智能合约|smart contract|web3", 0.3),
        (r"MCP|workflow|自动化|automation|编排|orchestrat", 0.3),
    ],
    "strategy": [
        (r"战略|strategy|规划|planning|决策|decision", 0.3),
    ],
    "paid-media": [
        (r"广告|ad|PPC|投放|媒体|media|programmatic", 0.3),
        (r"追踪|tracking|归因|attribution|ROI|ROAS", 0.3),
    ],
}

# 角色级关键词：从文件名和 description 中提取的特征词
# 这些在运行时动态构建
_ROLE_KEYWORD_PATTERNS: dict[str, list[tuple[str, float]]] = {
    # engineering 子角色
    "frontend-developer": [
        (r"前端|frontend|react|vue|angular|svelte|css|html|web|浏览器|browser|响应式|responsive", 0.7),
        (r"组件|component|webpack|vite|npm|yarn|tailwind|sass|less", 0.5),
    ],
    "backend-architect": [
        (r"后端|backend|服务端|server|api|微服务|microservice|数据库|database", 0.7),
        (r"spring|django|fastapi|express|nest|graphql|rest|grpc", 0.5),
    ],
    "mobile-app-builder": [
        (r"移动|mobile|android|ios|app|kotlin|swift|flutter|react native|compose", 0.7),
        (r"原生|native|跨平台|cross-platform|apk|ipa|手机|tablet", 0.5),
        (r"handler|looper|activity|fragment|intent|broadcast|service|contentprovider", 0.75),
        (r"viewmodel|livedata|lifecycle|jetpack|hilt|dagger|room|retrofit|okhttp", 0.75),
        (r"swiftui|uikit|storyboard|cocoapods|xcode|gradle|adb", 0.6),
    ],
    "security-engineer": [
        (r"安全|security|漏洞|vulnerability|渗透|penetration|加密|encrypt|认证|auth", 0.7),
        (r"XSS|CSRF|SQL注入|injection|OWASP|防火墙|firewall|WAF|token|密钥|secret", 0.5),
    ],
    "devops-automator": [
        (r"devops|CI/CD|docker|kubernetes|k8s|容器|container|部署|deploy|运维", 0.7),
        (r"jenkins|github actions|gitlab|terraform|ansible|helm|监控|prometheus|grafana", 0.5),
    ],
    "database-optimizer": [
        (r"数据库|database|SQL|MySQL|PostgreSQL|MongoDB|Redis|索引|index|查询优化", 0.7),
        (r"分库分表|sharding|主从|replication|缓存|cache|ORM|migration", 0.5),
    ],
    "code-reviewer": [
        (r"代码审查|code review|review|审查|CR|pull request|PR|merge request|MR", 0.7),
        (r"代码质量|code quality|重构|refactor|规范|convention|lint", 0.5),
    ],
    "software-architect": [
        (r"架构|architecture|系统设计|system design|分层|layer|模块化|modular", 0.7),
        (r"DDD|领域驱动|CQRS|事件驱动|event driven|六边形|hexagonal|clean arch", 0.5),
    ],
    "ai-engineer": [
        (r"AI|人工智能|机器学习|ML|深度学习|deep learning|模型|model|LLM|大模型", 0.7),
        (r"训练|training|推理|inference|prompt|embedding|向量|vector|RAG|fine-?tune", 0.5),
    ],
    "data-engineer": [
        (r"数据工程|data engineer|ETL|数据管道|data pipeline|数据仓库|data warehouse", 0.7),
        (r"spark|flink|kafka|airflow|dbt|数据湖|data lake|大数据|big data", 0.5),
    ],
    "sre": [
        (r"SRE|可靠性|reliability|可用性|availability|SLA|SLO|SLI|故障|incident", 0.7),
        (r"告警|alert|on-?call|容量|capacity|混沌工程|chaos|降级|fallback|熔断", 0.5),
    ],
    "technical-writer": [
        (r"技术文档|technical writ|文档|documentation|API文档|readme|changelog", 0.7),
        (r"教程|tutorial|指南|guide|手册|manual|注释|comment", 0.5),
    ],
    "git-workflow-master": [
        (r"git|版本控制|version control|分支|branch|合并|merge|rebase|cherry-pick", 0.7),
        (r"gitflow|trunk-based|monorepo|submodule|hook|commit|tag", 0.5),
    ],
    "senior-developer": [
        (r"高级开发|senior|资深|全栈|fullstack|full-stack", 0.5),
    ],
    "rapid-prototyper": [
        (r"原型|prototype|快速开发|rapid|MVP|概念验证|POC|demo", 0.7),
    ],
    "incident-response-commander": [
        (r"事件响应|incident response|故障处理|postmortem|复盘|根因分析|root cause", 0.7),
    ],
    "threat-detection-engineer": [
        (r"威胁检测|threat detect|入侵检测|IDS|SIEM|日志分析|安全监控", 0.7),
    ],
    "wechat-mini-program-developer": [
        (r"微信小程序|mini program|小程序|wechat|wx|weixin", 0.8),
    ],
    "feishu-integration-developer": [
        (r"飞书|feishu|lark|机器人|bot|webhook|开放平台", 0.7),
    ],
    "cms-developer": [
        (r"CMS|内容管理|wordpress|strapi|headless|sanity", 0.7),
    ],
    "embedded-firmware-engineer": [
        (r"嵌入式|embedded|固件|firmware|单片机|MCU|RTOS|IoT|物联网", 0.7),
    ],
    "solidity-smart-contract-engineer": [
        (r"solidity|智能合约|smart contract|以太坊|ethereum|web3|DeFi|NFT", 0.8),
    ],
    "email-intelligence-engineer": [
        (r"邮件|email|SMTP|IMAP|邮件系统|newsletter", 0.7),
    ],
    "autonomous-optimization-architect": [
        (r"自动优化|autonomous|自适应|adaptive|自动化架构", 0.6),
    ],
    "filament-optimization-specialist": [
        (r"filament|laravel|PHP|后台管理|admin panel", 0.7),
    ],
    "ai-data-remediation-engineer": [
        (r"数据修复|data remediation|数据清洗|data clean|数据质量|data quality", 0.7),
    ],
    # design 子角色
    "brand-guardian": [
        (r"品牌|brand|品牌策略|brand strategy|品牌指南|brand guide|标志|logo", 0.7),
    ],
    "ui-designer": [
        (r"UI设计|ui design|界面设计|interface|组件设计|design system|设计系统", 0.7),
        (r"设计.*界面|界面.*设计|UI.*界面|界面.*UI|设计一个.*页面|页面设计", 0.6),
    ],
    "ux-architect": [
        (r"UX架构|ux architect|信息架构|information architecture|用户流程|user flow", 0.7),
    ],
    "ux-researcher": [
        (r"用户研究|ux research|用户访谈|interview|可用性测试|usability|问卷|survey", 0.7),
    ],
    "image-prompt-engineer": [
        (r"图片生成|image generat|AI绘画|midjourney|stable diffusion|dall-e|提示词|prompt", 0.7),
    ],
    "visual-storyteller": [
        (r"视觉叙事|visual story|信息图|infographic|数据可视化|data viz", 0.7),
    ],
    "inclusive-visuals-specialist": [
        (r"包容性|inclusive|多样性|diversity|无障碍设计|accessible design", 0.7),
    ],
    "whimsy-injector": [
        (r"趣味|whimsy|微交互|micro-?interaction|动效|animation|彩蛋|easter egg", 0.7),
    ],
    # marketing 子角色
    "seo-specialist": [
        (r"SEO|搜索引擎优化|关键词|keyword|排名|ranking|外链|backlink", 0.7),
    ],
    "content-creator": [
        (r"内容创作|content creat|文案|copywriting|博客|blog|文章|article", 0.7),
    ],
    "growth-hacker": [
        (r"增长|growth|获客|acquisition|留存|retention|转化|conversion|漏斗|funnel", 0.7),
    ],
    "douyin-strategist": [
        (r"抖音|douyin|短视频|short video|直播|livestream", 0.8),
    ],
    "xiaohongshu-specialist": [
        (r"小红书|xiaohongshu|种草|笔记|note", 0.8),
    ],
    "wechat-official-account": [
        (r"微信公众号|official account|公众号|订阅号|服务号", 0.8),
    ],
    # testing 子角色
    "api-tester": [
        (r"API测试|api test|接口测试|postman|swagger|openapi", 0.7),
    ],
    "performance-benchmarker": [
        (r"性能测试|performance test|压测|benchmark|JMeter|k6|locust|负载测试", 0.7),
    ],
    "accessibility-auditor": [
        (r"无障碍审计|accessibility audit|WCAG|a11y|屏幕阅读器|screen reader", 0.7),
    ],
}

# 预编译所有模式
_compiled_category: dict[str, list[tuple[re.Pattern, float]]] = {
    cat: [(re.compile(pat, re.IGNORECASE), w) for pat, w in pairs]
    for cat, pairs in _CATEGORY_KEYWORDS.items()
}

_compiled_role: dict[str, list[tuple[re.Pattern, float]]] = {
    role: [(re.compile(pat, re.IGNORECASE), w) for pat, w in pairs]
    for role, pairs in _ROLE_KEYWORD_PATTERNS.items()
}


class AgencyAgentsLoader:
    """扫描 agency-agents 目录，构建角色索引"""

    def __init__(self, agents_dir: str | None = None):
        self._agents_dir = agents_dir or self._find_agents_dir()
        self._roles: list[AgentRole] = []
        if self._agents_dir:
            self._scan()

    @staticmethod
    def _find_agents_dir() -> str | None:
        """自动查找 agency-agents 目录

        搜索优先级：
        1. 环境变量 AGENCY_AGENTS_DIR
        2. 当前工作目录下的 agency-agents/
        3. ox-agent 项目内的 agency-agents/（clone 到同级目录）
        4. 用户主目录下的常见位置
        """
        candidates = [
            "agency-agents",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agency-agents"),
            os.path.join(os.getcwd(), "agency-agents"),
            os.path.expanduser("~/agency-agents"),
            os.path.expanduser("~/AI/agency-agents"),
        ]
        # 环境变量优先
        env_dir = os.environ.get("AGENCY_AGENTS_DIR")
        if env_dir:
            candidates.insert(0, env_dir)
        for d in candidates:
            if os.path.isdir(d):
                return os.path.abspath(d)
        return None

    def _scan(self):
        """扫描所有子目录中的 .md 文件"""
        self._roles = []
        if not self._agents_dir or not os.path.isdir(self._agents_dir):
            return

        # 跳过非角色目录
        skip_dirs = {".git", ".github", "examples", "integrations", "scripts", "strategy"}

        for category in sorted(os.listdir(self._agents_dir)):
            cat_path = os.path.join(self._agents_dir, category)
            if not os.path.isdir(cat_path) or category.startswith(".") or category in skip_dirs:
                continue
            for fname in sorted(os.listdir(cat_path)):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(cat_path, fname)
                try:
                    role = self._parse_role(fpath, category, fname)
                    if role:
                        self._roles.append(role)
                except Exception:
                    pass

        # 也扫描子目录中的子目录（如 game-development/unity/）
        for category in sorted(os.listdir(self._agents_dir)):
            cat_path = os.path.join(self._agents_dir, category)
            if not os.path.isdir(cat_path) or category.startswith(".") or category in skip_dirs:
                continue
            for sub in sorted(os.listdir(cat_path)):
                sub_path = os.path.join(cat_path, sub)
                if not os.path.isdir(sub_path):
                    continue
                for fname in sorted(os.listdir(sub_path)):
                    if not fname.endswith(".md"):
                        continue
                    fpath = os.path.join(sub_path, fname)
                    try:
                        role = self._parse_role(fpath, f"{category}/{sub}", fname)
                        if role:
                            self._roles.append(role)
                    except Exception:
                        pass

    def _parse_role(self, filepath: str, category: str, filename: str) -> AgentRole | None:
        """解析单个角色文件"""
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return None

        meta, body = _parse_front_matter(content)
        name = meta.get("name", filename.replace(".md", ""))
        description = meta.get("description", "")
        emoji = meta.get("emoji", "")

        if not body:
            return None

        # 从文件名提取角色标识（去掉 category 前缀）
        role_id = filename.replace(".md", "")
        # 去掉 category 前缀，如 engineering-frontend-developer → frontend-developer
        base_category = category.split("/")[0]
        if role_id.startswith(base_category + "-"):
            role_id = role_id[len(base_category) + 1:]

        # 构建关键词列表（从 name + description 提取）
        keywords = self._extract_keywords(name, description, role_id)

        return AgentRole(
            name=name,
            description=description,
            category=category,
            filename=filename,
            filepath=filepath,
            emoji=emoji,
            body=body,
            keywords=keywords,
        )

    @staticmethod
    def _extract_keywords(name: str, description: str, role_id: str) -> list[str]:
        """从名称和描述中提取关键词"""
        text = f"{name} {description} {role_id}".lower()
        # 提取中文词和英文词
        cn_words = re.findall(r'[\u4e00-\u9fff]+', text)
        en_words = re.findall(r'[a-z][a-z0-9]+', text)
        # 去重
        return list(set(cn_words + en_words))

    @property
    def roles(self) -> list[AgentRole]:
        return self._roles

    @property
    def available(self) -> bool:
        return len(self._roles) > 0

    def get_role(self, name: str) -> AgentRole | None:
        """按名称查找角色"""
        for r in self._roles:
            if r.name == name or r.filename.replace(".md", "") == name:
                return r
        return None

    def list_roles(self) -> list[dict]:
        """列出所有角色"""
        return [
            {
                "name": r.name,
                "category": r.category,
                "description": r.description[:80],
                "emoji": r.emoji,
            }
            for r in self._roles
        ]

    def reload(self):
        """重新扫描"""
        self._scan()


class AgencyMatcher:
    """基于用户输入匹配最合适的 agency-agents 角色

    匹配算法：
    1. 先按 category 关键词计算大类分数
    2. 再按 role 关键词计算角色分数
    3. 综合分数 = category 分数 × 0.3 + role 分数 × 0.7
    4. 超过阈值的最高分角色胜出
    """

    # 匹配阈值：低于此分数不匹配任何角色
    MATCH_THRESHOLD = 0.35
    # 切换阈值：新角色分数必须比当前角色高出此值才切换（防止频繁切换）
    SWITCH_MARGIN = 0.15

    def __init__(self, loader: AgencyAgentsLoader):
        self._loader = loader
        self._current_role: AgentRole | None = None
        self._current_score: float = 0.0

    def match(self, user_input: str) -> AgentRole | None:
        """匹配用户输入到最合适的角色

        返回匹配到的角色，或 None（无匹配 / 分数不够）。
        内置防抖：如果当前已有角色且新匹配分数不够高，保持当前角色。
        """
        if not self._loader.available:
            return self._current_role

        best_role: AgentRole | None = None
        best_score: float = 0.0

        for role in self._loader.roles:
            score = self._score_role(user_input, role)
            if score > best_score:
                best_score = score
                best_role = role

        # 不够阈值，不匹配
        if best_score < self.MATCH_THRESHOLD:
            return self._current_role

        # 防抖：如果已有角色，新角色必须明显更好才切换
        if self._current_role and best_role:
            if best_role.name == self._current_role.name:
                # 同一个角色，更新分数
                self._current_score = best_score
                return self._current_role
            if best_score < self._current_score + self.SWITCH_MARGIN:
                # 新角色不够好，保持当前
                return self._current_role

        # 切换到新角色
        self._current_role = best_role
        self._current_score = best_score
        return best_role

    def _score_role(self, user_input: str, role: AgentRole) -> float:
        """计算用户输入与角色的匹配分数"""
        text = user_input.lower()

        # 1. Category 分数
        cat_score = 0.0
        base_cat = role.category.split("/")[0]
        if base_cat in _compiled_category:
            for pattern, weight in _compiled_category[base_cat]:
                if pattern.search(text):
                    cat_score = max(cat_score, weight)

        # 2. Role 分数
        role_score = 0.0
        # 从文件名提取 role_id
        role_id = role.filename.replace(".md", "")
        base_category = role.category.split("/")[0]
        if role_id.startswith(base_category + "-"):
            role_id = role_id[len(base_category) + 1:]

        if role_id in _compiled_role:
            hit_count = 0
            for pattern, weight in _compiled_role[role_id]:
                if pattern.search(text):
                    role_score = max(role_score, weight)
                    hit_count += 1
            # 多关键词命中加成
            if hit_count > 1:
                role_score = min(role_score + hit_count * 0.05, 1.0)

        # 3. 关键词直接匹配（从 name/description 提取的词）
        keyword_score = 0.0
        for kw in role.keywords:
            if len(kw) >= 2 and kw in text:
                keyword_score = max(keyword_score, 0.3)

        # 4. description 中的关键短语匹配
        desc_score = 0.0
        if role.description:
            desc_lower = role.description.lower()
            # 提取 description 中的关键短语（3字以上的中文词或英文词组）
            desc_phrases = re.findall(r'[\u4e00-\u9fff]{3,}', desc_lower)
            desc_phrases += re.findall(r'[a-z][a-z ]{4,}[a-z]', desc_lower)
            for phrase in desc_phrases:
                if phrase.strip() in text:
                    desc_score = max(desc_score, 0.4)

        # 综合分数
        final = (cat_score * 0.2 + role_score * 0.5 +
                 keyword_score * 0.15 + desc_score * 0.15)
        return final

    @property
    def current_role(self) -> AgentRole | None:
        return self._current_role

    def reset(self):
        """重置匹配状态"""
        self._current_role = None
        self._current_score = 0.0


# ── 全局单例 ────────────────────────────────────────────────

_loader: AgencyAgentsLoader | None = None
_matcher: AgencyMatcher | None = None


def get_agency_loader(agents_dir: str | None = None) -> AgencyAgentsLoader:
    global _loader
    if _loader is None:
        _loader = AgencyAgentsLoader(agents_dir)
    return _loader


def get_agency_matcher(agents_dir: str | None = None) -> AgencyMatcher:
    global _matcher
    if _matcher is None:
        loader = get_agency_loader(agents_dir)
        _matcher = AgencyMatcher(loader)
    return _matcher
