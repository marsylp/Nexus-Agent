"""首次启动配置向导 — 交互式引导用户配置 API Key"""
from __future__ import annotations
import os

ENV_LOCAL_PATH = ".env.local"

PROVIDERS_INFO = [
    ("deepseek", "DEEPSEEK_API_KEY", "DeepSeek", "https://platform.deepseek.com"),
    ("zhipu", "ZHIPU_API_KEY", "智谱 GLM（glm-4-flash 免费）", "https://open.bigmodel.cn"),
    ("silicon", "SILICON_API_KEY", "SiliconFlow（多个开源模型免费）", "https://siliconflow.cn"),
    ("openai", "OPENAI_API_KEY", "OpenAI", "https://platform.openai.com"),
]


def needs_setup() -> bool:
    """检查是否需要运行配置向导"""
    return not os.path.exists(ENV_LOCAL_PATH)


def run_wizard() -> bool:
    """运行交互式配置向导，返回是否成功配置"""
    print("=" * 50)
    print("🎉 欢迎使用 Nexus Agent!")
    print("=" * 50)
    print("\n首次运行需要配置 API Key。")
    print("你可以随时在 .env.local 文件中修改配置。\n")

    lines = [
        "# Nexus Agent 本地敏感配置（自动生成，勿提交到 Git）\n",
    ]
    configured_any = False

    for provider, env_key, display_name, url in PROVIDERS_INFO:
        print(f"  [{display_name}]")
        print(f"  注册地址: {url}")
        key = input(f"  请输入 {env_key}（回车跳过）: ").strip()
        if key:
            lines.append(f"{env_key}={key}\n")
            configured_any = True
            print(f"  ✅ 已配置\n")
        else:
            lines.append(f"# {env_key}=\n")
            print(f"  ⏭️  已跳过\n")

    # Ollama 本地模型
    print("  [Ollama 本地模型（完全免费，需先安装 Ollama）]")
    print("  安装地址: https://ollama.ai")
    use_ollama = input("  是否使用 Ollama? [y/N]: ").strip().lower()
    if use_ollama in ("y", "yes"):
        model = input("  模型名称（默认 qwen2.5:7b）: ").strip() or "qwen2.5:7b"
        lines.append(f"# OLLAMA_MODEL={model}\n")
        configured_any = True
        print(f"  ✅ 已配置\n")

    if not configured_any:
        print("⚠️  未配置任何 API Key，Agent 将无法调用 LLM。")
        print(f"   你可以稍后编辑 {ENV_LOCAL_PATH} 添加配置。\n")

    # 写入文件
    with open(ENV_LOCAL_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print(f"✅ 配置已保存到 {ENV_LOCAL_PATH}")
    print("─" * 50)
    print()
    return configured_any


def check_and_setup():
    """检查配置，必要时运行向导"""
    if needs_setup():
        run_wizard()
