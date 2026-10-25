# -*- coding: utf-8 -*-
"""argparse CLI 与 main 入口。"""
from __future__ import annotations

import argparse
import sys

from ..auth import do_logout, do_whoami, get_token
from ..config import (
    COMMENT_PAGE_SIZE, DB_PATH, HTML_PATH, MAX_CONCURRENT, PAGE_SIZE,
    REQUEST_INTERVAL, TARGET_QPS,
)
from ..db import open_db
from ..http import FatalError, LIMITER
from .body import do_body
from .comment import diagnose_comments, do_comment
from .export_ops import do_export, do_reopen, do_verify
from .image import do_image
from .list import do_list, do_probe
from .pipeline import do_partial, do_resume, do_update, run_pipeline
from .shared import _parse_board_ids, _parse_ids, map_type_names
from .status_view import do_status

def build_parser():
    p = argparse.ArgumentParser(description="微北洋全站帖子抓取（五阶段流水线）")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--page-size", type=int, default=PAGE_SIZE)
        sp.add_argument("--qps", type=float, default=TARGET_QPS,
                        help="全局速率上限，默认 %.0f。实测 10 QPS 持续 60 秒"
                             "零限流；设为 0 表示不限速（不推荐）" % TARGET_QPS)
        sp.add_argument("--concurrency", type=int, default=MAX_CONCURRENT,
                        help="并发线程数，默认 %d。注意：整体速率由 --qps 统一"
                             "控制，调大并发只是让请求更平滑地填满配额"
                             % MAX_CONCURRENT)
        sp.add_argument("--interval", type=float, default=REQUEST_INTERVAL,
                        help="（已废弃）每个线程各自休眠的秒数，"
                             "仅在 --qps 0 时生效")
        sp.add_argument("--no-confirm", action="store_true",
                        help="跳过开始前的计划确认（脚本化调用时用）")
        sp.add_argument("--limit", type=int, default=0,
                        help="本轮最多处理多少条（0=不限）。所有阶段含义一致："
                             "本轮要处理的数量，不含库里已有的")
        sp.add_argument("--max-total", type=int, default=0,
                        help="库中帖子总数上限（0=不限）。用于「先抓够 N 条就停」，"
                             "会连同库里已有的数量一起算")
        sp.add_argument("--limit-pages", type=int, default=0,
                        help="每个板块本轮最多抓多少页，0 = 不限")
        sp.add_argument("--since-id", type=int, default=0,
                        help="增量抓取：只抓 id 大于该值的帖子（用上次的最大 id）")
        sp.add_argument("--since-date", type=str, default="",
                        help="增量抓取：只抓发布时间晚于该日期的帖子，"
                             "格式 2026-09-01")
        sp.add_argument("--types", type=str, default="",
                        help="只处理指定板块 id，逗号分隔，如 2,3")
        sp.add_argument("--ids", type=str, default="",
                        help="只处理指定帖子 id，逗号分隔（支持粘贴 "
                             "#MP123 或 wpy://school_project/123 分享口令）")
        return sp

    sub.add_parser("probe", help="探测各板块可达性与页数").add_argument(
        "--page-size", type=int, default=PAGE_SIZE)
    add_common(sub.add_parser("list", help="阶段一：抓全站帖子骨架"))
    add_common(sub.add_parser("body", help="阶段二：补全正文与图片"))
    c = sub.add_parser("comment", help="阶段三：抓评论（含楼中楼）")
    add_common(c)
    c.add_argument("--order", type=int, default=1, help="评论排序，默认 1")
    c.add_argument("--official", action="store_true", default=True,
                   help="同时抓校务专区官方回复（默认开启）")
    c.add_argument("--no-comment-filter", action="store_true",
                   help="不跳过评论数为 0 的帖子（默认跳过，省大量请求）")
    add_common(sub.add_parser("image", help="阶段四：下载图片到本地 img/"))
    sub.add_parser("export", help="导出 CSV / JSON")
    b = sub.add_parser("build", help="阶段五：生成 HTML 看板")
    b.add_argument("--max-posts", type=int, default=0,
                   help="看板最多内嵌多少帖（0=全部；数据量大时建议限制）")
    v = sub.add_parser("verify", help="抽样核对详情接口")
    v.add_argument("--sample", type=int, default=10)
    v.add_argument("--seed", type=int, default=42)
    d = sub.add_parser("diagnose", help="诊断评论接口参数（大面积 400 时用）")
    d.add_argument("--id", type=int, default=0,
                   help="用哪个帖子测，留空则自动取库里最新一条")
    st = sub.add_parser("status", help="查看抓取进度")
    st.add_argument("--brief", action="store_true",
                    help="只输出一行摘要（供菜单顶部显示）")
    sub.add_parser("logout", help="删除本地保存的账号密码")
    sub.add_parser("reopen", help="把已标记完成的板块重新打开（修复历史误判后补抓）")
    sv = sub.add_parser("serve", help="启动看板服务（本地/服务器）")
    sv.add_argument("--host", type=str, default="127.0.0.1",
                    help="监听地址。公网部署用 0.0.0.0（建议前面加反代）")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--token", type=str, default=None,
                    help="已停用的旧访问口令参数；不再提供登录或写权限")
    sv.add_argument("--no-open", action="store_true",
                    help="不自动打开浏览器")
    hv = sub.add_parser("hot", help="启动热榜独立服务")
    hv.add_argument("--host", type=str, default="127.0.0.1",
                    help="监听地址")
    hv.add_argument("--port", type=int, default=8766,
                    help="监听端口，默认 8766")
    hv.add_argument("--token", type=str, default=None,
                    help="已停用的旧访问口令参数；不再提供登录或写权限")
    sub.add_parser("whoami", help="查看当前用的是哪个账号（密码不显示）")
    a = sub.add_parser("ask", help="学习板块智能问答（需 .tju_key）")
    a.add_argument("question", nargs="+", help="要问的问题")
    a = add_common(sub.add_parser(
        "all", help="全量抓取：列表 -> 正文 -> 评论 -> 导出 -> 看板"))
    a.add_argument("--max-posts", type=int, default=0,
                   help="看板最多内嵌多少帖（0=全部）")
    # 评论参数：all 会走到评论阶段，缺了这些属性会直接报 AttributeError，
    # 导致整批帖子被误标为「抓取失败」（踩过坑，20 万帖全被标 -1）
    a.add_argument("--order", type=int, default=1, help="评论排序，默认 1")
    a.add_argument("--official", action="store_true", default=True)
    a.add_argument("--no-comment-filter", action="store_true")
    a.add_argument("--no-repair", action="store_true")

    r = add_common(sub.add_parser(
        "resume", help="继续抓取：接着上次的位置把没抓完的抓完"))
    r.add_argument("--max-posts", type=int, default=0)

    pt = add_common(sub.add_parser(
        "partial", help="部分抓取：按板块 / 日期 / Mp 号 指定范围"))
    pt.add_argument("--max-posts", type=int, default=0)

    up = add_common(sub.add_parser(
        "update", help="补充抓取：只抓上次之后的新帖"))
    up.add_argument("--max-posts", type=int, default=0)
    return p, sub


def main(argv=None):
    p, _ = build_parser()
    args = p.parse_args(argv)

    # 应用全局速率设置（所有联网命令都受影响）
    qps = getattr(args, "qps", None)
    if qps is not None:
        LIMITER.target = max(0.0, qps)
        LIMITER.current = LIMITER.target

    if args.cmd == "status":
        do_status(args.brief)
        return
    if args.cmd == "logout":
        do_logout()
        return
    if args.cmd == "reopen":
        do_reopen()
        return
    if args.cmd == "serve":
        from ..server import serve
        serve(port=args.port, open_browser=not args.no_open,
              host=getattr(args, "host", None),
              token=getattr(args, "token", None))
        return
    if args.cmd == "hot":
        from ..hot_server import serve as hot_serve
        hot_serve(host=getattr(args, "host", None),
                  port=args.port,
                  token=getattr(args, "token", None))
        return
    if args.cmd == "whoami":
        do_whoami()
        return
    if args.cmd == "ask":
        from ..ask import main as ask_main
        sys.argv = ["wpy", "ask"] + args.question  # 传给 ask.py 的 sys.argv[1:]
        ask_main()
        return
    if args.cmd == "export":
        do_export(args)
        return
    if args.cmd == "build":
        from ..ui import build_from_db
        build_from_db(DB_PATH, HTML_PATH, args.max_posts)
        return

    try:
        token = get_token()
    except FatalError as e:
        print(str(e))
        sys.exit(2)
    if args.cmd in ("all", "resume", "partial", "update"):
        args.types = _parse_board_ids(getattr(args, "types", ""))
        args.ids = _parse_ids(getattr(args, "ids", ""))
        if not hasattr(args, "since_id"):
            args.since_id = 0
        if not hasattr(args, "since_date"):
            args.since_date = ""
        if not hasattr(args, "incremental"):
            args.incremental = False
        tn = map_type_names(token)
        if args.cmd == "all":
            run_pipeline(token, args, mode="full", type_names=tn)
        elif args.cmd == "resume":
            do_resume(token, args, tn)
        elif args.cmd == "partial":
            do_partial(token, args, tn)
        else:
            do_update(token, args, tn)
    elif args.cmd == "probe":
        do_probe(token, map_type_names(token), args.page_size)
    elif args.cmd == "diagnose":
        pid = args.id
        if not pid:
            conn = open_db()
            row = conn.execute("SELECT id FROM posts ORDER BY id DESC LIMIT 1").fetchone()
            conn.close()
            if not row:
                print("库里还没有帖子，请先跑 list；或用 --id 指定一个帖子。")
                sys.exit(1)
            pid = row[0]
        diagnose_comments(token, pid)
    elif args.cmd == "list":
        args.types = _parse_board_ids(args.types)
        args.ids = []
        do_list(token, args, map_type_names(token))
    elif args.cmd == "body":
        args.types = _parse_board_ids(args.types)
        args.ids = _parse_ids(args.ids)
        do_body(token, args)
    elif args.cmd == "comment":
        args.types = _parse_board_ids(args.types)
        args.ids = _parse_ids(args.ids)
        if not getattr(args, "order", None):
            args.order = 1
        do_comment(token, args)
    elif args.cmd == "image":
        args.types = _parse_board_ids(args.types)
        do_image(token, args)
    elif args.cmd == "verify":
        do_verify(token, args.sample, args.seed)

