# -*- coding: utf-8 -*-
"""正文与图片链接补全阶段。"""
from __future__ import annotations

import threading

from ..db import mark_post_body_failed, open_db, update_post_body
from ..http import BadRequest, FatalError, Fuse, FuseTripped, api_get, pace, with_retry
from ..runlog import Ticker, log
from .shared import build_post_filter, run_workers

def do_body(token, args):
    conn = open_db()
    fuse = Fuse()
    where, params = build_post_filter(args, "body")
    # MP号 = posts.id；升序配队列尾部 pop，实际从 MP号 最大（最新）的帖子开始补
    rows = conn.execute('SELECT id FROM posts WHERE %s ORDER BY id' % where,
                        params).fetchall()
    if args.limit:
        rows = rows[:args.limit]
    log("=== 阶段二：补全正文（待处理 %d 条，并发 %d）===" % (len(rows), args.concurrency))
    if not rows:
        log("没有待补全的帖子。")
        conn.close()
        return

    lock = threading.Lock()
    counter = {"done": 0, "fail": 0}
    # worker 用 queue.pop() 从尾部取任务：列表尾部是 id 最大的帖子，先被补
    queue = [r[0] for r in rows]
    stop = {"flag": False}
    ticker = Ticker("补正文", interval=6)
    ticker.total = len(rows)
    ticker.start_beat()

    def worker():
        while True:
            with lock:
                if stop["flag"] or not queue:
                    return
                pid = queue.pop()
            try:
                fuse.check()   # 熔断后立刻退出，不再对该帖做请求
                data = with_retry(lambda: api_get(token, "post", {"id": pid}),
                                  "帖子%s详情" % pid, fuse)
                post = data.get("post") or {}
                imgs = post.get("image_urls") or []
                with lock:
                    update_post_body(
                        conn, pid, post.get("content") or "", imgs, len(imgs),
                        uid=post.get("uid"), nickname=post.get("nickname") or "")
                    counter["done"] += 1
                    n = counter["done"]
                    ticker.bump()
                if n == 1 or n % 25 == 0:
                    log("  已补正文 %d/%d 条（失败 %d）"
                        % (n, len(rows), counter["fail"]))
            except FuseTripped:
                return
            except FatalError:
                stop["flag"] = True
                raise
            except BadRequest as e:
                # 参数系统性错误：整体停下，不把帖子标记为 -1
                with lock:
                    stop["flag"] = True
                    counter["fatal"] = str(e)[:200]
                return
            except Exception as e:
                with lock:
                    counter["fail"] += 1
                    # 详情接口对已删除帖子会报错，标记跳过，避免每轮重复重试
                    mark_post_body_failed(conn, pid)
                    conn.commit()
                if counter["fail"] % 10 == 0:
                    log("  详情失败 %d 条（最后：%s）" % (counter["fail"], str(e)[:90]))
            pace(args.interval)

    try:
        run_workers(worker, args.concurrency)
    except FatalError:
        conn.close()
        raise
    finally:
        ticker.stop()
    conn.close()
    if counter.get("fatal"):
        log("阶段二中止：%s" % counter["fatal"])
        log("这是请求参数类错误，未处理的帖子仍标记为未抓，修好后直接重跑即可。")
        raise SystemExit(2)
    log("阶段二结束：成功 %d 条，失败 %d 条（失败的标记为 -1，不再重试）"
        % (counter["done"], counter["fail"]))

