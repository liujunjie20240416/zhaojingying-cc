import os
from pathlib import Path

import django
from dotenv import load_dotenv

import pytest

# 加载项目根目录的 .env 文件
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

# Unit tests must never export private prompts or depend on LangSmith network access.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"


@pytest.fixture(scope="session", autouse=True)
def setup_django():
    """所有测试自动配置 Django ORM（session 级，仅执行一次）"""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_settings")
    os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key-for-tests")
    django.setup()


@pytest.fixture
def api_key():
    """GLM integration key; local unit tests must not consume it."""
    return os.getenv("GLM_API_KEY", "test-key")


@pytest.fixture
def api_base():
    return os.getenv("GLM_API_BASE", "https://api.example.com/v1")


def pytest_collection_modifyitems(config, items):
    """Keep default test runs deterministic and free of external LLM calls."""
    if os.getenv("RUN_LLM_INTEGRATION_TESTS") == "1":
        return
    skip_llm = pytest.mark.skip(
        reason="set RUN_LLM_INTEGRATION_TESTS=1 to call the configured GLM API"
    )
    for item in items:
        if "llm_integration" in item.keywords:
            item.add_marker(skip_llm)
