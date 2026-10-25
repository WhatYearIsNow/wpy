# -*- coding: utf-8 -*-
"""湖底动作代理。每次请求必须显式传入当前用户的论坛 token。

不读取抓取账号凭据，不在个人会话失效时使用服务器账号重新登录。
"""
import threading
import time
import urllib.parse

from .config import LAKE_API
from .http import request_json
import urllib.error
import json as _json


def _request_json_safe(url, token=None, method="GET", headers=None, body=None):
    """request_json 的兜底版：HTTPError（非 429）时尝试读响应体里的 JSON。

    湖底接口会把业务错误（如 code=404 post not found）直接放在 HTTP
    状态码里，不读体的话只能看到一行 'HTTP Error 404'。"""
    try:
        return request_json(url, token=token, method=method,
                            headers=headers, body=body, throttle=method == "GET")
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
_last_write = 0.0
_WRITE_GAP = 1.5


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


def write(path, form, *, token):
    """以指定用户身份写入；token 失效交由看板要求本人重新登录。"""
    if not token:
        raise ValueError("请先登录微北洋账号")
    _pace()
    return _request_json_safe(
        LAKE_API + path, method="POST",
        headers={"token": token,
                 "Content-Type": "application/x-www-form-urlencoded"},
        body=_encode(form))


def get(path, params=None, *, token):
    """以指定用户身份读取论坛接口。"""
    if not token:
        raise ValueError("请先登录微北洋账号")
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    return _request_json_safe(LAKE_API + path + qs, token=token)
