# -*- coding: utf-8 -*-
"""Phase 9 real-API probes (small, non-full crawl)."""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("PYTHONUTF8", "1")

from wpy.cli import _ensure_utf8_stdio
_ensure_utf8_stdio()

from wpy.auth import get_token
from wpy.db import open_db
from wpy.http import LIMITER, api_get
from wpy.crawler.comment import diagnose_comments, fetch_floors, fetch_floor_replies
from wpy.crawler.list import fetch_list
from wpy.crawler.shared import map_type_names
from wpy.config import DEFAULT_TYPE_NAMES


def probe_list(token):
    print("=== LIST probe (type 7 / page1 / page_size 5) ===")
    for tid in (7, 2):
        t0 = time.time()
        items, total, tp = fetch_list(token, tid, 1, 5)
        dt = int((time.time() - t0) * 1000)
        ids = [it.get("id") for it in items]
        print("type", tid, "ms", dt, "n", len(items), "total", total,
              "total_pages", tp, "ids", ids[:5])
        # empty page check
        items2, total2, _ = fetch_list(token, tid, 9999, 5)
        print("type", tid, "deep_page_n", len(items2), "total", total2)


def probe_post_detail(token, pid):
    print("=== POST detail", pid, "===")
    data = api_get(token, "post", {"id": pid})
    post = data.get("post") or {}
    print("title", (post.get("title") or "")[:40],
          "content_len", len(post.get("content") or ""),
          "imgs", len(post.get("image_urls") or []),
          "comments", post.get("comment_count"))


def probe_floors(token, pid):
    print("=== FLOORS diagnose + single page", pid, "===")
    try:
        diagnose_comments(token, pid)
    except Exception as e:
        print("diagnose error", e)
    t0 = time.time()
    try:
        floors, total = fetch_floors(token, pid, 1, 20, 1)
        print("floors n", len(floors), "total", total,
              "ms", int((time.time() - t0) * 1000))
        if floors:
            f0 = floors[0]
            print("floor0 keys sample",
                  {k: f0.get(k) for k in ("id", "nickname", "sub_floor_cnt", "content")})
            fid = f0.get("id")
            if f0.get("sub_floor_cnt"):
                t1 = time.time()
                subs, st = fetch_floor_replies(token, fid, 0, 10)
                print("subs n", len(subs), "total", st,
                      "ms", int((time.time() - t1) * 1000))
    except Exception as e:
        print("floors probe error", type(e).__name__, e)


def probe_official(token, pid):
    print("=== official reply path", pid, "===")
    try:
        data = api_get(token, "post/replys", {"post_id": pid}, allow_null_data=True)
        lst = (data or {}).get("list") or []
        print("official n", len(lst))
    except Exception as e:
        print("official error", type(e).__name__, e)


def main():
    LIMITER.target = 2.0
    LIMITER.current = 2.0
    token = get_token(interactive=False)
    print("token_ok", bool(token), "qps", LIMITER.target)
    print("posttypes merge", len(map_type_names(token)), "names")
    probe_list(token)
    conn = open_db()
    row = conn.execute(
        'SELECT id FROM posts WHERE "板块ID"=2 AND "评论数">0 '
        'ORDER BY id DESC LIMIT 3').fetchall()
    type1 = conn.execute(
        'SELECT id FROM posts WHERE "板块ID"=1 AND "评论数">0 '
        'ORDER BY id DESC LIMIT 1').fetchone()
    conn.close()
    pids = [r[0] for r in row]
    print("sample pids", pids, "type1", type1)
    if pids:
        probe_post_detail(token, pids[0])
        probe_floors(token, pids[0])
        if type1:
            probe_official(token, type1[0])
            probe_floors(token, type1[0])
    print("PROBE_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
