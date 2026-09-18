# -*- coding: utf-8 -*-
"""本地看板 HTTP 服务：页面 + /api/* + 图片代理。

只读服务：不写业务数据，可单独部署。
默认只监听 127.0.0.1；公网部署请 --host 0.0.0.0 并设置 WPY_BOARD_TOKEN。
"""
import http.server
import json
import os
import socketserver
import threading
import urllib.parse
import webbrowser

from .config import (
    BOARD_TOKEN, DEFAULT_HOST, DEFAULT_PORT, HTML_PATH, IMG_DIR, PIC_API,
)
from .board_api import BoardDB
from .cli import _ensure_utf8_stdio
from .http import get_bytes

# 单张图上限，防止 CDN 异常把内存拖垮
_IMG_MAX_BYTES = 12 * 1024 * 1024
# 同名图片并发下载只跑一次
_img_locks = {}
_img_locks_mu = threading.Lock()


def _img_lock(name):
    with _img_locks_mu:
        lk = _img_locks.get(name)
        if lk is None:
            lk = threading.Lock()
            _img_locks[name] = lk
        return lk


# 1x1 透明 PNG：图片取不到时兜底，避免破图
_TRANSPARENT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")

_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>访问验证</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f1419;color:#e7ecf3;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
.card{background:#1a222d;padding:28px 32px;border-radius:12px;width:min(360px,92vw)}
h1{font-size:18px;margin:0 0 8px}
p{font-size:13px;color:#9aa7b8;margin:0 0 18px;line-height:1.5}
input,button{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;
border:1px solid #2c3848;font-size:14px}
input{background:#0f1419;color:#e7ecf3;margin-bottom:12px}
button{background:#3b82f6;border:none;color:#fff;cursor:pointer}
button:hover{background:#2563eb}
</style></head><body>
<div class="card">
  <h1>青年湖底本地看板</h1>
  <p>本站设置了访问口令。请输入后继续。</p>
  <form method="GET" action="/">
    <input type="password" name="token" placeholder="访问口令" autofocus required>
    <button type="submit">进入</button>
  </form>
</div></body></html>"""


def guess_type(name):
    ext = os.path.splitext(name)[1].lower()
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".gif": "image/gif", ".webp": "image/webp",
            ".bmp": "image/bmp"}.get(ext, "application/octet-stream")


def _log(msg):
    print(msg, flush=True)


class _Handler(http.server.BaseHTTPRequestHandler):
    board = None
    token = ""

    def log_message(self, *a):
        pass

    def _cookie_token(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            part = part.strip()
            if part.startswith("wpy_token="):
                return part.split("=", 1)[1]
        return ""

    def _query_token(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        return (qs.get("token") or [""])[0]

    def _authed(self):
        if not self.token:
            return True
        return self._query_token() == self.token or self._cookie_token() == self.token

    def _send(self, body, ctype, code=200, extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        # 默认短缓存；图片等可在 extra_headers 覆盖
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        return self._send(body, "application/json; charset=utf-8", code)

    def read_html(self):
        try:
            with open(HTML_PATH, "rb") as f:
                return f.read()
        except OSError:
            return "<h1>看板文件未找到，请先 python -m wpy build</h1>".encode("utf-8")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # 健康检查不鉴权，方便反代/监控探活
        if path == "/healthz":
            return self._json({"ok": True})

        if self.token and not self._authed():
            if path in ("/", "/index.html"):
                return self._send(_LOGIN_PAGE, "text/html; charset=utf-8", 401)
            return self._json({"error": "unauthorized"}, 401)

        # 首次带 ?token= 进入时种 Cookie，之后刷新不用再带
        if self.token and self._query_token() == self.token and path in ("/", "/index.html"):
            html = self.read_html().decode("utf-8", errors="replace")
            return self._send(html, "text/html; charset=utf-8", 200, {
                "Set-Cookie": "wpy_token=%s; Path=/; HttpOnly; SameSite=Lax" % self.token,
            })

        if path in ("/", "/index.html"):
            return self._send(self.read_html(), "text/html; charset=utf-8")

        qs = urllib.parse.parse_qs(parsed.query)
        q = {k: (v[0] if v else "") for k, v in qs.items()}

        try:
            if path == "/api/init":
                return self._json(self.board.init_info())
            if path == "/api/posts":
                return self._json(self.board.posts(q))
            if path == "/api/post":
                pid = int(q.get("id", 0) or 0)
                flim = int(q.get("flim", 150) or 150)
                foff = int(q.get("foff", 0) or 0)
                d = self.board.post_detail(pid, floor_limit=flim, floor_offset=foff)
                return self._json(d) if d else self._json({"error": "not found"}, 404)
            if path == "/api/user":
                uid = int(q.get("uid", 0) or 0)
                return self._json(self.board.user(uid))
            if path == "/api/topusers":
                return self._json(self.board.top_users())
            if path == "/api/users":
                ids = [x for x in (q.get("uids", "") or "").split(",")
                       if x.strip().isdigit()]
                return self._json(self.board.users([int(x) for x in ids[:200]]))
        except Exception as e:
            return self._json({"error": str(e)[:200]}, 500)

        if path.startswith("/img/"):
            return self._serve_img(path)

        self._send(b"not found", "text/plain; charset=utf-8", 404)

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
            # 拿锁后再读一次：并发下可能刚好被别的线程下好了
            if os.path.exists(local):
                try:
                    with open(local, "rb") as f:
                        data = f.read()
                    return self._send(data, ctype, 200, cache)
                except OSError:
                    pass
            try:
                url = PIC_API + size + "/" + urllib.parse.quote(name)
                # 看板图片代理不走抓取限速，避免页面浏览被 QPS 拖慢
                data = get_bytes(
                    url,
                    headers={"User-Agent": "wpy-board/1.0"},
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
                _log("  图片 %s 获取失败：%s" % (name, str(e)[:70]))
                return self._send(_TRANSPARENT_PNG, "image/png", 200,
                                  {"Cache-Control": "public, max-age=60"})


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(host=None, port=None, open_browser=None, token=None, db_path=None):
    _ensure_utf8_stdio()
    from .config import DB_PATH
    host = host if host is not None else DEFAULT_HOST
    port = port if port is not None else DEFAULT_PORT
    token = BOARD_TOKEN if token is None else token
    if open_browser is None:
        open_browser = host in ("127.0.0.1", "localhost")

    if not os.path.exists(HTML_PATH):
        from .ui import build_html
        build_html(HTML_PATH)
    if not os.path.exists(db_path or DB_PATH):
        print("还没有数据库：%s" % (db_path or DB_PATH))
        return

    os.makedirs(IMG_DIR, exist_ok=True)
    board = BoardDB(db_path or DB_PATH, pic_base=PIC_API, use_proxy=True)
    handler = type("Handler", (_Handler,), {"board": board, "token": token or ""})

    url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = "http://%s:%d/" % (url_host, port)
    if token:
        url += "?token=" + token

    print()
    print("=" * 60)
    print("  看板服务已启动")
    print("=" * 60)
    print("  监听：%s:%d" % (host, port))
    print("  本机打开：%s" % url)
    if host in ("0.0.0.0", "::"):
        print("  公网访问：请配置域名反代，并设置 WPY_BOARD_TOKEN")
    if token:
        print("  已启用访问口令（环境变量 WPY_BOARD_TOKEN）")
    print("  健康检查：/healthz")
    print("  按 Ctrl+C 停止。")
    print("=" * 60)
    print()

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        with Server((host, port), handler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    except OSError as e:
        print("启动失败（端口 %d 可能被占用）：%s" % (port, e))
        print("换端口：python -m wpy serve --port 8899")
