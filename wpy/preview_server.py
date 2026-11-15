"""Dedicated read-only HTTP service for emailed post preview links."""

from __future__ import annotations

import os
import re
import urllib.parse

from .board_api import BoardDB
from .config import DEFAULT_HOST, DB_PATH, PIC_API
from .server import Server, _Handler
from .ui import render_html


_API_PATHS = frozenset(("/api/me", "/api/post", "/api/user", "/api/users"))
_STATIC_PATHS = frozenset(
    (
        "/apple-touch-icon.png",
        "/icon-192.png",
        "/icon-512.png",
        "/manifest.json",
    )
)


def allowed_preview_path(path):
    """Only expose post detail, its supporting read APIs, and images."""
    if path in _API_PATHS or path in _STATIC_PATHS or path == "/healthz":
        return True
    if path.startswith("/img/"):
        return True
    if path.startswith("/post/"):
        match = re.fullmatch(r"/post/([0-9]{1,20})/?", path)
        return bool(match and int(match[1]) > 0)
    return False


class _PostPreviewHandler(_Handler):
    public_read_only = True
    token = ""

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if not allowed_preview_path(path):
            return self._send("not found", "text/plain; charset=utf-8", 404)
        if path.startswith("/post/"):
            base_href = "../../" if path.endswith("/") else "../"
            return self._send(render_html(base_href=base_href), "text/html; charset=utf-8")
        return super().do_GET()

    def do_POST(self):
        return self._json({"error": "read only"}, 403)


def serve(host=DEFAULT_HOST, port=8767, db_path=None):
    """Serve the existing post detail UI on a separate, read-only process."""
    db_path = db_path or DB_PATH
    if not os.path.isfile(db_path):
        raise FileNotFoundError("帖子归档数据库不存在")
    board = BoardDB(db_path, pic_base=PIC_API, use_proxy=True, read_only=True)
    handler = type("PostPreviewHandler", (_PostPreviewHandler,), {"board": board})
    print(f"帖子预览服务已启动：{host}:{port}")
    print("只提供帖子详情、只读辅助接口和图片；写入与列表接口均关闭。")
    with Server((host, port), handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("帖子预览服务已停止。")
        finally:
            board.conn.close()
