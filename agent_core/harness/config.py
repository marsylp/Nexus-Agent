"""ConfigLoader — 配置加载与技术栈模板

读取 .harnessrc.json 配置文件，与默认配置深度合并，提供技术栈模板。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Any


# ── 数据类 ──────────────────────────────────────────────

@dataclass
class TechStackConfig:
    """技术栈配置模板"""
    name: str
    src_dir: str
    test_runner: str
    layers: list[str] = field(default_factory=list)
    naming: dict[str, str] = field(default_factory=dict)


@dataclass
class HarnessConfig:
    """完整的 Harness 配置"""
    tech_stack: str = "custom"
    src_dir: str = "src"
    test_runner: str = ""
    layers: list[str] = field(default_factory=list)
    naming: dict[str, str] = field(default_factory=dict)
    forbidden: list[dict] = field(default_factory=list)
    security: dict = field(default_factory=lambda: {
        "checkSecretLeak": True,
        "checkEval": True,
        "checkLocalStorage": True,
        "checkUrlToken": True,
        "checkXss": True,
    })
    i18n: dict = field(default_factory=lambda: {
        "enabled": False,
        "filePatterns": [],
        "ignorePatterns": [],
    })
    custom_constraints: list[dict] = field(default_factory=list)


# ── 内置技术栈模板 ──────────────────────────────────────

_TECH_STACK_TEMPLATES: dict[str, TechStackConfig] = {
    "vue3-ts": TechStackConfig(
        name="vue3-ts",
        src_dir="src",
        test_runner="npx vitest run",
        layers=["views", "components", "stores", "api", "utils"],
        naming={
            "views": r"^[A-Z][a-zA-Z]+View\.vue$",
            "components": r"^[A-Z][a-zA-Z]+Component\.vue$",
            "stores": r"^[a-z][a-zA-Z]+\.ts$",
        },
    ),
    "react-ts": TechStackConfig(
        name="react-ts",
        src_dir="src",
        test_runner="npx jest --passWithNoTests",
        layers=["pages", "components", "hooks", "services", "utils"],
        naming={
            "pages": r"^[A-Z][a-zA-Z]+Page\.tsx$",
            "components": r"^[A-Z][a-zA-Z]+\.tsx$",
            "hooks": r"^use[A-Z][a-zA-Z]+\.ts$",
        },
    ),
    "python": TechStackConfig(
        name="python",
        src_dir="app",
        test_runner="pytest",
        layers=["views", "services", "models", "utils"],
        naming={
            "views": r"^[a-z_]+\.py$",
            "services": r"^[a-z_]+\.py$",
            "models": r"^[a-z_]+\.py$",
        },
    ),
    "node-express": TechStackConfig(
        name="node-express",
        src_dir="src",
        test_runner="npx jest --passWithNoTests",
        layers=["routes", "controllers", "services", "models", "utils"],
        naming={
            "routes": r"^[a-z][a-zA-Z]+\.routes\.(ts|js)$",
            "controllers": r"^[a-z][a-zA-Z]+\.controller\.(ts|js)$",
            "models": r"^[a-z][a-zA-Z]+\.model\.(ts|js)$",
        },
    ),
    "android-kotlin": TechStackConfig(
        name="android-kotlin",
        src_dir="app/src/main/java",
        test_runner="./gradlew test",
        layers=["ui", "viewmodel", "repository", "data", "util"],
        naming={
            "ui": r"^[A-Z][a-zA-Z]+Activity\.kt$|^[A-Z][a-zA-Z]+Fragment\.kt$",
            "viewmodel": r"^[A-Z][a-zA-Z]+ViewModel\.kt$",
            "repository": r"^[A-Z][a-zA-Z]+Repository\.kt$",
        },
    ),
}


# ── 默认配置 ────────────────────────────────────────────

_DEFAULT_CONFIG: dict[str, Any] = {
    "techStack": "custom",
    "srcDir": "src",
    "testRunner": "",
    "layers": [],
    "naming": {},
    "forbidden": [],
    "security": {
        "checkSecretLeak": True,
        "checkEval": True,
        "checkLocalStorage": True,
        "checkUrlToken": True,
        "checkXss": True,
    },
    "i18n": {
        "enabled": False,
        "filePatterns": [],
        "ignorePatterns": [],
    },
    "customConstraints": [],
}


# ── 工具函数 ────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个字典，override 优先。"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _dict_to_config(d: dict) -> HarnessConfig:
    """将配置字典转换为 HarnessConfig 数据类。"""
    return HarnessConfig(
        tech_stack=d.get("techStack", "custom"),
        src_dir=d.get("srcDir", "src"),
        test_runner=d.get("testRunner", ""),
        layers=d.get("layers", []),
        naming=d.get("naming", {}),
        forbidden=d.get("forbidden", []),
        security=d.get("security", _DEFAULT_CONFIG["security"].copy()),
        i18n=d.get("i18n", _DEFAULT_CONFIG["i18n"].copy()),
        custom_constraints=d.get("customConstraints", []),
    )


def config_to_dict(config: HarnessConfig) -> dict:
    """将 HarnessConfig 序列化为 JSON 兼容字典。"""
    return {
        "techStack": config.tech_stack,
        "srcDir": config.src_dir,
        "testRunner": config.test_runner,
        "layers": config.layers,
        "naming": config.naming,
        "forbidden": config.forbidden,
        "security": config.security,
        "i18n": config.i18n,
        "customConstraints": config.custom_constraints,
    }


def config_from_dict(d: dict) -> HarnessConfig:
    """从字典反序列化为 HarnessConfig（load_config 的别名）。"""
    return _dict_to_config(d)


# ── 公共接口 ────────────────────────────────────────────

def load_config(cwd: str | None = None) -> HarnessConfig:
    """加载配置，合并默认值。cwd 默认 os.getcwd()。"""
    cwd = cwd or os.getcwd()
    config_path = os.path.join(cwd, ".harnessrc.json")

    if not os.path.isfile(config_path):
        return _dict_to_config(_DEFAULT_CONFIG)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
    except json.JSONDecodeError:
        print("⚠️  .harnessrc.json 包含无效 JSON，使用默认配置", file=sys.stderr)
        return _dict_to_config(_DEFAULT_CONFIG)
    except Exception as e:
        print(f"⚠️  读取 .harnessrc.json 失败: {e}，使用默认配置", file=sys.stderr)
        return _dict_to_config(_DEFAULT_CONFIG)

    if not isinstance(user_config, dict):
        print("⚠️  .harnessrc.json 顶层不是对象，使用默认配置", file=sys.stderr)
        return _dict_to_config(_DEFAULT_CONFIG)

    merged = _deep_merge(_DEFAULT_CONFIG, user_config)
    return _dict_to_config(merged)


def get_tech_stack_template(name: str) -> TechStackConfig | None:
    """获取内置技术栈模板，不存在返回 None。"""
    return _TECH_STACK_TEMPLATES.get(name)


def get_available_templates() -> list[str]:
    """返回所有可用的技术栈模板名称列表。"""
    return list(_TECH_STACK_TEMPLATES.keys())


def generate_default_config(tech_stack: str) -> dict:
    """根据技术栈生成默认 .harnessrc.json 内容字典。"""
    template = _TECH_STACK_TEMPLATES.get(tech_stack)
    if template is None:
        # custom 技术栈：最小化配置
        return {
            "techStack": "custom",
            "srcDir": "src",
            "testRunner": "",
            "layers": [],
            "naming": {},
            "forbidden": [],
            "security": _DEFAULT_CONFIG["security"].copy(),
            "i18n": _DEFAULT_CONFIG["i18n"].copy(),
            "customConstraints": [],
        }

    return {
        "techStack": template.name,
        "srcDir": template.src_dir,
        "testRunner": template.test_runner,
        "layers": template.layers[:],
        "naming": template.naming.copy(),
        "forbidden": [],
        "security": _DEFAULT_CONFIG["security"].copy(),
        "i18n": {
            "enabled": template.name in ("vue3-ts", "react-ts"),
            "filePatterns": ["*.vue"] if "vue" in template.name else [],
            "ignorePatterns": [r"console\.log", r"//.*", r"<!--.*-->"],
        },
        "customConstraints": [],
    }
