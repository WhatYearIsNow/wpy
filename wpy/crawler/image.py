# -*- coding: utf-8 -*-
"""图片本地化阶段。"""
from __future__ import annotations

import json
import os
import threading
import urllib.parse

from ..config import IMG_DIR, PIC_API, TIMEOUT
from ..db import (
    collect_image_refs, image_done_names, mark_image_fail, mark_image_ok, open_db,
)
from ..http import Fuse, get_bytes, pace
from ..runlog import log
from .shared import run_workers

def collect_images(conn):
    """从帖子与楼层里汇总所有图片文件名。"""
    return collect_image_refs(conn)


def _json_list(s):
    try:
        v = json.loads(s) if s else []
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def do_image(token, args):
    conn = open_db()
    fuse = Fuse()
    items = collect_images(conn)
    log("=== 阶段四：图片本地化（共 %d 个引用，并发 %d）==="
        % (len(items), args.concurrency))
    os.makedirs(IMG_DIR, exist_ok=True)

    have = image_done_names(conn)
    queue = [it for it in items if it[2] and it[2] not in have]
    if args.limit:
        queue = queue[:args.limit]
    if not queue:
        log("没有待下载的图片。")
        conn.close()
        return

    lock = threading.Lock()
    counter = {"ok": 0, "fail": 0, "skip": 0}

    def worker():
        while True:
            with lock:
                if not queue:
                    return
                src, oid, fname = queue.pop()
            base = fname.split("#")[0]          # #masked 只是展示标记，不是文件名
            path = os.path.join(IMG_DIR, base)
            try:
                parent = os.path.dirname(path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    size = os.path.getsize(path)
                else:
                    url = PIC_API + "origin/" + urllib.parse.quote(base)
                    # 历史行为：图片下载不走全局限速（JSON API 才限速）
                    blob = get_bytes(url, timeout=TIMEOUT, throttle=False)
                    tmp = path + ".part"
                    with open(tmp, "wb") as f:
                        f.write(blob)
                    os.replace(tmp, path)
                    size = len(blob)
                    pace(args.interval)
                with lock:
                    mark_image_ok(conn, base, src, oid, size)
                    counter["ok"] += 1
                    n = counter["ok"]
                if n % 200 == 0:
                    log("  已下载 %d 张（失败 %d）" % (n, counter["fail"]))
            except Exception as e:
                with lock:
                    mark_image_fail(conn, base, src, oid, e)
                    counter["fail"] += 1

    run_workers(worker, args.concurrency)
    conn.close()
    log("阶段四结束：成功 %d 张，失败 %d 张（重新运行会只补失败的）"
        % (counter["ok"], counter["fail"]))

