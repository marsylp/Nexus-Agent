"""pytest 配置 — 确保路径和环境正确"""
import os, sys

# 确保项目根目录在 sys.path 中
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

# 测试环境中禁用 Ollama 探测（避免本地 Ollama 服务影响路由测试）
os.environ["DISABLE_OLLAMA"] = "1"

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
load_dotenv(os.path.join(ROOT, ".env.local"), override=True)

# 预加载 skills
from skills import load_all_skills
load_all_skills()
