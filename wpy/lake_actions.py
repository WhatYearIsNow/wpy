# -*- coding: utf-8 -*-
"""湖底写操作代理：发帖 / 点赞 / 评论 / 收藏 / 板块列表。

看板动作按钮都以「服务端单一账号」身份调用湖底接口：
  - 管理员动作（发帖/点赞/评论）与游客收藏，实际都落在这个账号上；
  - token 登录一次后缓存在内存里，失效自动重登；
  - 相邻写操作强制间隔，避免高频动作触发风控。
"""
import threading
import time
import urllib.parse

from .auth import get_token
from .config import LAKE_API
from .http import FatalError, request_json
import urllib.error
import json as _json


def _request_json_safe(url, token=None, method="GET", headers=None, body=None):
    """request_json 的兜底版：HTTPError（非 429）时尝试读响应体里的 JSON。

    湖底接口会把业务错误（如 code=404 post not found）直接放在 HTTP
    状态码里，不读体的话只能看到一行 'HTTP Error 404'。"""
    try:
        return request_json(url, token=token, method=method,
                            headers=headers, body=body, throttle=False)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", "replace")
            d = _json.loads(raw)
            if isinstance(d, dict):
                return d
        except Exception:
            pass
        return {"code": e.code, "msg": "HTTP %s" % e.code}

_mu = threading.Lock()
_token = {"v": None, "at": 0.0}
_last_write = 0.0
_WRITE_GAP = 1.5            # 相邻写操作最小间隔（秒）
_TOKEN_TTL = 6 * 60 * 60    # token 缓存 6 小时


def _fresh_token():
    with _mu:
        now = time.time()
        if _token["v"] and now - _token["at"] < _TOKEN_TTL:
            return _token["v"]
    tok = get_token(interactive=False)   # 缺凭据时抛 FatalError
    with _mu:
        _token["v"] = tok
        _token["at"] = time.time()
    return tok


def _invalidate():
    with _mu:
        _token["v"] = None
        _token["at"] = 0.0


def _pace():
    global _last_write
    with _mu:
        wait = _WRITE_GAP - (time.time() - _last_write)
        if wait > 0:
            time.sleep(wait)
        _last_write = time.time()


def _encode(form):
    return "&".join(
        "%s=%s" % (k, urllib.parse.quote(str(v))) for k, v in form.items())


def write(path, form):
    """POST 一个湖底写接口，返回原始 JSON（code==200 为成功）。

    token 失效（401/403）自动重登一次再试。"""
    global _last_write
    last_err = None
    for attempt in (1, 2):
        _pace()
        try:
            tok = _fresh_token()
        except FatalError as e:
            return {"code": 500, "msg": "服务端未配置湖底账号凭据：%s" % e}
        r = _request_json_safe(
            LAKE_API + path, method="POST",
            headers={"token": tok,
                     "Content-Type": "application/x-www-form-urlencoded"},
            body=_encode(form))
        code = r.get("code")
        if code == 200:
            return r
        last_err = r
        if code in (401, 403):
            _invalidate()
            continue
        return r
    return last_err or {"code": 500, "msg": "写操作失败"}


def get(path, params=None):
    """GET 一个湖底读接口（如 posttypes），带服务账号 token。"""
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    tok = _fresh_token()
    return _request_json_safe(LAKE_API + path + qs, token=tok)
