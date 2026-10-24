# -*- coding: utf-8 -*-
"""热榜独立 HTTP 服务。

只暴露 /hot、/hot.html、/healthz 和图片代理，
API 请求（/api/*）反代到看板服务（8765），
共享同一份数据库和构建产物，
但监听独立端口，方便面板独立监控。
"""
import os
import socketserver
import urllib.parse
import hashlib
import hmac
import json
import time
import threading
import sqlite3
import http.server
import urllib.request

from .config import (
    ASSET_DIR, BOARD_TOKEN, DEFAULT_HOST, HOT_PATH,
    IMG_DIR, PIC_API, DB_PATH,
)
from .content_type import guess_type
from .http import get_bytes

# 单张图上限
_IMG_MAX_BYTES = 12 * 1024 * 1024
_IMG_LOCKS_MAX = 5000
_img_locks = {}
_img_locks_mu = threading.Lock()

# 1x1 透明 PNG
_TRANSPARENT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


def _img_lock(name):
    with _img_locks_mu:
        lk = _img_locks.get(name)
        if lk is None:
            if len(_img_locks) >= _IMG_LOCKS_MAX:
                to_remove = list(_img_locks.keys())[:_IMG_LOCKS_MAX // 2]
                for k in to_remove:
                    del _img_locks[k]
            lk = threading.Lock()
            _img_locks[name] = lk
        return lk


class _Handler(http.server.BaseHTTPRequestHandler):
    token = ""
    hot_path = HOT_PATH

    def log_message(self, *a):
        pass

    def _read_html(self, path=None):
        try:
            with open(path or self.hot_path, "rb") as f:
                return f.read()
        except OSError:
            return b'<h1>Hot file not found, run: python -m wpy build</h1>'

    def _send(self, body, ctype, code=200, extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra_headers or {}).items():
            if isinstance(v, (list, tuple)):
                for one in v:
                    self.send_header(k, one)
            else:
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(body, "application/json; charset=utf-8", code)

    # ---------- 分区排行 API ----------
    _BOARD_MAP = {
        1: "校务专区", 2: "青年湖底", 3: "学习新知", 4: "交友恋爱",
        5: "二次元", 6: "摆摊", 8: "数码科技", 13: "美食专区",
    }
    _COL_MAP = {"view": "浏览量", "like": "点赞数", "comment": "评论数", "fav": "收藏数"}
    _WINDOW_SQL = {
        "day":    "replace(substr(发布时间,1,19),'T',' ') > datetime('now','localtime','-1 day')",
        "week":   "replace(substr(发布时间,1,19),'T',' ') > datetime('now','localtime','-7 days')",
        "month":  "replace(substr(发布时间,1,19),'T',' ') > datetime('now','localtime','-30 days')",
    }

    def _do_board_ranking(self):
        """GET /api/board-ranking?board=5&dim=view&window=day&limit=50"""
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        board_id = (qs.get("board") or [""])[0]
        dim = (qs.get("dim") or ["view"])[0]
        window = (qs.get("window") or ["all"])[0]
        limit = int((qs.get("limit") or ["50"])[0])

        if not board_id or not board_id.isdigit():
            return self._json({"error": "缺少 board 参数"}, 400)
        board_id = int(board_id)
        if dim not in self._COL_MAP:
            return self._json({"error": "dim 必须是 view/like/comment/fav"}, 400)

        col = self._COL_MAP[dim]
        board_name = self._BOARD_MAP.get(board_id, "")

        db = sqlite3.connect(str(DB_PATH))
        db.row_factory = sqlite3.Row
        cur = db.cursor()

        if window in self._WINDOW_SQL:
            where = f"板块ID = {board_id} AND {self._WINDOW_SQL[window]}"
        else:
            where = f"板块ID = {board_id}"

        rows = cur.execute(
            f"SELECT id, 标题, 作者, 分管部门, 板块ID, 点赞数, 评论数, 收藏数, 浏览量, 发布时间 "
            f"FROM posts WHERE {where} ORDER BY {col} DESC LIMIT ?",
            (limit,)
        ).fetchall()
        db.close()

        def fmt_time(s):
            if not s:
                return ""
            return s[:16].replace("T", " ")

        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "标题": r["标题"],
                "作者": r["作者"],
                "分管部门": r["分管部门"],
                "板块ID": r["板块ID"],
                "点赞数": r["点赞数"] or 0,
                "评论数": r["评论数"] or 0,
                "收藏数": r["收藏数"] or 0,
                "浏览量": r["浏览量"] or 0,
                "发布时间": fmt_time(r["发布时间"]),
            })

        return self._json({
            "board": board_id,
            "boardName": board_name,
            "dim": dim,
            "window": window,
            "data": result,
        })

    def _query_token(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        return (qs.get("token") or [""])[0]

    def _session_sig(self, role):
        return hmac.new(self.token.encode("utf-8"), role.encode("utf-8"),
                        hashlib.sha256).hexdigest()[:32]

    def _role(self):
        if not self.token:
            return "admin"
        # 检查 URL token
        if self._query_token() == self.token:
            return "admin"
        # 检查 cookie 会话
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            part = part.strip()
            if part.startswith("wpy_session="):
                val = part.split("=", 1)[1]
                if "." in val:
                    role, sig = val.split(".", 1)
                    if role in ("admin", "guest") and hmac.compare_digest(
                            sig, self._session_sig(role)):
                        return role
        return ""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # 健康检查
        if path == "/healthz":
            return self._json({"ok": True})

        # 分区排行 API
        if path == "/api/board-ranking":
            return self._do_board_ranking()

        # 热榜页面：公开可访问（与 board 服务一致）
        if path in ("/", "/hot", "/hot.html"):
            return self._send(self._read_html(), "text/html; charset=utf-8")

        # API 请求：反代到看板服务（8765），跳过本地鉴权
        if path.startswith("/api/"):
            return self._proxy_api(path, parsed.query)

        # 鉴权（仅对非 API 的非页面请求）
        role = self._role()
        if self.token and not role:
            return self._json({"error": "unauthorized"}, 401)

        # 图标
        if path in ("/apple-touch-icon.png", "/icon-192.png", "/icon-512.png"):
            icon = os.path.join(ASSET_DIR, path.lstrip("/"))
            if os.path.exists(icon):
                with open(icon, "rb") as f:
                    data = f.read()
                return self._send(data, "image/png", 200, {
                    "Cache-Control": "public, max-age=86400",
                })
            return self._send(b"not found", "text/plain", 404)

        # manifest
        if path == "/manifest.json":
            manifest = '{"name":"\u9752\u5e74\u6e56\u70ed\u699c","short_name":"\u70ed\u699c","icons":[{"src":"/icon-192.png","sizes":"192x192","type":"image/png"},{"src":"/icon-512.png","sizes":"512x512","type":"image/png"}],"display":"standalone","background_color":"#f5f7fa","theme_color":"#2b7fff"}'
            return self._send(manifest.encode(), "application/manifest+json")

        # 图片代理
        if path.startswith("/img/"):
            return self._serve_img(path)

        self._send(b"not found", "text/plain; charset=utf-8", 404)

    def _proxy_api(self, path, query_string):
        """将 API 请求代理到看板服务（8765）。"""
        board_url = "http://127.0.0.1:8765%s" % path
        if query_string:
            board_url += "?" + query_string
        try:
            headers = {
                "User-Agent": "wpy-hot-proxy/1.0",
                "Cookie": self.headers.get("Cookie", ""),
            }
            # 如果请求没有带 token 参数，用 token cookie 代替
            if not query_string or "token=" not in query_string:
                if self.token:
                    cookie_val = headers.get("Cookie", "")
                    if cookie_val:
                        cookie_val += "; "
                    cookie_val += "wpy_token=" + self.token
                    headers["Cookie"] = cookie_val
            req = urllib.request.Request(board_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read()
                ctype = resp.headers.get("Content-Type", "application/json")
                resp_headers = {}
                cache = resp.headers.get("Cache-Control")
                if cache:
                    resp_headers["Cache-Control"] = cache
                set_cookie = resp.headers.get("Set-Cookie")
                if set_cookie:
                    resp_headers["Set-Cookie"] = set_cookie
                return self._send(body, ctype, resp.status, resp_headers)
        except Exception as e:
            return self._json({"error": str(e)[:200]}, 502)

    def _serve_img(self, path):
        rest = path[5:]
        parts = rest.split("/", 1)
        if len(parts) == 2 and parts[0] in ("thumb", "origin"):
            size = parts[0]
            name = urllib.parse.unquote(parts[1])
        else:
            size = "origin"
            name = urllib.parse.unquote(rest)
        name = os.path.basename(name)
        if not name or name in (".", ".."):
            return self._send(b"bad name", "text/plain", 400)
        ctype = guess_type(name)
        cache = {"Cache-Control": "public, max-age=604800, immutable"}

        size_dir = os.path.join(IMG_DIR, size)
        os.makedirs(size_dir, exist_ok=True)
        local = os.path.join(size_dir, name)
        legacy = os.path.join(IMG_DIR, name)
        if not os.path.exists(local) and size == "origin" and os.path.exists(legacy):
            local = legacy
        if os.path.exists(local):
            try:
                with open(local, "rb") as f:
                    data = f.read()
                return self._send(data, ctype, 200, cache)
            except OSError:
                pass

        with _img_lock(size + "/" + name):
            if os.path.exists(local):
                try:
                    with open(local, "rb") as f:
                        data = f.read()
                    return self._send(data, ctype, 200, cache)
                except OSError:
                    pass
            try:
                url = PIC_API + size + "/" + urllib.parse.quote(name)
                data = get_bytes(
                    url,
                    headers={"User-Agent": "wpy-hot/1.0"},
                    timeout=25,
                    throttle=False,
                    max_bytes=_IMG_MAX_BYTES,
                )
                tmp = local + ".part"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, local)
                return self._send(data, ctype, 200, cache)
            except Exception as e:
                print("  图片 %s 获取失败: %s" % (name, str(e)[:70]))
                return self._send(_TRANSPARENT_PNG, "image/png", 200,
                                  {"Cache-Control": "public, max-age=60"})


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(host=None, port=None, token=None):
    host = host or DEFAULT_HOST
    port = port or int(os.environ.get("WPY_HOT_PORT", "8766"))
    token = token or BOARD_TOKEN

    if not os.path.exists(HOT_PATH):
        print("热榜文件不存在: %s" % HOT_PATH)
        print("请先运行: python -m wpy build")
        return

    handler = type("HotHandler", (_Handler,), {"token": token or "", "hot_path": HOT_PATH})

    print()
    print("=" * 60)
    print("  热榜服务已启动")
    print("=" * 60)
    print("  监听: %s:%d" % (host, port))
    url = "http://%s:%d/" % (host, port)
    if token:
        url += "?token=" + token
    print("  本机打开: %s" % url)
    print("  健康检查: /healthz")
    print("  按 Ctrl+C 停止。")
    print("=" * 60)
    print()

    try:
        with Server((host, port), handler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    except OSError as e:
        print("启动失败（端口 %d 可能被占用）: %s" % (port, e))
