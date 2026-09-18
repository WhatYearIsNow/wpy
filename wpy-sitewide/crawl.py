# -*- coding: utf-8 -*-
"""兼容入口：python crawl.py serve / update / ...（与 python -m wpy 等价）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wpy.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main() or 0)
