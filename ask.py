#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微北洋学习板块智能问答（兼容入口）。

用法:
    python3 ask.py "你的问题"          # 旧方式
    python -m wpy ask "你的问题"       # 新方式（推荐）

注意: 此脚本仅为兼容旧用法保留，推荐使用 `python -m wpy ask`。
"""

import sys
import os

# 兼容旧用法：从 wpy-sitewide 目录直接运行
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from wpy.ask import main  # noqa: E404

if __name__ == "__main__":
    main()
