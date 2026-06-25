import os
from pathlib import Path

import django
from dotenv import load_dotenv

import pytest

# 加载项目根目录的 .env 文件
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)


@pytest.fixture(scope="session", autouse=True)
def setup_django():
    """所有测试自动配置 Django ORM（session 级，仅执行一次）"""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_settings")
    os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key-for-tests")
    django.setup()


@pytest.fixture
def api_key():
    """API Key fixture，优先从环境变量读取"""
    return os.getenv("API_KEY", "test-key")


@pytest.fixture
def api_base():
    return os.getenv("API_BASE", "https://api.example.com/v1")
