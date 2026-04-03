"""Agency Agents 中文名称映射

将 agency-agents 仓库中的英文角色名翻译为中文。

角色来源：
  agency-agents — https://github.com/msitarzewski/agency-agents
  Copyright (c) 2025 AgentLand Contributors, MIT License
匹配规则：先按 name 精确匹配，再按 filename 匹配。
未匹配到的角色保留英文名。
"""

# 角色名 → 中文名
ROLE_NAME_ZH: dict[str, str] = {
    # academic
    "Anthropologist": "人类学家",
    "Geographer": "地理学家",
    "Historian": "历史学家",
    "Narratologist": "叙事学家",
    "Psychologist": "心理学家",

    # design
    "Brand Guardian": "品牌守护者",
    "Image Prompt Engineer": "图像提示词工程师",
    "Inclusive Visuals Specialist": "包容性视觉专家",
    "UI Designer": "UI 设计师",
    "UX Architect": "UX 架构师",
    "UX Researcher": "UX 研究员",
    "Visual Storyteller": "视觉叙事师",
    "Whimsy Injector": "趣味注入师",

    # engineering
    "AI Data Remediation Engineer": "AI 数据修复工程师",
    "AI Engineer": "AI 工程师",
    "Autonomous Optimization Architect": "自动优化架构师",
    "Backend Architect": "后端架构师",
    "CMS Developer": "CMS 开发者",
    "Code Reviewer": "代码审查员",
    "Data Engineer": "数据工程师",
    "Database Optimizer": "数据库优化师",
    "DevOps Automator": "DevOps 自动化师",
    "Email Intelligence Engineer": "邮件智能工程师",
    "Embedded Firmware Engineer": "嵌入式固件工程师",
    "Feishu Integration Developer": "飞书集成开发者",
    "Filament Optimization Specialist": "Filament 优化专家",
    "Frontend Developer": "前端开发者",
    "Git Workflow Master": "Git 工作流大师",
    "Incident Response Commander": "事件响应指挥官",
    "Mobile App Builder": "移动应用开发者",
    "Rapid Prototyper": "快速原型师",
    "Security Engineer": "安全工程师",
    "Senior Developer": "高级开发者",
    "Software Architect": "软件架构师",
    "Solidity Smart Contract Engineer": "Solidity 智能合约工程师",
    "SRE": "SRE 可靠性工程师",
    "Technical Writer": "技术写作者",
    "Threat Detection Engineer": "威胁检测工程师",
    "WeChat Mini Program Developer": "微信小程序开发者",

    # game-development
    "Game Audio Engineer": "游戏音频工程师",
    "Game Designer": "游戏设计师",
    "Level Designer": "关卡设计师",
    "Narrative Designer": "叙事设计师",
    "Technical Artist": "技术美术师",

    # marketing
    "AI Citation Strategist": "AI 引用策略师",
    "App Store Optimizer": "应用商店优化师",
    "Baidu SEO Specialist": "百度 SEO 专家",
    "Bilibili Content Strategist": "B站内容策略师",
    "Book Co-Author": "图书联合作者",
    "Carousel Growth Engine": "轮播增长引擎",
    "China E-commerce Operator": "中国电商运营师",
    "China Market Localization Strategist": "中国市场本地化策略师",
    "Content Creator": "内容创作者",
    "Cross-Border E-commerce": "跨境电商专家",
    "Douyin Strategist": "抖音策略师",
    "Growth Hacker": "增长黑客",
    "Instagram Curator": "Instagram 策展人",
    "Kuaishou Strategist": "快手策略师",
    "LinkedIn Content Creator": "LinkedIn 内容创作者",
    "Livestream Commerce Coach": "直播电商教练",
    "Podcast Strategist": "播客策略师",
    "Private Domain Operator": "私域运营师",
    "Reddit Community Builder": "Reddit 社区建设者",
    "SEO Specialist": "SEO 专家",
    "Short Video Editing Coach": "短视频剪辑教练",
    "Social Media Strategist": "社交媒体策略师",
    "TikTok Strategist": "TikTok 策略师",
    "Twitter Engager": "Twitter 互动专家",
    "Video Optimization Specialist": "视频优化专家",
    "WeChat Official Account": "微信公众号运营师",
    "Weibo Strategist": "微博策略师",
    "Xiaohongshu Specialist": "小红书专家",
    "Zhihu Strategist": "知乎策略师",

    # paid-media
    "Paid Media Auditor": "付费媒体审计师",
    "Ad Creative Strategist": "广告创意策略师",
    "Paid Social Strategist": "付费社交策略师",
    "PPC Campaign Strategist": "PPC 竞价策略师",
    "Programmatic & Display Buyer": "程序化广告买手",
    "Search Query Analyst": "搜索查询分析师",
    "Tracking & Measurement Specialist": "追踪与度量专家",

    # product
    "Behavioral Nudge Engine": "行为助推引擎",
    "Feedback Synthesizer": "反馈综合分析师",
    "Product Manager": "产品经理",
    "Sprint Prioritizer": "迭代优先级规划师",
    "Trend Researcher": "趋势研究员",

    # project-management
    "Experiment Tracker": "实验追踪师",
    "Jira Workflow Steward": "Jira 工作流管家",
    "Project Shepherd": "项目牧羊人",
    "Studio Operations": "工作室运营",
    "Studio Producer": "工作室制片人",
    "Senior Project Manager": "高级项目经理",

    # sales
    "Account Strategist": "客户策略师",
    "Sales Coach": "销售教练",
    "Deal Strategist": "交易策略师",
    "Discovery Coach": "需求发现教练",
    "Sales Engineer": "售前工程师",
    "Outbound Strategist": "外呼策略师",
    "Pipeline Analyst": "销售管道分析师",
    "Proposal Strategist": "提案策略师",

    # spatial-computing
    "macOS Spatial Metal Engineer": "macOS 空间 Metal 工程师",
    "Terminal Integration Specialist": "终端集成专家",
    "visionOS Spatial Engineer": "visionOS 空间工程师",
    "XR Cockpit Interaction Specialist": "XR 座舱交互专家",
    "XR Immersive Developer": "XR 沉浸式开发者",
    "XR Interface Architect": "XR 界面架构师",

    # support
    "Analytics Reporter": "数据分析报告师",
    "Executive Summary Generator": "高管摘要生成器",
    "Finance Tracker": "财务追踪师",
    "Infrastructure Maintainer": "基础设施维护师",
    "Legal Compliance Checker": "法律合规检查师",
    "Support Responder": "客服响应师",

    # testing
    "Accessibility Auditor": "无障碍审计师",
    "API Tester": "API 测试师",
    "Evidence Collector": "证据收集师",
    "Performance Benchmarker": "性能基准测试师",
    "Reality Checker": "现实检验师",
    "Test Results Analyzer": "测试结果分析师",
    "Tool Evaluator": "工具评估师",
    "Workflow Optimizer": "工作流优化师",

    # specialized
    "Accounts Payable Agent": "应付账款代理",
    "Agentic Identity & Trust": "代理身份与信任",
    "Agents Orchestrator": "代理编排师",
    "Automation Governance Architect": "自动化治理架构师",
    "Blockchain Security Auditor": "区块链安全审计师",
    "Compliance Auditor": "合规审计师",
    "Corporate Training Designer": "企业培训设计师",
    "Data Consolidation Agent": "数据整合代理",
    "Government Digital Presales Consultant": "政府数字化售前顾问",
    "Healthcare Marketing Compliance": "医疗营销合规师",
    "Identity Graph Operator": "身份图谱运营师",
    "LSP Index Engineer": "LSP 索引工程师",
    "Recruitment Specialist": "招聘专家",
    "Report Distribution Agent": "报告分发代理",
    "Sales Data Extraction Agent": "销售数据提取代理",
    "Civil Engineer": "土木工程师",
    "Cultural Intelligence Strategist": "文化智能策略师",
    "Developer Advocate": "开发者布道师",
    "Document Generator": "文档生成器",
    "French Consulting Market": "法国咨询市场专家",
    "Korean Business Navigator": "韩国商务导航师",
    "MCP Builder": "MCP 构建师",
    "Model QA": "模型质量保证师",
    "Salesforce Architect": "Salesforce 架构师",
    "Workflow Architect": "工作流架构师",
    "Study Abroad Advisor": "留学顾问",
    "Supply Chain Strategist": "供应链策略师",
    "ZK Steward": "ZK 管家",

    # ── 补充：front-matter name 与映射表 key 不一致的角色 ──

    # engineering
    "SRE (Site Reliability Engineer)": "SRE 可靠性工程师",

    # marketing（name 带后缀的变体）
    "China E-Commerce Operator": "中国电商运营师",
    "Cross-Border E-Commerce Specialist": "跨境电商专家",
    "Short-Video Editing Coach": "短视频剪辑教练",
    "WeChat Official Account Manager": "微信公众号运营师",

    # spatial-computing
    "macOS Spatial/Metal Engineer": "macOS 空间 Metal 工程师",

    # specialized
    "Agentic Identity & Trust Architect": "代理身份与信任架构师",
    "Healthcare Marketing Compliance Specialist": "医疗营销合规专家",
    "LSP/Index Engineer": "LSP 索引工程师",
    "French Consulting Market Navigator": "法国咨询市场导航师",
    "Model QA Specialist": "模型质量保证专家",

    # game-development/blender
    "Blender Add-on Engineer": "Blender 插件工程师",

    # game-development/godot
    "Godot Gameplay Scripter": "Godot 游戏脚本师",
    "Godot Multiplayer Engineer": "Godot 多人联机工程师",
    "Godot Shader Developer": "Godot 着色器开发者",

    # game-development/roblox-studio
    "Roblox Avatar Creator": "Roblox 角色创建师",
    "Roblox Experience Designer": "Roblox 体验设计师",
    "Roblox Systems Scripter": "Roblox 系统脚本师",

    # game-development/unity
    "Unity Architect": "Unity 架构师",
    "Unity Editor Tool Developer": "Unity 编辑器工具开发者",
    "Unity Multiplayer Engineer": "Unity 多人联机工程师",
    "Unity Shader Graph Artist": "Unity 着色器图表美术师",

    # game-development/unreal-engine
    "Unreal Multiplayer Architect": "Unreal 多人联机架构师",
    "Unreal Systems Engineer": "Unreal 系统工程师",
    "Unreal Technical Artist": "Unreal 技术美术师",
    "Unreal World Builder": "Unreal 世界构建师",
}


def get_zh_name(en_name: str) -> str:
    """获取中文名称，未找到则返回原名"""
    return ROLE_NAME_ZH.get(en_name, en_name)