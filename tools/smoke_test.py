# -*- coding: utf-8 -*-
"""本地冒烟测试：不依赖外网业务接口，只测本包只读能力。

用法（在 wpy-sitewide 目录）：
    python tools/smoke_test.py
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "wpy-smoke"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _json(url, timeout=20):
    st, body = _get(url, timeout=timeout)
    return st, json.loads(body.decode("utf-8"))


def main() -> int:
    fails = []

    def check(name, cond, detail=""):
        print(("PASS" if cond else "FAIL"), name, detail)
        if not cond:
            fails.append(name)

    # 1) imports
    try:
        from wpy import board_api, config, server, ui, crawler, auth  # noqa
        check("import package", True)
    except Exception as e:
        check("import package", False, str(e))
        return 1

    # 2) build html
    try:
        size = ui.build_html(config.HTML_PATH)
        check("build html", size > 10_000, "bytes=%s" % size)
    except Exception as e:
        check("build html", False, str(e))

    # 3) BoardDB
    from wpy.board_api import BoardDB

    b = BoardDB(config.DB_PATH, pic_base=config.PIC_API, use_proxy=True)
    t0 = time.time()
    info = b.init_info()
    check("init_info", info.get("total", 0) > 1000, "total=%s" % info.get("total"))
    t0 = time.time()
    posts = b.posts({"size": 5, "page": 1})
    check("posts page", posts.get("total", 0) > 0 and posts.get("items"),
          "ms=%d total=%s" % (int((time.time() - t0) * 1000), posts.get("total")))
    # MP号 / MPID 定点搜索：拿首页帖子的 id / uid 回搜，应能精确命中
    p0 = next((i for i in posts["items"] if i.get("uid")), None)
    if p0 is None:
        check("sample author available", False)
        return 1
    sample_uid = p0["uid"]
    if p0:
        t0 = time.time()
        s = b.posts({"q": "#MP%d" % p0["id"], "size": 5, "page": 1})
        check("search MP id (#MP<id>)",
              s.get("total", 0) >= 1
              and any(i["id"] == p0["id"] for i in s["items"]),
              "ms=%d total=%s" % (int((time.time() - t0) * 1000), s.get("total")))
        t0 = time.time()
        s = b.posts({"q": "MPID%d" % p0["uid"], "size": 5, "page": 1})
        check("search MPID<uid>",
              s.get("total", 0) >= 1
              and all(i["uid"] == p0["uid"] for i in s["items"]),
              "ms=%d total=%s" % (int((time.time() - t0) * 1000), s.get("total")))
    t0 = time.time()
    u = b.user(sample_uid)
    check("sample author", time.time() - t0 < 1.0 and u.get("uid") == sample_uid,
          "ms=%d posts=%s" % (int((time.time() - t0) * 1000), u.get("posts")))
    # list images should be thumb paths
    imgs = posts["items"][0].get("imgs") or []
    if imgs:
        check("list img thumb", all("/img/thumb/" in x for x in imgs), str(imgs[:1]))
    else:
        # 第一页可能无图，放宽：只要 API 返回结构即可
        check("list imgs field", "imgs" in posts["items"][0])

    # concurrent user+posts (thread safety)
    errs = []
    def hit():
        try:
            b.user(sample_uid)
            b.posts({"size": 3, "page": 1})
        except Exception as e:
            errs.append(str(e))
    ths = [threading.Thread(target=hit) for _ in range(8)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    check("concurrent queries", not errs, str(errs[:1]))

    # 4) HTTP serve
    from wpy.server import serve
    port = 8777
    th = threading.Thread(
        target=serve,
        kwargs={"host": "127.0.0.1", "port": port, "open_browser": False,
                "token": "smoke"},
        daemon=True,
    )
    th.start()
    time.sleep(1.0)
    base = "http://127.0.0.1:%d" % port
    try:
        st, body = _json(base + "/healthz")
        check("healthz", st == 200 and body.get("ok") is True)
        # unauthorized
        try:
            _get(base + "/api/init")
            check("token required", False, "should 401")
        except urllib.error.HTTPError as e:
            check("token required", e.code == 401, "code=%s" % e.code)
        st, body = _json(base + "/api/init?token=smoke")
        check("api init with token", st == 200 and body.get("total"))
        st, body = _json(base + "/api/posts?size=3&token=smoke")
        check("api posts", st == 200 and body.get("items"))
        st, body = _json(base + "/api/user?uid=%s&token=smoke" % sample_uid)
        check("api user", st == 200 and body.get("uid") == sample_uid)
        # detail with floor paging if possible
        pid = body_posts_id = posts["items"][0]["id"]
        st, d = _json(base + "/api/post?id=%s&flim=5&token=smoke" % pid)
        check("api post detail", st == 200 and "floorsTotal" in d,
              "floors=%s/%s" % (d.get("floorsLoaded"), d.get("floorsTotal")))
        # img thumb path format from a post if any
        # healthz no auth
        st, body = _json(base + "/healthz")
        check("healthz no auth", st == 200)
    except Exception as e:
        check("http serve suite", False, str(e))

    print("-" * 40)
    if fails:
        print("FAILED:", ", ".join(fails))
        return 1
    print("ALL SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
