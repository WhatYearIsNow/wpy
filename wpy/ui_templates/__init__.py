# -*- coding: utf-8 -*-
"""看板前端模板资源。

CSS 和 JS 从 ui.py 拆分到这里，便于独立编辑和版本管理。
ui.py 的 build_html 在运行时读取这些文件。
"""
from __future__ import annotations

import os

# 模板目录（与 ui_templates/ 同级）
_TEMPLATE_DIR = os.path.dirname(os.path.abspath(__file__))


def _read(name: str) -> str:
    """读取模板文件。"""
    path = os.path.join(_TEMPLATE_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# CSS 和 JS 作为模块级常量缓存（避免每次导入都读文件）
_CSS: str | None = None
_JS: str | None = None


def get_css() -> str:
    """获取 CSS 内容（缓存）。"""
    global _CSS
    if _CSS is None:
        _CSS = _read("board.css")
    return _CSS


def get_js() -> str:
    """获取 JS 内容（缓存）。"""
    global _JS
    if _JS is None:
        _JS = _read("board.js")
    return _JS


# 兼容旧接口：ui.py 中仍使用 CSS / JS 常量名
CSS = get_css()
JS = get_js()
