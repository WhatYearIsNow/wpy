# -*- coding: utf-8 -*-
"""流水线编排：full / resume / partial / update。"""
from __future__ import annotations

import json
import time

from ..config import DB_PATH, HTML_PATH
from ..db import open_db, save_posts, update_post_body
from ..http import FatalError, Fuse, api_get, pace, with_retry
from ..runlog import log, mark_finished, write_run_state
from .body import do_body
from .comment import do_comment
from .essence import crawl_essence, crawl_my_posts
from .export_ops import do_export
from .list import do_list
from .shared import (
    confirm_plan, map_type_names, usable_types, warn_if_only_recommend,
    warn_unknown_types,
)
from .status_view import do_status

def run_pipeline(token, args, mode="full", type_names=None):
    """四种抓取模式共用的流水线。

    mode:
      full      全量：把每个板块从第 1 页抓到最后一页
      resume    继续：沿用各板块已存的页码，接着往下抓
      partial   部分：只抓指定板块 / 指定帖子
      update    补充：只抓比分界线更新的新帖
    """
    started = time.time()
    titles = {
        "full": "全量抓取（把每个板块抓到底）",
        "resume": "继续抓取（接着上次的位置）",
        "partial": "部分抓取（指定范围）",
        "update": "补充抓取（只要新帖）",
    }
    log("=" * 60)
    log("%s：列表 -> 正文 -> 评论 -> 看板" % titles.get(mode, mode))
    log("中途可随时按 Ctrl+C 停下，下次运行从断点继续")
    log("=" * 60)

    if type_names is None:
        type_names = map_type_names(token)

    # 补齐评论阶段需要的参数。
    # 以前只在 comment 子命令里定义了 --order，all/resume 走同一条流水线却拿不到，
    # 于是评论阶段对每一帖都抛 AttributeError，整批被标记为失败。
    # 这里统一兜底，任何入口都不会再缺。
    for k, v in (("order", 1), ("official", True),
                 ("no_comment_filter", False)):
        if not hasattr(args, k):
            setattr(args, k, v)

    # 开始前先给出工作量与预计耗时，等用户确认（非交互时自动通过）
    if not getattr(args, "no_confirm", False):
        try:
            conn = open_db()
            ok = confirm_plan(conn, set(args.types) if args.types else set(),
                              type_names, getattr(args, "since_date", ""),
                              args.limit)
            conn.close()
        except Exception:
            ok = True
        if not ok:
            log("已取消，未做任何请求。")
            return

    TOTAL_STEPS = 7

    def banner(step, title, hint=""):
        # 总的进度条：6 步各占一段，让人一眼看到整体完成度
        pct = (step - 1) * 100.0 / TOTAL_STEPS
        filled = int(24 * pct / 100)
        bar = "[%s%s] %5.1f%%" % ("█" * filled, "░" * (24 - filled), pct)
        log("")
        log("=" * 60)
        log("总进度 %s  第 %s/%s 步" % (bar, step, TOTAL_STEPS))
        log("本步：%s" % title)
        if hint:
            log(hint)
        log("=" * 60)
        write_run_state("运行中（第 %s 步 / 共 %s 步）" % (step, TOTAL_STEPS),
                        title)

    banner(1, "抓帖子列表",
           "全量：从每个板块第 1 页抓到底" if mode == "full" else "")
    do_list(token, args, type_names)

    banner(2, "抓取精华集合", "精华不是板块，是 etag=recommend 的筛选结果，单独记录")
    crawl_essence(token, args)

    banner(3, "抓取我的帖子与收藏", "分开标记，看板里可单独筛选")
    crawl_my_posts(token, args, "是我的帖子", "posts/user", "我的帖子")
    crawl_my_posts(token, args, "是我的收藏", "posts/fav", "我的收藏")

    banner(4, "补全正文与图片", "逐帖调详情接口，拿完整正文和图片链接")
    do_body(token, args)

    banner(5, "抓评论楼层", "最耗时的一步，含楼中楼；每 10 帖报一次进度")
    do_comment(token, args)

    banner(6, "导出表格", "生成 CSV 与 JSON")
    do_export(args)

    banner(7, "生成看板", "生成服务版空壳页面（数据走本地 API）")
    from ..ui import build_from_db
    build_from_db(DB_PATH, HTML_PATH, getattr(args, "max_posts", 0))

    el = int(time.time() - started)
    log("")
    log("=" * 60)
    log("全部完成，总用时 %d 分 %d 秒" % (el // 60, el % 60))
    log("=" * 60)
    mark_finished("已完成（总用时 %d 分 %d 秒）" % (el // 60, el % 60))
    do_status(brief=True)
    print()
    print("看板已生成，可直接双击打开：")
    print("  " + HTML_PATH)


def do_resume(token, args, type_names=None):
    """继续抓取：沿用已存页码往下抓。

    与 all 的区别：all 用于第一次（板块状态是 pending 从头抓），
    resume 用于上次没抓完的情况——它会把因达到条数上限而停在中间的
    板块继续往下抓。两者实际调用同一条流水线，因为断点逻辑是同一套。
    """
    conn = open_db()
    states = conn.execute(
        'SELECT "type", name, status, last_page, total_pages '
        'FROM scrape_progress ORDER BY "type"').fetchall()
    conn.close()
    pending = [s for s in states if s[2] != "done"]
    if not states:
        log("还没开始抓过，将从头开始（等同全量抓取）。")
    elif pending:
        log("以下板块还没抓完，继续：")
        for t, name, st, lp, tp in pending:
            log("  %s(%s)：已抓 %s 页%s"
                % (name, t, lp, "/%s" % tp if tp else ""))
        log("已抓完的板块会自动跳过。")
    else:
        log("所有板块都标记为已抓完。如需只补新帖，请用「补充抓取」。")
    run_pipeline(token, args, mode="resume", type_names=type_names)


def do_update(token, args, type_names=None):
    """补充抓取：只抓比分界线更新的帖子。

    分界线默认取库里已有的最大 id（实测 id 越大越新）。
    评论/正文阶段会自动只处理新抓进来的那些帖子，因为脚本按
    「正文已补=0 / 评论已抓=0」筛选待处理项。
    """
    conn = open_db()
    latest = max_post_id(conn)
    conn.close()
    args.incremental = True
    if not args.since_id:
        args.since_id = latest
    log("库里最新帖子 id = %d" % latest)
    log("补充抓取：只取 id > %d 的新帖" % args.since_id)
    run_pipeline(token, args, mode="update", type_names=type_names)


def do_partial(token, args, type_names=None):
    """部分抓取：按板块 / 日期 / Mp 号 指定范围。

    三种范围可以任意组合，未指定的维度视为不限。
    """
    scoped = {}
    if args.types:
        warn_unknown_types(args.types, type_names, "partial")
        valid = usable_types(args.types, type_names)
        if not valid:
            log("错误：指定的板块都不在当前可用映射中，partial 不执行。可选：" +
                "、".join("%s=%s" % (k, v) for k, v in sorted(type_names.items())))
            return
        args.types = valid
        scoped = {t: type_names[t] for t in valid}
        log("限定板块：" + "、".join("%s(%s)" % (v, k) for k, v in scoped.items()))
        warn_if_only_recommend(scoped)
    else:
        scoped = type_names

    if args.since_date:
        log("限定日期：%s 之后发布的帖子" % args.since_date)
    if args.ids:
        log("限定帖子：%d 条（直接按 id 抓，跳过板块遍历）" % len(args.ids))
        # 指定了具体帖子就不用遍历板块，直接把这批 id 的详情与评论抓下来
        conn = open_db()
        placeholders = ",".join("?" * len(args.ids))
        existing = [r[0] for r in conn.execute(
            "SELECT id FROM posts WHERE id IN (%s)" % placeholders, args.ids)]
        conn.close()
        missing = [i for i in args.ids if i not in existing]
        if missing:
            log("以下帖子库里还没有，将先抓它们的基本信息：%s"
                % ",".join(str(i) for i in missing))
            fetch_posts_by_id(token, args, missing)

    run_pipeline(token, args, mode="partial",
                 type_names=scoped if not args.ids else {})


def fetch_posts_by_id(token, args, ids):
    """按 id 直接抓帖子基本信息（不经过板块列表）。

    列表接口不支持按 id 查，但详情接口支持，所以直接用详情接口把
    基本信息补进 posts 表，供后续正文/评论阶段使用。
    """
    conn = open_db()
    fuse = Fuse()
    for pid in ids:
        try:
            data = with_retry(lambda: api_get(token, "post", {"id": pid}),
                              "帖子%s详情" % pid, fuse)
            post = data.get("post") or {}
            if not post.get("id"):
                log("  帖子 %s 不存在或无权查看" % pid)
                continue
            save_posts(conn, [post])
            imgs = post.get("image_urls") or []
            update_post_body(conn, pid, post.get("content") or "", imgs, len(imgs))
            log("  已取到帖子 %s：%s" % (pid, (post.get("title") or "")[:30]))
        except FatalError:
            raise
        except Exception as e:
            log("  帖子 %s 抓取失败：%s" % (pid, str(e)[:80]))
        pace(args.interval)
    conn.close()

