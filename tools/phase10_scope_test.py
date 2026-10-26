# -*- coding: utf-8 -*-
"""Phase 10 临时库测试：repair 作用域 + invalid type 提示。不访问真实 sitewide.db。"""

from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from wpy.cli import _ensure_utf8_stdio

_ensure_utf8_stdio()

_TMP = tempfile.mkdtemp(prefix="wpy_phase10_")
os.environ["WPY_DATA_DIR"] = _TMP
os.environ.setdefault("PYTHONUTF8", "1")

from wpy.db import open_db, reset_incomplete_comments, save_posts, mark_post_comment_status
from wpy.crawler.shared import usable_types, warn_unknown_types
from wpy.crawler.comment import do_comment
from wpy.crawler.list import do_list


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), name, detail)
    return bool(cond)


def seed(conn):
    posts = []
    # type2 incomplete: floors 0 < comment_count 2, marked done
    posts.append(
        {
            "id": 2001,
            "uid": 1,
            "title": "a",
            "content": "c",
            "created_at": "2026-01-01",
            "nickname": "n",
            "user_info": {},
            "type": 2,
            "department": {},
            "tag": {},
            "comment_count": 2,
            "image_urls": [],
            "like_count": 0,
            "fav_count": 0,
            "visit_count": 0,
            "e_tag": "",
            "solved": False,
        }
    )
    posts.append(
        {
            "id": 1001,
            "uid": 2,
            "title": "b",
            "content": "c",
            "created_at": "2026-01-01",
            "nickname": "n",
            "user_info": {},
            "type": 1,
            "department": {},
            "tag": {},
            "comment_count": 2,
            "image_urls": [],
            "like_count": 0,
            "fav_count": 0,
            "visit_count": 0,
            "e_tag": "",
            "solved": False,
        }
    )
    posts.append(
        {
            "id": 3001,
            "uid": 3,
            "title": "c",
            "content": "c",
            "created_at": "2026-01-01",
            "nickname": "n",
            "user_info": {},
            "type": 3,
            "department": {},
            "tag": {},
            "comment_count": 2,
            "image_urls": [],
            "like_count": 0,
            "fav_count": 0,
            "visit_count": 0,
            "e_tag": "",
            "solved": False,
        }
    )
    save_posts(conn, posts)
    for pid in (2001, 1001, 3001):
        mark_post_comment_status(conn, pid, 1)  # pretend complete but no floors


def flags(conn):
    return {
        pid: conn.execute('SELECT "评论已抓" FROM posts WHERE id=?', (pid,)).fetchone()[0]
        for pid in (2001, 1001, 3001)
    }


def main():
    fails = []
    conn = open_db()
    seed(conn)
    f0 = flags(conn)
    check("seed marked done", f0 == {2001: 1, 1001: 1, 3001: 1}, str(f0))

    # 1) ids scope
    n = reset_incomplete_comments(conn, ids=[2001])
    f = flags(conn)
    check("repair ids scope", f[2001] == 0 and f[1001] == 1 and f[3001] == 1 and n >= 0, str(f))
    if not (f[2001] == 0 and f[1001] == 1):
        fails.append("repair_ids")

    # reset all done again
    mark_post_comment_status(conn, 2001, 1)
    mark_post_comment_status(conn, 1001, 1)
    mark_post_comment_status(conn, 3001, 1)

    # 2) types scope
    reset_incomplete_comments(conn, types=[2, 3])
    f = flags(conn)
    check("repair types scope", f[2001] == 0 and f[3001] == 0 and f[1001] == 1, str(f))
    if not (f[1001] == 1 and f[2001] == 0):
        fails.append("repair_types")

    mark_post_comment_status(conn, 2001, 1)
    mark_post_comment_status(conn, 3001, 1)

    # 3) limit without scope → skip global repair
    n = reset_incomplete_comments(conn, limit=5)
    f = flags(conn)
    check("repair limit skip global", n == 0 and all(v == 1 for v in f.values()), str(f))
    if n != 0 or not all(v == 1 for v in f.values()):
        fails.append("repair_limit_skip")

    # 4) global repair when no scope/limit
    n = reset_incomplete_comments(conn)
    f = flags(conn)
    check("repair global", f[2001] == 0 and f[1001] == 0 and f[3001] == 0 and n > 0, str(f))
    if f[1001] != 0:
        fails.append("repair_global")

    # 5) do_comment with ids+limit: only those posts in queue, no mass global reset
    # mark others done complete-looking again, leave floors empty
    mark_post_comment_status(conn, 1001, 1)
    mark_post_comment_status(conn, 3001, 1)
    # 2001 is 0 after global repair
    args = SimpleNamespace(
        page_size=20,
        concurrency=1,
        limit=1,
        types=[],
        ids=[2001],
        order=1,
        official=False,
        no_comment_filter=False,
        no_repair=False,
        interval=0,
    )

    # mock api to avoid network
    import wpy.crawler.comment as comment_mod
    import wpy.crawler.body as body_mod

    def fake_api_get(token, path, params=None, allow_null_data=False):
        if path == "floors":
            return {"list": [], "total": 0}
        if path == "post/replys":
            return {"list": []}
        return {"list": [], "total": 0}

    def fake_with_retry(fn, what, fuse, interval=0.0):
        return fn()

    comment_mod.api_get = fake_api_get
    comment_mod.with_retry = fake_with_retry
    do_comment("tok", args)
    f = flags(conn)
    check(
        "do_comment ids scoped repair",
        f[1001] == 1 and f[3001] == 1,
        "after ids comment run " + str(f),
    )
    if f[1001] != 1 or f[3001] != 1:
        fails.append("do_comment_scope")

    # 6) invalid types helpers
    tn = {1: "校务专区", 2: "青年湖底", 3: "学习新知", 13: "美食专区"}
    unknown = warn_unknown_types([7, 2, 99], tn, "test")
    check("warn_unknown_types", unknown == [7, 99], str(unknown))
    check("usable_types", usable_types([7, 2, 99], tn) == [2], str(usable_types([7, 2, 99], tn)))
    check("usable empty=request all", usable_types([], tn) == [1, 2, 3, 13])

    # 7) do_list invalid types → explicit message, no write
    args_l = SimpleNamespace(
        page_size=5,
        types=[7],
        limit=5,
        since_id=0,
        since_date="",
        incremental=False,
        limit_pages=0,
        max_total=0,
        interval=0,
    )
    import wpy.crawler.list as list_mod

    list_mod.api_get = fake_api_get
    list_mod.with_retry = fake_with_retry
    before = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    do_list("tok", args_l, tn)
    after = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    check("do_list invalid type no-op explicit", after == before, "posts %s->%s" % (before, after))

    conn.close()
    print("-" * 40)
    print("TEMP", _TMP)
    if fails:
        print("FAILED:", ", ".join(fails))
        return 1
    print("ALL PHASE10 TEMP TESTS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
