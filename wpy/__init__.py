# -*- coding: utf-8 -*-
"""
微北洋湖底 · 本地数据库版看板
=============================
数据在 SQLite，浏览走 Web 看板；抓取只是可选的更新通道。

本地：
    python crawl.py serve
    python -m wpy serve --port 8765

服务器（公网）：
    WPY_BOARD_TOKEN=口令 python -m wpy serve --host 0.0.0.0 --port 8765 --no-open
    定时更新：WPY_ACCOUNT=... WPY_PASSWORD=... python -m wpy update --no-confirm
"""

from .cli import main

__all__ = ["main"]
