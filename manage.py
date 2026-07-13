#!/usr/bin/env python
"""Django manage.py — 仅用于运行 migration 和 shell 等管理命令。"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main():
    load_dotenv(Path(__file__).resolve().parent / ".env")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
