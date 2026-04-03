from setuptools import setup, find_packages

setup(
    name="nexus-agent",
    version="0.6.0",
    description="轻量级 AI Agent 开发框架，内置 Harness Engineering 驾驭层",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "openai>=1.0.0",
        "python-dotenv>=1.0.0",
        "tiktoken>=0.7.0",
        "requests>=2.28.0",
    ],
    extras_require={
        "html": ["beautifulsoup4>=4.12.0"],
        "dev": ["pytest>=7.0.0", "mypy>=1.0.0"],
    },
    entry_points={
        "console_scripts": [
            "nexus-agent=main:main",
        ],
    },
)
