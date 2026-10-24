# -*- coding: utf-8 -*-
"""Phase 9: snapshot sitewide.db counts/schema for before/after compare."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "sitewide.db")
OUT = os.path.join(os.path.dirname(ROOT), "docs", "refactor")


def snapshot(label: str):
    c = sqlite3.connect(DB)
    tables = [
        "posts", "floors", "official_replies", "pagination_events",
        "essence_ids", "images", "scrape_progress",
    ]
    counts = {t: c.execute("select count(*) from %s" % t).fetchone()[0]
              for t in tables}
    schema = {t: [r[1] for r in c.execute('pragma table_info("%s")' % t)]
              for t in tables}
    marks = {
        "body_ok": c.execute('select count(*) from posts where "正文已补"=1').fetchone()[0],
        "body_fail": c.execute('select count(*) from posts where "正文已补"=-1').fetchone()[0],
        "cmt_ok": c.execute('select count(*) from posts where "评论已抓"=1').fetchone()[0],
        "cmt_fail": c.execute('select count(*) from posts where "评论已抓"=-1').fetchone()[0],
        "essence_mark": c.execute('select count(*) from posts where "精华"=1').fetchone()[0],
        "my_posts": c.execute('select count(*) from posts where "是我的帖子"=1').fetchone()[0],
        "my_fav": c.execute('select count(*) from posts where "是我的收藏"=1').fetchone()[0],
        "max_id": c.execute("select max(id) from posts").fetchone()[0],
        "type2": c.execute('select count(*) from posts where "板块ID"=2').fetchone()[0],
        "img_local": c.execute('select count(*) from images where "已有本地"=1').fetchone()[0],
    }
    progress = c.execute(
        'select "type",name,status,last_page,fetched from scrape_progress '
        'order by "type"').fetchall()
    pe = c.execute(
        "select kind, count(*) from pagination_events group by kind").fetchall()
    c.close()
    data = {
        "label": label,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "db_size": os.path.getsize(DB),
        "counts": counts,
        "marks": marks,
        "schema_cols": {k: len(v) for k, v in schema.items()},
        "scrape_progress": progress,
        "pagination_kinds": pe,
    }
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "phase09_db_%s.json" % label)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return data


if __name__ == "__main__":
    snapshot(sys.argv[1] if len(sys.argv) > 1 else "before")
