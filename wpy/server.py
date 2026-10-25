# -*- coding: utf-8 -*-
"""本地看板 HTTP 服务：页面 + /api/* + 图片代理 + 湖底动作代理。

游客只读，登录用户以本人的微北洋会话操作论坛。
默认只监听 127.0.0.1；全站校园网访问边界由部署网关提供。
"""
import hmac
import html
import ipaddress
from http.cookies import SimpleCookie
import http.server
import json
import os
import socketserver
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import webbrowser

from .config import (
    ASSET_DIR, DB_PATH, DEFAULT_HOST, DEFAULT_PORT, HOT_PATH,
    HTML_PATH, IMG_DIR, LAB_PATH, PIC_API,
)
from . import auth
from .board_auth import Sessions, SESSION_TTL
from . import lake_actions
from . import ranking
from .board_api import BoardDB
from .cli import _ensure_utf8_stdio
from .http import get_bytes

# 单张图上限，防止 CDN 异常把内存拖垮
_IMG_MAX_BYTES = 12 * 1024 * 1024
# 同名图片并发下载只跑一次
# 限容：最多保留 5000 个锁，超过时清理最久未使用的。
# 防止长时间运行后锁字典无限增长。
_IMG_LOCKS_MAX = 5000
_img_locks = {}
_img_locks_mu = threading.Lock()

# 游客屏蔽名单：这些作者的帖子对游客不可见
# （环境变量 WPY_GUEST_BLOCK_UIDS，逗号分隔 uid，写在 deploy/env.board 里）
GUEST_BLOCK_UIDS = [u.strip() for u in
                    os.environ.get("WPY_GUEST_BLOCK_UIDS", "").split(",")
                    if u.strip().isdigit()]
_follows_mu = threading.Lock()

# 登录失败限速：公网上防止对账号登录的暴力尝试。
# 内存态，重启即清；10 分钟窗口内失败满 8 次暂时拒绝继续尝试。
_login_fails = {}
_login_fails_mu = threading.Lock()


def _local_peer(handler):
    try:
        return ipaddress.ip_address(handler.client_address[0]).is_loopback
    except ValueError:
        return False


def _client_ip(handler):
    # 仅信任本机反代；Caddy 追加的最后一跳是客户端地址。
    xff = handler.headers.get("X-Forwarded-For") or ""
    return (xff.split(",")[-1].strip() if xff and _local_peer(handler)
            else handler.client_address[0])


def _login_throttled(ip):
    now = time.time()
    with _login_fails_mu:
        hits = [t for t in _login_fails.get(ip, []) if now - t < 600]
        _login_fails[ip] = hits
        return len(hits) >= 8


def _login_fail(ip):
    now = time.time()
    with _login_fails_mu:
        hits = [t for t in _login_fails.get(ip, []) if now - t < 600]
        hits.append(now)
        _login_fails[ip] = hits
        # 防内存膨胀：只清理过期条目，不清理其他 IP 的记录。
        # 旧实现用 clear() 会误删所有 IP 的失败计数，导致暴力破解
        # 防护在长时间运行后形同虚设。
        if len(_login_fails) > 1000:
            for key in list(_login_fails):
                if not any(now - t < 600 for t in _login_fails[key]):
                    del _login_fails[key]


def _to_int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _follows_load(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _follows_save(path, d):
    tmp = path + ".part"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, path)


def _img_lock(name):
    with _img_locks_mu:
        lk = _img_locks.get(name)
        if lk is None:
            # 限容：超过上限时清理一半（LRU 近似：字典插入顺序即使用顺序）
            if len(_img_locks) >= _IMG_LOCKS_MAX:
                # 删除前一半（较旧的锁）
                to_remove = list(_img_locks.keys())[:_IMG_LOCKS_MAX // 2]
                for k in to_remove:
                    del _img_locks[k]
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
<title>登录 · 青年湖底看板</title>
<style>
*{box-sizing:border-box}body{font-family:system-ui,sans-serif;background:#f5f7fa;
color:#202938;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:20px}
.card{background:white;padding:28px;border-radius:16px;width:min(400px,100%);box-shadow:0 8px 32px #20305010}
h1{font-size:22px;margin:0 0 10px}p{font-size:14px;line-height:1.7;color:#64748b}
label{display:block;font-size:14px;margin:16px 0 6px}input,button{width:100%;padding:12px;
border:1px solid #ccd5e0;border-radius:8px;font:inherit}button{margin-top:22px;
background:#2b7fff;color:white;border:0;cursor:pointer}button:disabled{opacity:.65}
a{color:#2b7fff;text-decoration:none}.guest{display:block;text-align:center;margin-top:18px}
.err{color:#b42318;background:#fff1f0;border-radius:8px;padding:10px}.note{font-size:12px}
</style></head><body><main class="card">
<h1>登录青年湖底看板</h1>
<p>使用你的微北洋账号。登录后可用自己的身份点赞、评论、收藏和发帖。</p>
{ERR}
<form method="POST" action="login" id="loginform">
<input type="hidden" name="csrf" value="{CSRF}">
<label for="account">微北洋账号</label>
<input id="account" name="account" autocomplete="username" required maxlength="128" autofocus>
<label for="password">密码</label>
<input id="password" type="password" name="password" autocomplete="current-password" required maxlength="512">
<button type="submit" id="submit">登录</button></form>
<a class="guest" href="./">继续以游客浏览</a>
<p class="note">密码仅用于本次登录，不保存到看板。退出或登录过期后需要重新登录。</p>
</main><script>document.getElementById('loginform').addEventListener('submit',function(){
var b=document.getElementById('submit');b.disabled=true;b.textContent='正在登录…';});</script></body></html>"""


def guess_type(name):
    """按扩展名返回 Content-Type（从 content_type 模块导入）。"""
    from .content_type import guess_type as _gt
    return _gt(name)


def _log(msg):
    print(msg, flush=True)


class _Handler(http.server.BaseHTTPRequestHandler):
    board = None
    sessions = None

    def log_message(self, *a):
        pass

    def _cookie(self, name):
        try:
            cookies = SimpleCookie(self.headers.get("Cookie") or "")
            return cookies[name].value if name in cookies else ""
        except Exception:
            return ""

    def _session(self):
        return self.sessions.get(self._cookie("wpy_user_session"))

    def _role(self):
        return "user" if self._session() else "guest"

    def _secure(self):
        return _local_peer(self) and self.headers.get("X-Forwarded-Proto", "").split(",")[-1].strip() == "https"

    def _session_cookie(self, value, *, name="wpy_user_session", max_age=SESSION_TTL):
        return (f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
                + ("; Secure" if self._secure() else ""))

    def _login_page(self, error="", code=200):
        csrf = self.sessions.login_csrf()
        page = _LOGIN_PAGE.replace("{CSRF}", csrf).replace(
            "{ERR}", '<p class="err" role="alert">' + html.escape(error) + '</p>' if error else "")
        return self._send(page, "text/html; charset=utf-8", code, {
            "Set-Cookie": self._session_cookie(csrf, name="wpy_login_csrf", max_age=600),
            "Referrer-Policy": "no-referrer",
        })

    def _same_origin(self):
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        # no-referrer 策略下浏览器的同站表单可能发送 null Origin。
        # Fetch Metadata 必须确认同源，随后仍验证登录表单或个人会话 CSRF。
        if origin == "null":
            return self.headers.get("Sec-Fetch-Site") == "same-origin"
        parsed = urllib.parse.urlparse(origin)
        return parsed.scheme in ("http", "https") and parsed.netloc.lower() == self.headers.get("Host", "").lower()

    def _expired(self):
        self.sessions.revoke(self._cookie("wpy_user_session"))
        return self._json({"ok": 0, "error": "登录已过期，请重新登录", "login": "login"}, 401)

    def _remote(self, session, path, params=None):
        response = lake_actions.get(path, params, token=session.token)
        if response.get("code") == 401:
            raise PermissionError("forum session expired")
        if response.get("code") != 200:
            raise RuntimeError("forum unavailable")
        return response.get("data") or {}

    def _personal(self, session, *, required=False):
        if not session:
            return {"mine": set(), "fav": set()}
        with session.lock:
            now = time.monotonic()
            try:
                if session.retry_after > now:
                    raise RuntimeError("personal sync retry pending")
                return self._load_personal(session, now)
            except PermissionError:
                raise
            except Exception:
                # 个人接口暂时不可用仍可浏览归档；个人筛选不伪装成空列表。
                session.retry_after = now + 10
                if required:
                    raise
                return {**(session.personal or {"mine": set(), "fav": set()}), "pending": True}

    def _load_personal(self, session, now):
        if session.personal is not None and session.personal_at and now - session.personal_at < 60:
            return session.personal
        personal = {}
        for kind, path in (("mine", "posts/user"), ("fav", "posts/fav")):
            ids = set()
            for page in range(1, 201):
                data = self._remote(session, path, {"page": page, "page_size": 50})
                rows = data.get("list")
                if not isinstance(rows, list):
                    raise RuntimeError("invalid personal collection")
                before = len(ids)
                ids.update(int(r["id"]) for r in rows if str(r.get("id", "")).isdigit())
                total = data.get("total")
                if total is not None and len(ids) >= int(total):
                    break
                if len(rows) < 50:
                    if total is not None and len(ids) < int(total):
                        raise RuntimeError("incomplete personal collection")
                    break
                if len(ids) == before:
                    raise RuntimeError("repeated personal collection page")
            else:
                raise RuntimeError("personal collection too large")
            personal[kind] = ids
        session.personal = personal
        session.personal_at = now
        return personal

    @staticmethod
    def _personalize(post, personal):
        if post:
            post["mine"] = int(post["id"] in personal["mine"])
            post["fav"] = int(post["id"] in personal["fav"])
            post["personalPending"] = bool(personal.get("pending"))
        return post

    def _redirect(self, location, cookie=None):
        headers = {"Location": location}
        if cookie:
            headers["Set-Cookie"] = cookie
        return self._send(b"", "text/plain; charset=utf-8", 302, headers)

    def _read_form(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if not 0 <= length <= 65536:
            self._json({"error": "请求内容过长"}, 413)
            return None
        raw = self.rfile.read(length).decode("utf-8", "replace")
        qs = urllib.parse.parse_qs(raw, keep_blank_values=True)
        return {k: (v[0] if v else "") for k, v in qs.items()}

    # ---------- 通用 ----------
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
        return self._send(body, "application/json; charset=utf-8", code)

    def read_html(self, path=None):
        try:
            with open(path or HTML_PATH, "rb") as f:
                return f.read()
        except OSError:
            return "<h1>看板文件未找到，请先 python -m wpy build</h1>".encode("utf-8")

    # ---------- GET ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        # 静态资源白名单（PWA 图标、manifest）
        if path in ("/apple-touch-icon.png", "/icon-192.png", "/icon-512.png"):
            icon = os.path.join(ASSET_DIR, path.lstrip("/"))
            if os.path.exists(icon):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                with open(icon, "rb") as f:
                    self.wfile.write(f.read())
                return
        if path == "/manifest.json":
            manifest = br'{"name":"\u9752\u5e74\u6e56\u70ed\u699c","short_name":"\u70ed\u699c","icons":[{"src":"/icon-192.png","sizes":"192x192","type":"image/png"},{"src":"/icon-512.png","sizes":"512x512","type":"image/png"}],"display":"standalone","background_color":"#f5f7fa","theme_color":"#2b7fff"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json")
            self.end_headers()
            self.wfile.write(manifest)
            return

        # 健康检查不鉴权，方便反代/监控探活
        if path == "/healthz":
            return self._json({"ok": True})

        # 热榜分区排行 API 不鉴权（热榜页面公开可访问）
        if path == "/api/board-ranking":
            rank_qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            rank_q = {k: (v[0] if v else "") for k, v in rank_qs.items()}
            try:
                board_name, result = ranking.query_ranking(
                    DB_PATH,
                    board=rank_q.get("board") or "all",
                    dim=rank_q.get("dim") or "view",
                    window=rank_q.get("window") or "all",
                    limit=rank_q.get("limit") or 50,
                )
            except ranking.RankingError as exc:
                return self._json({"error": str(exc)}, 400)
            except ValueError:
                return self._json({"error": "limit 必须是数字"}, 400)
            return self._json({
                "board": rank_q.get("board") or "all",
                "boardName": board_name,
                "dim": rank_q.get("dim") or "view",
                "window": rank_q.get("window") or "all",
                "data": result,
            })

        if path == "/login":
            return self._login_page()
        if path == "/logout":
            return self._json({"error": "请使用退出按钮"}, 405)
        session = self._session()
        role = "user" if session else "guest"

        if path in ("/", "/index.html"):
            return self._send(self.read_html(), "text/html; charset=utf-8")

        if path in ("/hot", "/hot.html"):
            return self._send(self.read_html(HOT_PATH), "text/html; charset=utf-8")
        if path in ("/lab", "/lab.html"):
            return self._send(self.read_html(LAB_PATH), "text/html; charset=utf-8")
        if path in ("/apple-touch-icon.png", "/icon-192.png", "/icon-512.png"):
            icon = os.path.join(ASSET_DIR, path.lstrip("/"))
            if os.path.exists(icon):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                with open(icon, "rb") as f:
                    self.wfile.write(f.read())
                return
        if path == "/manifest.json":
            manifest = '{"name":"青年湖热榜","short_name":"热榜","icons":[{"src":"/icon-192.png","sizes":"192x192","type":"image/png"},{"src":"/icon-512.png","sizes":"512x512","type":"image/png"}],"display":"standalone","background_color":"#f5f7fa","theme_color":"#2b7fff"}'
            return self._send(manifest.encode(), "application/manifest+json")

        if path == "/api/me":
            return self._json({"role": role, "csrf": session.csrf if session else ""})

        if path == "/api/metrics":
            return self._json({"error": "not found"}, 404)

        if path == "/api/follow":
            # 登录用户的关注列表按账号持久化，游客只存在浏览器。
            if not session:
                return self._json({"error": "forbidden"}, 403)
            with _follows_mu:
                return self._json({"follows": _follows_load(self.sessions.follows_path(session))})

        if path == "/api/wpy/posttypes":
            if not session:
                return self._json({"error": "请先登录微北洋账号"}, 401)
            try:
                return self._json({"ok": 1, "data": self._remote(session, "posttypes")})
            except PermissionError:
                return self._expired()
            except Exception:
                return self._json({"ok": 0, "error": "论坛暂时不可用，请稍后再试"}, 502)

        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        q = {k: (v[0] if v else "") for k, v in qs.items()}
        # 游客一律套用游客屏蔽名单
        if role == "guest" and GUEST_BLOCK_UIDS:
            q["blockUids"] = ",".join(GUEST_BLOCK_UIDS)

        try:
            if path == "/api/init":
                return self._json(self.board.init_info())
            if path == "/api/posts":
                personal = self._personal(session, required=q.get("mine") == "1" or q.get("fav") == "1")
                result = self.board.posts(q, personal=personal)
                result["personalPending"] = bool(personal.get("pending"))
                result["items"] = [self._personalize(p, personal) for p in result["items"]]
                return self._json(result)
            if path == "/api/post":
                pid = _to_int(q.get("id", 0), 0)
                flim = _to_int(q.get("flim", 150), 150)
                foff = _to_int(q.get("foff", 0), 0)
                d = self.board.post_detail(pid, floor_limit=flim, floor_offset=foff)
                if (d and role == "guest" and GUEST_BLOCK_UIDS
                        and str(d.get("uid") or "") in GUEST_BLOCK_UIDS):
                    return self._json({"error": "not found"}, 404)
                return self._json(self._personalize(d, self._personal(session))) if d else self._json({"error": "not found"}, 404)
            if path == "/api/user":
                uid = _to_int(q.get("uid", 0), 0)
                return self._json(self.board.user(uid))
            if path == "/api/topusers":
                return self._json(self.board.top_users())
            if path == "/api/users":
                ids = [x for x in (q.get("uids", "") or "").split(",")
                       if x.strip().isdigit()]
                return self._json(self.board.users([int(x) for x in ids[:200]]))
        except PermissionError:
            return self._expired()
        except Exception:
            return self._json({"error": "数据暂时不可用，请稍后再试"}, 502)

        if path.startswith("/img/"):
            return self._serve_img(path)

        self._send(b"not found", "text/plain; charset=utf-8", 404)

    # ---------- POST：个人登录、关注与论坛动作 ----------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._same_origin():
            return self._json({"error": "请求来源无效，请刷新页面"}, 403)
        form = self._read_form()
        if form is None:
            return
        if path == "/login":
            csrf = form.get("csrf") or ""
            if not (hmac.compare_digest(csrf, self._cookie("wpy_login_csrf"))
                    and self.sessions.valid_login_csrf(csrf)):
                return self._login_page("登录页面已过期，请重新提交", 403)
            # 生产登录通过 HTTPS；允许本机开发使用回环地址 HTTP。
            host = urllib.parse.urlparse("//" + self.headers.get("Host", "")).hostname
            if not self._secure() and not (_local_peer(self) and host in ("127.0.0.1", "localhost", "::1")):
                return self._login_page("请通过 HTTPS 页面登录", 403)
            account = (form.get("account") or "").strip()
            password = form.get("password") or ""
            if not account or not password or len(account) > 128 or len(password) > 512:
                return self._login_page("请填写微北洋账号和密码", 400)
            ip = _client_ip(self)
            if _login_throttled(ip):
                return self._login_page("尝试次数过多，请 10 分钟后重试", 429)
            try:
                token = auth.login(account, password)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    _login_fail(ip)
                    return self._login_page("登录未成功，请检查账号密码或稍后重试", 401)
                if exc.code == 429:
                    return self._login_page("微北洋请求过多，请稍后再试", 429)
                return self._login_page("账号登录暂时不可用，请稍后再试", 503)
            except (auth.FatalError, OSError):
                return self._login_page("账号登录暂时不可用，请稍后再试", 503)
            except Exception:
                _login_fail(ip)
                return self._login_page("登录未成功，请检查账号密码或稍后重试", 401)
            if not isinstance(token, str) or not token:
                return self._login_page("微北洋登录暂时不可用，请稍后重试", 502)
            try:
                sid, _ = self.sessions.create(account, token)
            except ValueError:
                return self._login_page("当前登录人数过多，请稍后重试", 503)
            self.sessions.revoke(self._cookie("wpy_user_session"))
            with _login_fails_mu:
                _login_fails.pop(ip, None)
            return self._redirect("./", [
                self._session_cookie(sid),
                self._session_cookie("", name="wpy_login_csrf", max_age=0),
                self._session_cookie("", name="wpy_token", max_age=0),
            ])

        session = self._session()
        if not session:
            return self._json({"ok": 0, "error": "请先登录微北洋账号", "login": "login"}, 401)
        if not hmac.compare_digest(self.headers.get("X-WPY-CSRF") or "", session.csrf):
            return self._json({"ok": 0, "error": "会话验证失败，请刷新页面"}, 403)
        if path == "/logout":
            self.sessions.revoke(self._cookie("wpy_user_session"))
            return self._send(json.dumps({"ok": 1}), "application/json", 200, {
                "Set-Cookie": self._session_cookie("", max_age=0),
            })
        if path == "/api/follow":
            uid = (form.get("uid") or "").strip()
            if not uid.isdigit():
                return self._json({"ok": 0, "error": "bad uid"}, 400)
            with _follows_mu:
                location = self.sessions.follows_path(session)
                data = _follows_load(location)
                if uid in data:
                    del data[uid]
                else:
                    data[uid] = (form.get("name") or ("用户 " + uid))[:40]
                _follows_save(location, data)
                return self._json({"ok": 1, "follows": data})

        actions = {
            "fav": ("post/fav", ("post_id", "op")),
            "like": ("post/like", ("post_id", "op")),
            "floor_like": ("floor/like", ("floor_id", "op")),
            "reply": ("floor", ("post_id", "content", "images")),
            "floor_reply": ("floor/reply", ("reply_to_floor", "content", "images")),
            "post": ("post", ("type", "title", "content", "campus", "department_id", "tag_id", "masked")),
        }
        if path.startswith("/api/wpy/"):
            key = path[len("/api/wpy/"):]
            if key not in actions:
                return self._json({"error": "not found"}, 404)
            wpath, fields = actions[key]
            payload = {k: v for k, v in form.items() if k in fields}
            try:
                response = lake_actions.write(wpath, payload, token=session.token)
            except Exception:
                return self._json({"ok": 0, "error": "论坛暂时不可用，请稍后再试"}, 502)
            if response.get("code") == 401:
                return self._expired()
            if response.get("code") == 200:
                if key in ("fav", "post"):
                    self.sessions.invalidate_personal(session.principal)
                # 不改动抓取账号在共享数据库中的个人标记。
                return self._json({"ok": 1, "data": response.get("data")})
            return self._json({"ok": 0, "error": "论坛未接受该操作，请刷新后重试"}, 502)
        return self._json({"error": "not found"}, 404)

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
    from .config import DB_PATH as _DB
    host = host if host is not None else DEFAULT_HOST
    port = port if port is not None else DEFAULT_PORT
    if open_browser is None:
        open_browser = host in ("127.0.0.1", "localhost")

    if not os.path.exists(HTML_PATH):
        from .ui import build_html
        build_html(HTML_PATH)
    if not os.path.exists(db_path or _DB) or os.path.getsize(db_path or _DB) == 0:
        from .db import open_db
        conn = open_db(db_path or _DB)
        conn.close()
        print("已初始化空数据库：%s" % (db_path or _DB))

    os.makedirs(IMG_DIR, exist_ok=True)
    board = BoardDB(db_path or _DB, pic_base=PIC_API, use_proxy=True)
    handler = type("Handler", (_Handler,), {"board": board,
        "sessions": Sessions(os.path.dirname(db_path or _DB) or ".")})

    url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = "http://%s:%d/" % (url_host, port)

    print()
    print("=" * 60)
    print("  看板服务已启动")
    print("=" * 60)
    print("  监听：%s:%d" % (host, port))
    print("  本机打开：%s" % url)
    if host in ("0.0.0.0", "::"):
        print("  访问范围：请由网关限制校园网访问，并为账号登录配置 HTTPS")
    print("  游客只读；登录微北洋账号后可操作论坛")
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
