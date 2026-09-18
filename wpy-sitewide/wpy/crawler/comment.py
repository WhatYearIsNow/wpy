# -*- coding: utf-8 -*-
"""评论 / 楼中楼 / 官方回复阶段。"""
from __future__ import annotations

import threading
import urllib.parse

from ..config import COMMENT_PAGE_SIZE, LAKE_API, REQUEST_INTERVAL
from ..db import (
    _int, iter_floors, mark_post_comment_status, official_row, open_db,
    reset_incomplete_comments, save_floors, save_official_replies,
)
from ..http import (
    BadRequest, FatalError, Fuse, FuseTripped, api_get, pace, request_json,
    with_retry,
)
from ..runlog import Ticker, log
from .shared import build_post_filter, run_workers

def fetch_floors(token, post_id, page, page_size, order):
    """拉一页楼层。

    ⚠ 实测（2026-09-16）：`only_owner` 必须是**整数** 0/1，传字符串 "false"
    会直接返回 code=400 请求参数错误。源码里它也是 ValueNotifier<int>。
    order 同为整数（0/1）。

    ⚠ 列表接口的 `sub_floors` **只带前几条**，完整楼中楼看 `sub_floor_cnt`，
    缺的部分要再调 floor/replys（见 expand_sub_floors）。只信嵌套数组会漏评论。
    """
    data = api_get(token, "floors", {
        "post_id": post_id, "page": page, "page_size": page_size,
        "order": int(order), "only_owner": 0,
    })
    return data.get("list") or [], data.get("total")


def fetch_floor_replies(token, floor_id, page, page_size=10):
    """拉某顶层楼下的楼中楼（官方 reply 详情页用的接口）。

    源码 feedback_service.getFloorReplyById：page 从 0 起，page_size=10，
    并带 pageBase=0。返回 data.list。
    """
    data = api_get(token, "floor/replys", {
        "floor_id": floor_id, "page": int(page), "page_size": int(page_size),
        "pageBase": 0,
    }, allow_null_data=True)
    if not isinstance(data, dict):
        return [], None
    return data.get("list") or [], data.get("total")


def _merge_floors_by_id(base, extra):
    """按 id 合并两批楼层，保留先出现的字段，去重。"""
    out = list(base or [])
    seen = {_int(f.get("id")) for f in out if f.get("id")}
    for f in extra or []:
        fid = _int(f.get("id"))
        if not fid or fid in seen:
            continue
        seen.add(fid)
        out.append(f)
    return out


def fetch_all_floor_replies(token, floor_id, expected, fuse, what_prefix,
                            page_size=10):
    """分页拉齐某一楼的全部楼中楼。expected 为 sub_floor_cnt（可为 0/未知）。"""
    all_replies, page = [], 0
    # 官方首屏 page=0；最多多翻 2 页兜底，避免 total 语义不清时空转
    max_pages = 50
    if expected and expected > 0:
        max_pages = max(3, (int(expected) + page_size - 1) // page_size + 2)
    while page < max_pages:
        lst, _total = with_retry(
            lambda p=page: fetch_floor_replies(token, floor_id, p, page_size),
            "%s楼中楼第%d页" % (what_prefix, page), fuse)
        if not lst:
            break
        all_replies = _merge_floors_by_id(all_replies, lst)
        if expected and len(all_replies) >= int(expected):
            break
        if len(lst) < page_size:
            break
        page += 1
        pace()
    return all_replies


def expand_sub_floors(token, floors, fuse, pid, page_size=10):
    """把列表接口截断的 sub_floors 补全到 sub_floor_cnt。

    背景：floors 列表每条只内嵌前几条楼中楼，子楼数在 sub_floor_cnt。
    只存嵌套数组会导致「子楼数=8 实际=5」这类漏抓——看板上表现为
    回复对不上号、甚至整段对话缺层。
    """
    for f in floors or []:
        expand_one_floor_subs(token, f, fuse, pid, page_size, depth=0)
    return floors


def expand_one_floor_subs(token, f, fuse, pid, page_size, depth):
    if not isinstance(f, dict) or depth > 4:
        return
    declared = _int(f.get("sub_floor_cnt"))
    embedded = f.get("sub_floors") or []
    if declared > len(embedded):
        fid = _int(f.get("id"))
        if fid:
            full = fetch_all_floor_replies(
                token, fid, declared, fuse,
                "帖子%s楼%s" % (pid, fid), page_size=page_size)
            embedded = _merge_floors_by_id(embedded, full)
            f["sub_floors"] = embedded
            # 数量仍以声明为准：服务端可能含已删/屏蔽层，宁可多对几页
            f["sub_floor_cnt"] = max(declared, len(embedded))
    for sub in f.get("sub_floors") or []:
        expand_one_floor_subs(token, sub, fuse, pid, page_size, depth + 1)


def diagnose_comments(token, post_id):
    """逐个试参数组合，定位 floors 接口到底接受什么参数。

    当评论抓取大面积 400 时运行这个，它会把每种组合的结果打印出来，
    避免靠猜。结果会写进日志。
    """
    log("=== 评论接口诊断：post_id=%s ===" % post_id)
    attempts = [
        ("源码原样：only_owner=0(int), page_size=10",
         {"post_id": post_id, "page": 1, "page_size": 10, "order": 1,
          "only_owner": 0}),
        ("only_owner=0, page_size=20",
         {"post_id": post_id, "page": 1, "page_size": 20, "order": 1,
          "only_owner": 0}),
        ("only_owner='0'(字符串)",
         {"post_id": post_id, "page": 1, "page_size": 10, "order": 1,
          "only_owner": "0"}),
        ("去掉 only_owner",
         {"post_id": post_id, "page": 1, "page_size": 10, "order": 1}),
        ("去掉 order 和 only_owner",
         {"post_id": post_id, "page": 1, "page_size": 10}),
        ("只留 post_id 和 page",
         {"post_id": post_id, "page": 1}),
        ("order=0",
         {"post_id": post_id, "page": 1, "page_size": 10, "order": 0,
          "only_owner": 0}),
    ]
    ok_found = None
    for label, params in attempts:
        try:
            r = request_json(LAKE_API + "floors?" + urllib.parse.urlencode(params), token)
            code = r.get("code")
            if code == 200:
                n = len((r.get("data") or {}).get("list") or [])
                tp = (r.get("data") or {}).get("total")
                print("  可用 ✓  %-38s -> 本页 %s 楼, total=%s" % (label, n, tp))
                if ok_found is None:
                    ok_found = label
            else:
                print("  失败 ✗  %-38s -> code=%s %s"
                      % (label, code, str(r.get("msg"))[:40]))
        except Exception as e:
            print("  异常 ✗  %-38s -> %s" % (label, str(e)[:50]))
        pace(REQUEST_INTERVAL)

    print()
    if ok_found:
        print("结论：可用组合为「%s」。脚本当前用的就是第一种（源码原样）。" % ok_found)
    else:
        print("结论：所有组合都失败。可能该账号无权读评论，或接口已变更。")
        print("      请把上面完整输出发给开发者。")
    return ok_found


def fetch_official(token, post_id):
    data = api_get(token, "post/replys", {"post_id": post_id},
                   allow_null_data=True)
    return (data or {}).get("list") or []


def do_comment(token, args):
    conn = open_db()
    fuse = Fuse()
    # 修复历史漏抓：作用域与本轮命令一致；全局 repair 仅在无 types/ids 且无 limit 时
    if not getattr(args, "no_repair", False):
        reset_incomplete_comments(
            conn,
            types=getattr(args, "types", None),
            ids=getattr(args, "ids", None),
            limit=int(getattr(args, "limit", 0) or 0),
        )
    where, params = build_post_filter(args, "comment")
    rows = conn.execute(
        'SELECT id, "板块ID", "评论数" FROM posts WHERE %s '
        'ORDER BY "评论数" DESC, id' % where, params).fetchall()
    if args.limit:
        rows = rows[:args.limit]
    log("=== 阶段三：评论抓取（待处理 %d 帖，并发 %d，每页 %d 楼）===="
        % (len(rows), args.concurrency, args.page_size))
    if not rows:
        log("没有待抓评论的帖子。")
        conn.close()
        return

    lock = threading.Lock()
    queue = list(rows)
    counter = {"posts": 0, "floors": 0, "fail": 0, "pages": 0}
    stop = {"flag": False}
    ticker = Ticker("抓评论", interval=6)
    ticker.total = len(rows)
    ticker.start_beat()

    def worker():
        while True:
            with lock:
                if stop["flag"] or not queue:
                    return
                pid, tid, ccount = queue.pop()
            try:
                # 熔断后立刻退出，不再对该帖做任何请求
                fuse.check()
                n_floors = crawl_one_post(
                    conn, token, pid, args, fuse, lock, counter)
                with lock:
                    mark_post_comment_status(conn, pid, 1)
                    counter["posts"] += 1
                    counter["floors"] += n_floors
                    ticker.bump()
                    n = counter["posts"]
                if n == 1 or n % 10 == 0:
                    log("  已抓评论 %d/%d 帖，累计 %d 楼（失败 %d）"
                        % (n, len(rows), counter["floors"], counter["fail"]))
            except FuseTripped:
                return
            except FatalError:
                stop["flag"] = True
                raise
            except BadRequest as e:
                # 参数类系统错误：整体停下，**不**把帖子标记为 -1。
                # 否则一旦参数写错，整库帖子会被永久标记为"抓过"，无法挽回。
                with lock:
                    stop["flag"] = True
                    counter["fatal"] = str(e)[:200]
                return
            except Exception as e:
                with lock:
                    counter["fail"] += 1
                    mark_post_comment_status(conn, pid, -1)
                if counter["fail"] % 10 == 0:
                    log("  评论失败 %d 帖（最后：%s）" % (counter["fail"], str(e)[:90]))

    try:
        run_workers(worker, args.concurrency)
    except FatalError:
        conn.close()
        raise
    finally:
        ticker.stop()
    conn.close()
    if counter.get("fatal"):
        log("阶段三中止：%s" % counter["fatal"])
        log("这是请求参数类错误，所有帖子都会被标记为未抓，修好参数后直接重跑即可。")
        log("可运行 `python wpy_sitewide.py diagnose` 逐个试参数组合定位问题。")
        raise SystemExit(2)
    log("阶段三结束：成功 %d 帖、共 %d 楼，失败 %d 帖（标记 -1，不再重试）"
        % (counter["posts"], counter["floors"], counter["fail"]))


def crawl_one_post(conn, token, pid, args, fuse, lock, counter):
    """抓一帖的全部楼层（含楼中楼）与官方回复，写库。返回 (页数, 楼层数)。

    评论接口的 total 语义未知（可能总楼层数、可能总页数），所以不靠它判结束，
    统一用「返回空页 或 不满一页」作为终止条件——两种语义下都成立。

    每页顶层楼拿回后，还要把截断的楼中楼用 floor/replys 补齐再入库。
    """
    reply_ps = max(1, int(getattr(args, "page_size", COMMENT_PAGE_SIZE) or 10))
    # 楼中楼官方按 10 条/页；顶层 page_size 不必相同，这里单独取
    sub_ps = 10 if reply_ps < 10 else min(reply_ps, 50)
    page, n_total = 1, 0
    while True:
        floors, total = with_retry(
            lambda: fetch_floors(token, pid, page, args.page_size, args.order),
            "帖子%s评论第%d页" % (pid, page), fuse)
        if not floors:
            break

        expand_sub_floors(token, floors, fuse, pid, page_size=sub_ps)
        tuples = list(iter_floors(floors, pid))
        with lock:
            save_floors(conn, tuples)
        n_total += len(tuples)

        if len(floors) < args.page_size:
            break
        page += 1
        pace(args.interval)

    # 校务专区（type=1）帖另有官方回复，单独抓
    if args.official:
        try:
            offs = with_retry(lambda: fetch_official(token, pid),
                              "帖子%s官方回复" % pid, fuse)
            if offs:
                rows = [official_row(f, pid) for f in offs]
                with lock:
                    save_official_replies(conn, rows)
        except FatalError:
            raise
        except Exception as e:
            log("  帖子 %s 官方回复抓取失败（跳过）：%s" % (pid, str(e)[:90]))

    return n_total

