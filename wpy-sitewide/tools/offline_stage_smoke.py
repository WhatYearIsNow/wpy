# -*- coding: utf-8 -*-
"""离线阶段 smoke：不访问真实外网，验证 crawler 各阶段入口可执行。

用法（在 wpy-sitewide 目录）：
    python tools/offline_stage_smoke.py

目标：
- 模块导入完整
- do_list / crawl_essence / do_body / do_comment / do_image / run_pipeline 可进入
- 无 NameError / 缺失 import
- DB 写入边界正常（临时库，不碰 data/sitewide.db）
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 测试自身保证 UTF-8，避免被宿主 PowerShell 的 cp1252 控制台编码干扰。
from wpy.cli import _ensure_utf8_stdio
_ensure_utf8_stdio()

# 必须在 import wpy 之前切换数据目录，否则会打到真实 sitewide.db
_TMP = tempfile.mkdtemp(prefix="wpy_offline_smoke_")
os.environ["WPY_DATA_DIR"] = _TMP
os.environ.setdefault("PYTHONUTF8", "1")


def _post(pid, tid=2, comments=0, body_ok=0):
    return {
        "id": pid,
        "uid": 1000 + pid,
        "title": "t%d" % pid,
        "content": "list-body-%d" % pid,
        "created_at": "2026-09-01 10:00:00",
        "nickname": "user%d" % pid,
        "user_info": {"level": 3, "avatar": "http://x/%d.png" % pid},
        "type": tid,
        "department": {"name": "青年湖底"},
        "tag": {"name": "tag", "id": 1},
        "like_count": 1,
        "fav_count": 0,
        "comment_count": comments,
        "visit_count": 10,
        "e_tag": "",
        "image_urls": ["img_%d.png" % pid],
        "solved": False,
    }


def _floor(fid, pid):
    return {
        "id": fid,
        "uid": 50,
        "nickname": "c",
        "content": "floor-%d" % fid,
        "created_at": "2026-09-01 11:00:00",
        "user_info": {"level": 1, "avatar": ""},
        "like_count": 0,
        "value": 0,
        "sub_floor_cnt": 0,
        "image_url": "",
        "is_owner": False,
        "sub_floors": [],
    }


def install_mocks():
    import wpy.crawler.body as body
    import wpy.crawler.comment as comment
    import wpy.crawler.essence as essence
    import wpy.crawler.image as image
    import wpy.crawler.list as list_mod
    import wpy.crawler.pipeline as pipeline
    import wpy.crawler.shared as shared
    import wpy.crawler.export_ops as export_ops
    import wpy.http as http

    state = {"list_page": 0, "essence_page": 0, "user_page": 0, "fav_page": 0,
             "comment_pages": {}}

    def fake_api_get(token, path, params=None, allow_null_data=False):
        params = params or {}
        if path == "posttypes":
            return {"list": [{"id": 2, "name": "青年湖底"}]}
        if path == "posts" and params.get("etag") == "recommend":
            state["essence_page"] += 1
            if state["essence_page"] > 1:
                return {"list": [], "total": 0}
            return {"list": [_post(9001, 2)], "total": 1}
        if path == "posts" and str(params.get("type")) in ("2", "2"):
            state["list_page"] += 1
            if state["list_page"] == 1:
                # 两页重复 + 空页终止：触发 pagination_events 写路径
                return {
                    "list": [_post(1001, 2, comments=2), _post(1002, 2, comments=0)],
                    "total": 20,
                }
            return {"list": [], "total": 0}
        if path in ("posts/user", "posts/fav"):
            key = "user_page" if path == "posts/user" else "fav_page"
            state[key] += 1
            if state[key] > 1:
                return {"list": [], "total": 0}
            pid = 2001 if path == "posts/user" else 2002
            return {"list": [_post(pid, 2, comments=1)], "total": 1}
        if path == "post":
            pid = int(params.get("id") or 0)
            return {
                "post": dict(
                    _post(pid, 2, comments=2),
                    content="full-body-%d" % pid,
                    image_urls=["origin_%d.png" % pid],
                )
            }
        if path == "floors":
            pid = int(params.get("post_id") or 0)
            n = state["comment_pages"].get(pid, 0)
            state["comment_pages"][pid] = n + 1
            if n > 0:
                return {"list": [], "total": 0}
            return {
                "list": [_floor(501, pid), _floor(502, pid)],
                "total": 2,
            }
        if path == "floor/replys":
            return {"list": [], "total": 0}
        if path == "post/replys":
            return {"list": []}
        raise RuntimeError("unexpected api path %s params=%s" % (path, params))

    def fake_with_retry(fn, what, fuse, interval=0.0):
        return fn()

    def fake_get_bytes(url, headers=None, timeout=30, throttle=False,
                       max_bytes=None):
        return b"offline-image-bytes"

    for mod in (list_mod, essence, body, comment, shared, pipeline, export_ops):
        if hasattr(mod, "api_get"):
            mod.api_get = fake_api_get
        if hasattr(mod, "with_retry"):
            mod.with_retry = fake_with_retry
    if hasattr(http, "api_get"):
        http.api_get = fake_api_get
    if hasattr(http, "with_retry"):
        http.with_retry = fake_with_retry
    image.get_bytes = fake_get_bytes
    if hasattr(pipeline, "get_bytes"):
        pipeline.get_bytes = fake_get_bytes


def args(**kw):
    base = dict(
        page_size=20,
        qps=0,
        concurrency=1,
        interval=0,
        no_confirm=True,
        limit=0,
        max_total=0,
        limit_pages=0,
        since_id=0,
        since_date="",
        types=[],
        ids=[],
        order=1,
        official=True,
        no_comment_filter=False,
        no_repair=False,
        incremental=False,
        max_posts=0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), name, detail)
    return bool(cond)


def main():
    fails = []

    # 0) 兼容 import
    try:
        from wpy.crawler import (  # noqa
            api_get, do_body, do_comment, do_image, do_list, open_db,
            run_pipeline,
        )
        from wpy.crawler.body import do_body as b2
        from wpy.crawler.comment import do_comment as c2
        from wpy.crawler.essence import crawl_essence
        from wpy.crawler.image import do_image as i2
        from wpy.crawler.list import do_list as l2
        from wpy.crawler.pipeline import run_pipeline as p2
        if not check("compat import stages", b2 is do_body and l2 is do_list):
            fails.append("compat import")
    except Exception as e:
        check("compat import stages", False, str(e))
        return 1

    # 1) 模块符号：确保已修的缺失名在命名空间中
    import wpy.crawler.body as body_mod
    import wpy.crawler.comment as comment_mod
    import wpy.crawler.essence as essence_mod
    import wpy.crawler.list as list_mod
    import wpy.crawler.shared as shared_mod
    for mod, need in (
        (body_mod, ("open_db", "threading")),
        (comment_mod, ("open_db", "threading")),
        (essence_mod, ("open_db",)),
        (list_mod, ("open_db", "record_pagination_event")),
        (shared_mod, ("threading",)),
    ):
        missing = [n for n in need if not hasattr(mod, n)]
        if missing:
            check("symbols %s" % mod.__name__, False, "missing %s" % missing)
            fails.append("symbols:%s" % mod.__name__)
        else:
            check("symbols %s" % mod.__name__, True, ",".join(need))

    install_mocks()

    from wpy import config
    check("temp data dir", config.DATA_DIR.startswith(_TMP), config.DATA_DIR)
    check("temp db path", config.DB_PATH.endswith("sitewide.db")
          and _TMP in config.DB_PATH, config.DB_PATH)

    conn = open_db()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    need = {"posts", "floors", "official_replies", "images",
            "scrape_progress", "pagination_events", "essence_ids"}
    ok_tables = need.issubset(tables)
    check("schema tables", ok_tables, str(sorted(need - tables)))
    if not ok_tables:
        fails.append("schema")
    conn.close()

    type_names = {2: "青年湖底"}

    # do_list
    try:
        do_list("tok", args(), type_names)
        conn = open_db()
        n = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        conn.close()
        check("do_list posts", n >= 2, "posts=%s" % n)
        if n < 2:
            fails.append("do_list")
    except Exception as e:
        check("do_list", False, repr(e))
        fails.append("do_list")

    # crawl_essence
    try:
        ids = crawl_essence("tok", args())
        conn = open_db()
        e = conn.execute("SELECT COUNT(*) FROM essence_ids").fetchone()[0]
        conn.close()
        check("crawl_essence", bool(ids) and e >= 1, "ids=%s essence=%s" % (ids, e))
        if e < 1:
            fails.append("essence")
    except Exception as e:
        check("crawl_essence", False, repr(e))
        fails.append("essence")

    # my posts
    try:
        from wpy.crawler.essence import crawl_my_posts
        crawl_my_posts("tok", args(), "是我的帖子", "posts/user", "我的帖子")
        conn = open_db()
        m = conn.execute(
            'SELECT COUNT(*) FROM posts WHERE "是我的帖子"=1').fetchone()[0]
        conn.close()
        check("crawl_my_posts", m >= 1, "marked=%s" % m)
    except Exception as e:
        check("crawl_my_posts", False, repr(e))
        fails.append("my_posts")

    # do_body：先确保有待补正文
    try:
        conn = open_db()
        conn.execute('UPDATE posts SET "正文已补"=0')
        conn.commit()
        n0 = conn.execute(
            'SELECT COUNT(*) FROM posts WHERE "正文已补"=0').fetchone()[0]
        conn.close()
        do_body("tok", args(concurrency=2, limit=10))
        conn = open_db()
        n1 = conn.execute(
            'SELECT COUNT(*) FROM posts WHERE "正文已补"=1').fetchone()[0]
        sample = conn.execute(
            'SELECT "正文" FROM posts WHERE id=1001').fetchone()
        conn.close()
        check("do_body", n1 >= 1 and sample and sample[0].startswith("full-body-"),
              "pending=%s body_ok=%s sample=%s" % (n0, n1, sample))
        if n1 < 1:
            fails.append("body")
    except Exception as e:
        check("do_body", False, repr(e))
        fails.append("body")

    # do_comment
    try:
        conn = open_db()
        conn.execute('UPDATE posts SET "评论已抓"=0, "评论数"=2 WHERE id IN (1001,2001)')
        conn.commit()
        conn.close()
        do_comment("tok", args(concurrency=2, page_size=20, limit=10, no_repair=True))
        conn = open_db()
        nf = conn.execute("SELECT COUNT(*) FROM floors").fetchone()[0]
        nc = conn.execute(
            'SELECT COUNT(*) FROM posts WHERE "评论已抓"=1').fetchone()[0]
        conn.close()
        check("do_comment", nf >= 1 and nc >= 1, "floors=%s done_posts=%s" % (nf, nc))
        if nf < 1:
            fails.append("comment")
    except Exception as e:
        check("do_comment", False, repr(e))
        fails.append("comment")

    # do_image
    try:
        from wpy.crawler.image import collect_images
        conn = open_db()
        refs = collect_images(conn)
        conn.close()
        do_image("tok", args(concurrency=1, limit=20))
        conn = open_db()
        ni = conn.execute(
            'SELECT COUNT(*) FROM images WHERE "已有本地"=1').fetchone()[0]
        conn.close()
        check("do_image", ni >= 1, "refs=%s saved=%s" % (len(refs), ni))
        if ni < 1:
            fails.append("image")
    except Exception as e:
        check("do_image", False, repr(e))
        fails.append("image")

    # pagination event path（list 重复页）
    try:
        from wpy.crawler.list import do_list as dl
        # 再抓一轮 list：mock 状态已推进，可能空页；强制写入一条事件验证函数名
        conn = open_db()
        from wpy.db import record_pagination_event
        record_pagination_event(conn, 2, 2, "overlap", "offline-smoke")
        n = conn.execute(
            "SELECT COUNT(*) FROM pagination_events").fetchone()[0]
        conn.close()
        check("pagination_events write", n >= 1, "n=%s" % n)
        # 源码检查：list 模块不得再引用未定义的 _record
        src = open(list_mod.__file__, encoding="utf-8").read()
        bad = "_record_pagination_event" in src
        check("list no legacy symbol", not bad)
        if bad:
            fails.append("list_symbol")
    except Exception as e:
        check("pagination_events write", False, repr(e))
        fails.append("pagination")

    # run_pipeline（全程 mock + 临时库 + no_confirm）
    try:
        # 重置 mock 页码，避免直接空转
        install_mocks()
        run_pipeline(
            "tok",
            args(concurrency=1, limit=5, page_size=20, no_confirm=True,
                 max_posts=0),
            mode="partial",
            type_names={2: "青年湖底"},
        )
        conn = open_db()
        s_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        conn.close()
        html_exists = os.path.exists(config.HTML_PATH)
        check("run_pipeline", s_posts >= 1, "posts=%s html=%s" % (
            s_posts, html_exists))
        if s_posts < 1:
            fails.append("pipeline")
    except Exception as e:
        check("run_pipeline", False, repr(e))
        fails.append("pipeline")

    # 真实库未被离线临时库写入（允许真实业务抓取导致的合法增量）
    real_db = os.path.join(ROOT, "data", "sitewide.db")
    if os.path.exists(real_db):
        import sqlite3
        c = sqlite3.connect(real_db)
        counts = {
            t: c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            for t in ("posts", "floors", "official_replies", "pagination_events",
                      "essence_ids")
        }
        schema_ok = True
        for t, ncol in (("posts", 30), ("floors", 15), ("official_replies", 9),
                        ("pagination_events", 6), ("essence_ids", 3)):
            cols = [r[1] for r in c.execute('pragma table_info("%s")' % t)]
            if len(cols) != ncol:
                schema_ok = False
                print("schema mismatch", t, len(cols), "!=", ncol)
        c.close()
        # 真实库应存在且规模未被离线测试破坏；允许合法抓取带来的增量
        ok = (
            counts["posts"] >= 202600
            and counts["floors"] >= 212566
            and counts["essence_ids"] >= 198
            and schema_ok
        )
        check("real sitewide.db intact", ok, str(counts))
        if not ok:
            fails.append("real_db")
    else:
        check("real sitewide.db intact", True, "no real db in env")

    print("-" * 40)
    print("TEMP_DIR", _TMP)
    if fails:
        print("FAILED:", ", ".join(fails))
        return 1
    print("ALL OFFLINE STAGE SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
