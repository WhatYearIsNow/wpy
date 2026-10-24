# -*- coding: utf-8 -*-
"""本地看板 HTTP 服务：页面 + /api/* + 图片代理 + 湖底动作代理。

看板本身只读；写动作（发帖/点赞/评论/收藏）经本服务以服务端单一账号
身份代理到湖底接口，见 lake_actions.py。
默认只监听 127.0.0.1；公网部署请 --host 0.0.0.0 并设置 WPY_BOARD_TOKEN。
"""
import hashlib
import hmac
import http.server
import json
import os
import socketserver
import sqlite3
import threading
import time
import urllib.parse
import webbrowser

from .config import (
    ASSET_DIR, BOARD_TOKEN, DB_PATH, DEFAULT_HOST, DEFAULT_PORT, HOT_PATH,
    HTML_PATH, IMG_DIR, LAB_PATH, METRICS_JSON_PATH, PIC_API,
)
from . import lake_actions
from .board_api import BoardDB
from .cli import _ensure_utf8_stdio
from .http import get_bytes
from .metrics import metrics

# 单张图上限，防止 CDN 异常把内存拖垮
_IMG_MAX_BYTES = 12 * 1024 * 1024
# 同名图片并发下载只跑一次
# 限容：最多保留 5000 个锁，超过时清理最久未使用的。
# 防止长时间运行后锁字典无限增长。
_IMG_LOCKS_MAX = 5000
_img_locks = {}
_img_locks_mu = threading.Lock()

# 关注列表（服务端持久化，跨设备/浏览器共享）
_FOLLOWS_PATH = os.path.join(os.path.dirname(DB_PATH) or ".", "follows.json")

# 游客屏蔽名单：这些作者的帖子对非管理员不可见
# （环境变量 WPY_GUEST_BLOCK_UIDS，逗号分隔 uid，写在 deploy/env.board 里）
GUEST_BLOCK_UIDS = [u.strip() for u in
                    os.environ.get("WPY_GUEST_BLOCK_UIDS", "").split(",")
                    if u.strip().isdigit()]
_follows_mu = threading.Lock()

# 登录失败限速：公网上防止对管理员口令的暴力尝试。
# 内存态，重启即清；10 分钟窗口内失败满 8 次暂时拒绝继续尝试。
_login_fails = {}
_login_fails_mu = threading.Lock()


def _client_ip(handler):
    """服务只监听 127.0.0.1，公网请求都经反代进来，XFF 首跳可信。"""
    xff = handler.headers.get("X-Forwarded-For") or ""
    return (xff.split(",")[0].strip() if xff else "") \
        or (handler.client_address[0] if handler.client_address else "?")


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
            _login_fails = {
                k: v for k, v in _login_fails.items()
                if any(now - t < 600 for t in v)
            }
            _login_fails[ip] = hits


def _to_int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _follows_load():
    try:
        with open(_FOLLOWS_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _follows_save(d):
    tmp = _FOLLOWS_PATH + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, _FOLLOWS_PATH)


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
<meta name="theme-color" content="#0f1419">
<title>青年湖底看板 · 登录</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f1419;color:#e7ecf3;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
.card{background:#1a222d;padding:28px 32px;border-radius:12px;width:min(360px,92vw)}
h1{font-size:18px;margin:0 0 6px}
p.sub{font-size:13px;color:#9aa7b8;margin:0 0 18px;line-height:1.5}
button{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;
border:1px solid #2c3848;font-size:14px;cursor:pointer}
.guest{background:#1f2b3a;color:#e7ecf3}
.guest:hover{background:#26354a}
.hr{display:flex;align-items:center;gap:10px;color:#5b6b80;font-size:12px;margin:16px 0}
.hr::before,.hr::after{content:"";flex:1;border-top:1px solid #2c3848}
input,button.admin{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;
border:1px solid #2c3848;font-size:14px}
input{background:#0f1419;color:#e7ecf3;margin-bottom:12px}
button.admin{background:#3b82f6;border:none;color:#fff}
button.admin:hover{background:#2563eb}
.err{color:#f87171;font-size:12px;margin:0 0 12px}
.foot{font-size:11px;color:#5b6b80;margin-top:16px;text-align:center;line-height:1.6}
</style></head><body>
<div class="card">
  <h1>青年湖底看板</h1>
  <p class="sub">微北洋「青年湖底」全站数据看板（只读浏览 + 动作代理）</p>
  <form method="POST" action="login">
    <input type="hidden" name="mode" value="guest">
    <button type="submit" class="guest">游客进入</button>
  </form>
  <div class="hr">管理员</div>
  <form method="POST" action="login">
    <input type="password" name="password" placeholder="管理员口令" required>
    <button type="submit" class="admin">管理员登录</button>
  </form>
  {ERR}
  <p class="foot">游客：浏览 / 关注 / 收藏<br>管理员：另可发帖 / 点赞 / 评论</p>
</div></body></html>"""


def guess_type(name):
    """按扩展名返回 Content-Type（从 content_type 模块导入）。"""
    from .content_type import guess_type as _gt
    return _gt(name)


def _log(msg):
    print(msg, flush=True)


class _Handler(http.server.BaseHTTPRequestHandler):
    board = None
    token = ""

    def log_message(self, *a):
        pass

    # ---------- 会话与鉴权 ----------
    def _cookie(self, name):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            part = part.strip()
            if part.startswith(name + "="):
                return part.split("=", 1)[1]
        return ""

    def _query_token(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        return (qs.get("token") or [""])[0]

    def _session_sig(self, role):
        return hmac.new(self.token.encode("utf-8"), role.encode("utf-8"),
                        hashlib.sha256).hexdigest()[:32]

    def _session_cookie(self, role):
        host = self.headers.get("Host", "127.0.0.1")
        # 去掉端口号：浏览器对带端口的 domain 兼容性差
        if ":" in host:
            host = host.rsplit(":", 1)[0]
        return ("wpy_session=%s.%s; Path=/; HttpOnly; SameSite=Lax; "
                "Max-Age=2592000; Domain=%s" % (role, self._session_sig(role), host))

    def _role(self):
        """'admin' / 'guest'；未登录返回 ''。口令为空（本地模式）视为 admin。

        判定顺序：URL ?token=（显式链接，最优先）> 新会话 cookie
        wpy_session（游客/管理员）> 旧版口令 cookie wpy_token（兼容）。
        新会话优先于旧 cookie：否则先拿到过口令 cookie 的浏览器
        点「游客进入」后仍会被当成管理员。"""
        if not self.token:
            return "admin"
        if self._query_token() == self.token:
            return "admin"
        raw = self._cookie("wpy_session")
        if "." in raw:
            role, sig = raw.split(".", 1)
            if role in ("admin", "guest") and hmac.compare_digest(
                    sig, self._session_sig(role)):
                return role
        if self._cookie("wpy_token") == self.token:
            return "admin"
        return ""

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
        raw = self.rfile.read(min(length, 65536)).decode("utf-8", "replace")
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
            manifest = b'{"name":"\u9752\u5e74\u6e56\u70ed\u699c","short_name":"\u70ed\u699c","icons":[{"src":"/icon-192.png","sizes":"192x192","type":"image/png"},{"src":"/icon-512.png","sizes":"512x512","type":"image/png"}],"display":"standalone","background_color":"#f5f7fa","theme_color":"#2b7fff"}'
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
            board_id = (rank_q.get("board") or "")
            dim = rank_q.get("dim") or "view"
            window = rank_q.get("window") or "all"
            limit = int(rank_q.get("limit") or "50")

            if not board_id or not str(board_id).isdigit():
                return self._json({"error": "缺少 board 参数"}, 400)
            board_id = int(board_id)
            _COL_MAP = {"view": "浏览量", "like": "点赞数", "comment": "评论数", "fav": "收藏数"}
            _BOARD_MAP = {
                1: "校务专区", 2: "青年湖底", 3: "学习新知", 4: "交友恋爱",
                5: "二次元", 6: "摆摊", 8: "数码科技", 13: "美食专区",
            }
            if dim not in _COL_MAP:
                return self._json({"error": "dim 必须是 view/like/comment/fav"}, 400)

            col = _COL_MAP[dim]
            board_name = _BOARD_MAP.get(board_id, "")

            db = sqlite3.connect(str(DB_PATH))
            db.row_factory = sqlite3.Row
            cur = db.cursor()

            if window == "day":
                where = f"板块ID = {board_id} AND replace(substr(发布时间,1,19),'T',' ') > datetime('now','localtime','-1 day')"
            elif window == "week":
                where = f"板块ID = {board_id} AND replace(substr(发布时间,1,19),'T',' ') > datetime('now','localtime','-7 days')"
            elif window == "month":
                where = f"板块ID = {board_id} AND replace(substr(发布时间,1,19),'T',' ') > datetime('now','localtime','-30 days')"
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

        if path == "/login":
            if self.token and self._role():
                return self._redirect("./")
            err = ("<p class=\"err\">口令不对，再试一次</p>"
                   if "err=1" in (parsed.query or "") else "")
            return self._send(_LOGIN_PAGE.replace("{ERR}", err),
                              "text/html; charset=utf-8")

        if path == "/logout":
            return self._redirect("login",
                                  "wpy_session=; Path=/; HttpOnly; Max-Age=0")

        role = self._role()
        if self.token and not role:
            if path in ("/", "/index.html"):
                return self._send(_LOGIN_PAGE.replace("{ERR}", ""),
                                  "text/html; charset=utf-8", 401)
            return self._json({"error": "unauthorized"}, 401)

        # 首次带 ?token= 进入时种管理员会话，之后刷新不用再带
        if self.token and self._query_token() == self.token \
                and path in ("/", "/index.html"):
            html = self.read_html().decode("utf-8", errors="replace")
            return self._send(html, "text/html; charset=utf-8", 200, {
                "Set-Cookie": self._session_cookie("admin"),
            })

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
            return self._json({"role": role or "admin"})

        if path == "/api/metrics":
            return self._json(metrics.snapshot())

        if path == "/api/follow":
            # 管理员的关注列表（服务端持久化）；游客的关注只存本地浏览器
            if role != "admin":
                return self._json({"error": "forbidden"}, 403)
            with _follows_mu:
                return self._json({"follows": _follows_load()})

        if path == "/api/wpy/posttypes":
            if role != "admin":
                return self._json({"error": "forbidden"}, 403)
            try:
                r = lake_actions.get("posttypes")
                return self._json({"ok": 1, "data": r.get("data")})
            except Exception as e:
                return self._json({"ok": 0, "error": str(e)[:200]}, 502)

        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        q = {k: (v[0] if v else "") for k, v in qs.items()}
        # 非管理员一律套用游客屏蔽名单
        if role != "admin" and GUEST_BLOCK_UIDS:
            q["blockUids"] = ",".join(GUEST_BLOCK_UIDS)

        try:
            if path == "/api/init":
                return self._json(self.board.init_info())
            if path == "/api/posts":
                return self._json(self.board.posts(q))
            if path == "/api/post":
                pid = _to_int(q.get("id", 0), 0)
                flim = _to_int(q.get("flim", 150), 150)
                foff = _to_int(q.get("foff", 0), 0)
                d = self.board.post_detail(pid, floor_limit=flim, floor_offset=foff)
                if (d and role != "admin" and GUEST_BLOCK_UIDS
                        and str(d.get("uid") or "") in GUEST_BLOCK_UIDS):
                    return self._json({"error": "not found"}, 404)
                return self._json(d) if d else self._json({"error": "not found"}, 404)
            if path == "/api/user":
                uid = _to_int(q.get("uid", 0), 0)
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

    # ---------- POST：登录 + 湖底动作代理 ----------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        form = self._read_form()

        if path == "/login":
            if not self.token:
                return self._redirect("./")
            ip = _client_ip(self)
            if _login_throttled(ip):
                return self._json({"error": "失败次数过多，请 10 分钟后再试"}, 429)
            if form.get("mode") == "guest":
                # 游客会话 + 顺手清掉旧版口令 cookie，避免被判成管理员
                return self._redirect("./", [
                    self._session_cookie("guest"),
                    "wpy_token=; Path=/; HttpOnly; Max-Age=0",
                ])
            password = form.get("password") or ""
            if password and hmac.compare_digest(password, self.token):
                with _login_fails_mu:
                    _login_fails.pop(ip, None)   # 成功即清空失败计数
                return self._redirect("./", self._session_cookie("admin"))
            if password:
                _login_fail(ip)
            return self._redirect("login?err=1")

        role = self._role()
        if self.token and not role:
            return self._json({"error": "unauthorized"}, 401)

        if path == "/api/follow":
            uid = (form.get("uid") or "").strip()
            if not uid.isdigit():
                return self._json({"ok": 0, "error": "bad uid"}, 400)
            with _follows_mu:
                d = _follows_load()
                if uid in d:
                    del d[uid]
                else:
                    d[uid] = (form.get("name") or ("用户 " + uid))[:40]
                _follows_save(d)
                return self._json({"ok": 1, "follows": d})

        # ---- 湖底动作代理（服务端单一账号身份）----
        actions = {
            # 键: (湖底路径,     允许角色, 允许字段)
            "fav":       ("post/fav",    "any",   ("post_id", "op")),
            "like":      ("post/like",   "admin", ("post_id", "op")),
            "floor_like": ("floor/like", "admin", ("floor_id", "op")),
            # 评论两个接口（对齐 App 源码 feedback_service.dart）：
            #   顶层评论 POST floor       (post_id + content + images)
            #   楼中楼   POST floor/reply (reply_to_floor + content + images)
            # images 是必须携带的字段（App 恒发送，空值也带），缺了湖底直接 500；
            # post/reply 是官方帖专用，对普通帖用它会返回「无权限回复」
            "reply":       ("floor",       "admin", ("post_id", "content",
                                                     "images")),
            "floor_reply": ("floor/reply", "admin", ("reply_to_floor",
                                                     "content", "images")),
            "post":      ("post",        "admin", ("type", "title", "content",
                                                   "campus", "department_id",
                                                   "tag_id", "masked")),
        }
        if path.startswith("/api/wpy/"):
            key = path[len("/api/wpy/"):]
            if key not in actions:
                return self._json({"error": "not found"}, 404)
            wpath, need, fields = actions[key]
            if need == "admin" and role != "admin":
                return self._json({"ok": 0, "error": "游客不能执行该操作"}, 403)
            form = {k: v for k, v in form.items() if k in fields}
            try:
                r = lake_actions.write(wpath, form)
            except Exception as e:
                return self._json({"ok": 0, "error": str(e)[:200]}, 502)
            if r.get("code") == 200:
                if key == "fav":
                    # 收藏成功立即同步本地「是我的收藏」标记：
                    # 该列平时由定时抓取同步，不等它的话刷新后状态会回退，
                    # 用户再点一次就会撞上湖底对重复收藏的报错
                    self._sync_fav_flag(form)
                return self._json({"ok": 1, "data": r.get("data")})
            return self._json({"ok": 0,
                               "error": str(r.get("msg") or r)[:200]}, 502)

        return self._json({"error": "not found"}, 404)

    def _sync_fav_flag(self, form):
        pid = form.get("post_id")
        if not (pid and str(pid).isdigit()):
            return
        try:
            # 看板连接被 PRAGMA query_only 锁成只读，这里开短连接单独写
            con = sqlite3.connect(DB_PATH, timeout=30)
            try:
                con.execute('UPDATE posts SET "是我的收藏"=? WHERE id=?',
                            (1 if form.get("op") == "1" else 0, int(pid)))
                con.commit()
            finally:
                con.close()
        except sqlite3.Error as e:
            # 湖底操作已成功，本地同步失败只记日志，等下轮抓取自然补上
            _log("fav 状态同步失败：%s" % e)

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
    token = BOARD_TOKEN if token is None else token
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
        print("  已启用访问口令：游客免口令浏览，管理员用 WPY_BOARD_TOKEN")
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
