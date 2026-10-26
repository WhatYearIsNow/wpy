# -*- coding: utf-8 -*-
"""统一的 Content-Type 映射。

替代 server.py 和 export_ops.py 中重复的 guess_type 实现。
"""

from __future__ import annotations

_CONTENT_TYPES: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}


def guess_type(name: str) -> str:
    """按文件扩展名返回 Content-Type。

    未知扩展名返回 application/octet-stream。
    """
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    # 兼容 os.path.splitext 的行为（处理 ".hidden" 等边缘情况）
    if ext == name:
        ext = ""
    return _CONTENT_TYPES.get(ext, "application/octet-stream")
