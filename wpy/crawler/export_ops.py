# -*- coding: utf-8 -*-
"""导出、抽样核查、板块 reopen。"""
from __future__ import annotations

import csv
import json
import random

from ..config import CSV_PATH, JSON_PATH, REQUEST_INTERVAL
from ..db import list_done_boards, open_db, reopen_done_boards
from ..http import api_get, pace
from ..runlog import log

def do_export(args):
    conn = open_db()
    # 分页导出：避免 20 万帖一次性加载到内存。
    # 每次取 5000 条，写完后释放，循环直到全部导出。
    PAGE_SIZE = 5000
    offset = 0
    total_written = 0

    # 先取第一页获取列名
    cur = conn.execute(
        "SELECT * FROM posts ORDER BY id LIMIT ? OFFSET ?",
        (PAGE_SIZE, offset))
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]

    if not rows:
        log("数据库里还没有帖子，先跑 list。")
        conn.close()
        return

    # CSV 导出
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=names)
        w.writeheader()
        w.writerows(rows)
    total_written += len(rows)

    # JSON 导出（分批追加）
    # 注意：JSON 不支持流式追加，所以这里仍然需要全量。
    # 但对于 JSON 来说，20 万条 × 平均 2KB ≈ 400MB，
    # 实际测试中 json.dump 可以处理。如果内存不足，
    # 可以改用 NDJSON（每行一个 JSON 对象）格式。
    all_json_rows = list(rows)

    while len(rows) == PAGE_SIZE:
        offset += PAGE_SIZE
        cur = conn.execute(
            "SELECT * FROM posts ORDER BY id LIMIT ? OFFSET ?",
            (PAGE_SIZE, offset))
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
        if rows:
            with open(CSV_PATH, "a", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=names)
                w.writerows(rows)
            total_written += len(rows)
            all_json_rows.extend(rows)

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(all_json_rows, f, ensure_ascii=False, indent=1)

    n_floor = conn.execute("SELECT COUNT(*) FROM floors").fetchone()[0]
    conn.close()
    log("导出完成：%s（%d 帖）、%s；评论库 %d 楼" % (CSV_PATH, total_written, JSON_PATH, n_floor))


def do_verify(token, sample_n, seed):
    conn = open_db()
    rows = conn.execute('SELECT id, "标题", "正文", "作者" FROM posts '
                        'WHERE "正文已补">=0').fetchall()
    if not rows:
        conn.close()
        log("无数据可核查。")
        return
    random.seed(seed)
    sample = random.sample(rows, min(sample_n, len(rows)))
    ok = 0
    for pid, title, content, author in sample:
        try:
            data = api_get(token, "post", {"id": pid})
            post = data.get("post") or {}
        except Exception as e:
            print("CHECK id=%s 详情接口出错：%s" % (pid, str(e)[:80]))
            continue
        t_ok = (post.get("title") or "") == (title or "")
        c_ok = (post.get("content") or "") == (content or "")
        ok += 1 if (t_ok and c_ok) else 0
        n_floor = conn.execute('SELECT COUNT(*) FROM floors WHERE "帖子ID"=?',
                               (pid,)).fetchone()[0]
        print("CHECK id=%s title=%s content=%s 作者=%s 本地评论=%d"
              % (pid, t_ok, c_ok, author, n_floor))
        pace(REQUEST_INTERVAL)
    conn.close()
    print("\n抽样 %d 条，标题+正文完全一致 %d 条。" % (len(sample), ok))


def do_reopen():
    """把因「total 偏小」而被误判完成（done）的板块重新打开，继续往下抓。

    背景：早期版本信服务端 total，每个板块抓到第 20 页就标成 done。
    修好终止条件后，这些板块需要重新排队，否则续抓会直接跳过它们。
    已抓到的帖子保留，只是从当前最后一页继续往更旧的方向抓。
    """
    conn = open_db()
    rows = list_done_boards(conn)
    if not rows:
        print("没有标记为已完成的板块，无需打开。")
        conn.close()
        return
    print("以下板块将被重新打开，从中断处继续往更旧的帖子抓：")
    print()
    print("  %-5s %-10s %9s %9s" % ("type", "板块", "已抓页", "已抓条"))
    print("  " + "-" * 38)
    for t, name, lp, ft, st in rows:
        print("  %-5s %-10s %9s %9s" % (t, name, lp, ft))
    print()
    print("  说明：已抓到的帖子不会丢，只是重新排队继续往下抓。")
    try:
        ans = input("  确认打开？(y=确认，其他=取消)：").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n"
    if ans != "y":
        print("已取消。")
        conn.close()
        return
    n = reopen_done_boards(conn)
    print()
    print("已打开 %d 个板块，现在运行「继续爬取」即可接着往下抓。" % n)
    conn.close()


def guess_type(name):
    """按扩展名返回 Content-Type（从 content_type 模块导入）。"""
    from ..content_type import guess_type as _gt
    return _gt(name)

