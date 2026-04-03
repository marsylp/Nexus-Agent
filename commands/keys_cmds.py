"""Key 管理命令: /keys"""
from __future__ import annotations


def handle_keys(cmd: str):
    from agent_core.llm import get_auth_manager, PROVIDERS
    auth = get_auth_manager()
    parts = cmd.strip().split(maxsplit=2)
    sub = parts[1] if len(parts) > 1 else ""

    if sub == "add" and len(parts) > 2:
        kv = parts[2].strip()
        if ":" not in kv:
            print("  用法: /keys add <provider>:<api_key>\n")
            return
        provider, key = kv.split(":", 1)
        provider = provider.strip().lower()
        if provider not in PROVIDERS:
            print(f"  ❌ 未知提供商: {provider}\n")
            return
        auth.add_key(provider, key.strip())
        print(f"  ✅ 已添加 {provider} 的 API Key\n")
        return

    for name in PROVIDERS:
        keys = auth.get_all_keys(name)
        print(f"  🔑 {name}: {len(keys)} 个 Key")
    print(f"\n  💡 /keys add <provider>:<api_key>\n")
