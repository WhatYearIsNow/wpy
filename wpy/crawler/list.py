# -*- coding: utf-8 -*-
"""列表抓取阶段。"""

from __future__ import annotations

from ..config import (
    EMPTY_STREAK_STOP,
    MAX_GAP_WORTH_REPORT,
    RECOMMEND_TYPE,
    REQUEST_INTERVAL,
)
from ..db import (
    _int,
    count_posts,
    ensure_type_row,
    mark_status,
    next_page,
    open_db,
    record_pagination_event,
    save_posts,
    update_scrape_page,
)
from ..http import FatalError, Fuse, api_get, pace, with_retry
from ..runlog import log
from .shared import (
    _parse_since_date,
    crawlable_types,
    usable_types,
    warn_if_only_recommend,
    warn_unknown_types,
)


def fetch_list(token, tid, page, page_size):
    """拉一页列表，返回 (items, total, total_pages)。

    ⚠ 重要的实测结论（2026-09-16，真实账号验证）：

    服务端返回的 `total` **不可信，不能用来判断"抓完了"**。
    它声称每个板块只有 20 页，但实测：
      - 青年湖底 第 1200 页仍能返回数据（total 说只有 20 页）
      - 学习新知 第 300 页仍能返回
    也就是说 total 只是某个时间窗口的页数，真实页数远大于它。
    早期版本用 `page >= total_pages` 判结束，导致每个板块只抓 20 页
    （400 条）就静默"完成"——青年湖底实际只抓到了约 2%。

    因此这里把 total_pages 标记为 None（不可用），由调用方改用
    「翻到空页」判结束。返回值保留 total 仅供显示参考。
    """
    data = api_get(
        token,
        "posts",
        {
            "type": tid,
            "search_mode": 0,
            "etag": "",
            "content": "",
            "tag_id": "",
            "department_id": "",
            "page_size": page_size,
            "page": page,
        },
    )
    items = data.get("list") or []
    total = data.get("total")
    return items, total, None  # total_pages 不可信，一律 None


def do_probe(token, type_names, page_size):
    """每个板块只拉第 1 页：确认可达性、总量、页数、以及 page_size 是否生效。"""
    log("=== 探测模式（每个板块只拉第 1 页）===")
    print("%-5s %-12s %10s %8s %8s  %s" % ("type", "板块", "total", "页数", "本页条数", "状态"))
    print("-" * 62)
    total_all = 0
    for tid in crawlable_types(type_names):
        name = type_names[tid]
        try:
            items, total, tp = fetch_list(token, tid, 1, page_size)
            flag = "可达" if len(items) <= page_size else "page_size 生效"
            print("%-5s %-12s %10s %8s %8s  %s" % (tid, name, total, tp, len(items), flag))
            if isinstance(total, int) and total > 0:
                total_all += total
        except Exception as e:
            print("%-5s %-12s %10s %8s %8s  失败：%s" % (tid, name, "-", "-", "-", str(e)[:50]))
        pace(REQUEST_INTERVAL)
    print("-" * 62)
    print("各板块 total 之和约 %s 条（含精华重复计，仅作量级参考）" % total_all)
    print("说明：total 若等于 1，则它表示总页数而非总条数，脚本会按此自适应。")
    print("      若「本页条数」等于请求的 page_size，说明该 page_size 被服务端接受；")
    print("      想提速可用 list --page-size 试更大的值，脚本会按实际返回自适应。")


def do_list(token, args, type_names):
    conn = open_db()
    page_size = args.page_size
    fuse = Fuse()

    # 增量模式：只抓比分界线更新的帖子。id 越大越新（实测），所以按 id 判最准；
    # 没给 since_id 就自动取库里已有的最大 id。
    since_id = getattr(args, "since_id", 0) or 0
    since_date = _parse_since_date(getattr(args, "since_date", ""))
    auto_since = False
    if getattr(args, "incremental", False) and not since_id:
        row = conn.execute("SELECT MAX(id) FROM posts").fetchone()
        since_id = (row[0] or 0) if row else 0
        auto_since = True
    if since_id and auto_since:
        log("增量模式：只抓 id > %d 的新帖子（自动取库中最大 id）" % since_id)
    elif since_id:
        log("增量模式：只抓 id > %d 的帖子" % since_id)
    if since_date:
        log("日期限制：只保留 %s 及以后发布的帖子" % since_date)

    # --types 校验：避免「不在映射内却 exit 0 静默不抓」
    if getattr(args, "types", None):
        unknown = warn_unknown_types(args.types, type_names, "list")
        valid_types = usable_types(args.types, type_names)
        if unknown and not valid_types:
            log("错误：--types 均不在当前可用板块映射中，列表阶段不执行。")
            conn.close()
            return
        # 仅保留有效 type，后续遍历行为与原来一致
        args.types = valid_types

    # 防呆：如果过滤后没有任何可遍历的板块（典型情况是用户只选了精华），
    # 必须明确说出来，否则会静默什么都不做、看起来像卡住。
    targets = crawlable_types(type_names)
    if not targets:
        warn_if_only_recommend(type_names)
        log("这次没有任何可遍历的板块，列表阶段跳过（上面已说明原因）。")
        conn.close()
        return
    if getattr(args, "types", None):
        targets = [t for t in targets if t in set(args.types)]
        if not targets:
            log(
                "错误：有效板块过滤后为空，列表阶段不执行。"
                "可用：%s" % "、".join("%s=%s" % (k, type_names[k]) for k in sorted(type_names))
            )
            conn.close()
            return

    log(
        "=== 阶段一：列表抓取（page_size=%d，板块 %d 个%s%s）==="
        % (
            page_size,
            len(targets),
            "，本轮上限 %d 条" % args.limit if args.limit else "",
            "，增量" if since_id else "",
        )
    )

    # --max-total 是「库中总条数」上限：先看库里已有多少，只在余额不够时继续抓。
    # 与 --limit 分开是因为两者语义不同：--limit 是「本轮处理多少」，
    # --max-total 是「库里有 N 条就停」。混用一个参数最容易误解。
    max_total = getattr(args, "max_total", 0) or 0
    already = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    budget = None
    if max_total:
        budget = max_total - already
        if budget <= 0:
            log("库里已有 %d 条，已达到总数上限 %d 条，无需再抓。" % (already, max_total))
            conn.close()
            return
        log("库里已有 %d 条，本轮还可抓 %d 条（总数上限 %d）" % (already, budget, max_total))
    # --limit 在 list 阶段同样表示「本轮最多写入多少条」
    if args.limit:
        budget = args.limit if budget is None else min(budget, args.limit)

    for tid in crawlable_types(type_names):
        name = type_names[tid]
        if args.types and tid not in args.types:
            continue
        ensure_type_row(conn, tid, name, page_size)
        # 增量模式总是从第 1 页开始（新帖都在最前面），不沿用旧的翻页进度
        if since_id:
            page, tp = 1, None
        else:
            page, tp = next_page(conn, tid)
            if page is None:
                log("板块 %s(%s) 已完成，跳过" % (name, tid))
                continue
        log("板块 %s(%s) 从第 %s 页开始" % (name, tid, page))

        limit_page = (page - 1 + args.limit_pages) if args.limit_pages else None
        hit_old = False
        seen_ids = set()  # 该板块已见过的 id，用于去重与断层检测
        prev_min_id = None  # 上一页的最小 id，用于发现漏帖断层
        empty_streak = 0  # 连续多少页没有新帖
        while True:
            if limit_page and page > limit_page:
                log("板块 %s 达到本轮页数上限，下次运行可续抓" % name)
                break
            try:
                items, total, total_pages = with_retry(
                    lambda: fetch_list(token, tid, page, page_size),
                    "板块%s第%d页" % (name, page),
                    fuse,
                )
            except FatalError:
                conn.close()
                raise
            except Exception as e:
                mark_status(conn, tid, "error", str(e)[:300])
                log("板块 %s 第 %d 页失败，进度已存，下次可续抓：%s" % (name, page, str(e)[:150]))
                break

            if not items:
                mark_status(conn, tid, "done")
                log("板块 %s 返回空页，判定结束（已抓 %d 页）" % (name, page - 1))
                break

            # ---- 分页漂移 / 置顶帖错位检测 ----
            # 两个已知的接口行为会造成重复或漏帖：
            #  1) 置顶帖（e_tag=top）被强行插在首页最前面，占掉几个位置，
            #     导致后续页整体后移 —— 实测第 1 页末是 458896、第 2 页头是
            #     458894，中间的 458895 被直接跳过（漏帖）。
            #  2) 翻页期间有人发新帖，列表整体后移（重叠）。
            # 这里用「已见过的 id 集合」统计重复，并把 id 断层记下来供事后补漏。
            page_ids = [_int(it.get("id")) for it in items if it.get("id")]
            dup = 0
            if page_ids and seen_ids:
                dup = sum(1 for i in page_ids if i in seen_ids)
                if dup:
                    record_pagination_event(
                        conn,
                        tid,
                        page,
                        "overlap",
                        "本页 %d 条中 %d 条与之前页重复" % (len(page_ids), dup),
                    )
                # id 断层只做「疑似」记录，不能当成漏帖：
                # 全站 id 是所有板块共享的，本板块两页之间夹着别的板块的帖子
                # 是正常的（实测缺口动辄几十个且 id 连续，正是别的板块）。
                # 真正需要警惕的是「本板块帖子被置顶帖挤出窗口」，那种情况
                # 缺口很小（1~3 个），所以这里只在缺口特别小时才提示。
                if prev_min_id is not None:
                    lo, hi = min(page_ids) + 1, prev_min_id
                    if 0 < (hi - lo) <= MAX_GAP_WORTH_REPORT:
                        gap = [i for i in range(lo, hi) if i not in seen_ids]
                        if gap:
                            record_pagination_event(
                                conn,
                                tid,
                                page,
                                "gap",
                                "与上页之间可能有 %d 个帖子被置顶帖挤出"
                                "（id %s）" % (len(gap), ",".join(str(g) for g in gap[:5])),
                            )
            if page_ids:
                prev_min_id = (
                    min(page_ids) if prev_min_id is None else min(prev_min_id, min(page_ids))
                )
            seen_ids.update(page_ids)

            # ---- 终止判定 ----
            # ⚠ 不能用 total（服务端给的页数严重偏小，实测差 50 倍以上），
            # 也不只看「短页」，因为置顶帖会让首页看起来不满。
            # 规则：返回空页 → 结束；连续多页都没有新帖 → 认为已追平。
            # 后者是必要的保险：万一服务端在深层页码上重复返回同一批数据，
            # 没有这个判断会无限翻下去。
            short_page = len(items) < page_size
            new_count = len(page_ids) - dup
            if new_count <= 0:
                empty_streak += 1
            else:
                empty_streak = 0

            wrote = items
            # 增量模式：只保留比 since_id 新的；遇到更老的就说明该板块已追平
            if since_id:
                fresh = [it for it in items if _int(it.get("id")) > since_id]
                if len(fresh) < len(items):
                    hit_old = True
                wrote = fresh
                # 页内已入库的帖子也 UPSERT 刷新列表侧字段（实测列表接口返回
                # 全文，无损；进度/集合列不在写入范围）。评论数变大后，「补抓
                # 准备」会把帖子标回待抓，评论阶段才能补到新增评论。
                stale = [it for it in items if _int(it.get("id")) <= since_id]
                if stale:
                    save_posts(conn, stale)
            # 日期限制：只保留指定日期及以后发布的；遇到更早的说明该板块已到边界
            if since_date:
                fresh = [it for it in wrote if str(it.get("created_at") or "")[:10] >= since_date]
                if len(fresh) < len(wrote):
                    hit_old = True
                wrote = fresh
            # 总条数上限：只写入需要的部分，并记录该页实际写入了多少
            if budget is not None:
                keep = min(len(wrote), budget)
                wrote = wrote[:keep]
                budget -= keep
            if wrote:
                save_posts(conn, wrote)
            update_scrape_page(conn, tid, page, total, total_pages, len(wrote))
            got = count_posts(conn)

            if page == 1 or page % 20 == 0:
                # 不再显示百分比：服务端的 total 严重偏小（说 20 页、
                # 实际有 1200+ 页），拿它算百分比会一直卡在 100% 却还在抓，
                # 反而误导。改为显示已翻页数和累计条数。
                log(
                    "  %s 已翻 %d 页，本页新增 %d 条，全库累计 %d 条"
                    % (name, page, len(wrote), got)
                )
            if budget is not None and budget <= 0:
                # 达到总条数上限：不能把板块标记为 done，否则续抓会跳过剩余页
                log("已达到本轮总条数上限 %d 条，停下。重新运行可继续续抓。" % args.limit)
                conn.close()
                return
            if hit_old:
                # 增量/日期模式追平：之后的页只会更旧，直接结束该板块
                mark_status(conn, tid, "done")
                log("板块 %s 已追平（第 %d 页出现早于分界线的帖子）" % (name, page))
                break
            # 收尾判定：短页说明到底了；连续多页没新帖说明已追平
            if short_page:
                mark_status(conn, tid, "done")
                log("板块 %s 完成（翻到最后一页，共 %d 页）" % (name, page))
                break
            if empty_streak >= EMPTY_STREAK_STOP:
                mark_status(conn, tid, "done")
                log(
                    "板块 %s 连续 %d 页没有新帖，判定已抓完（共 %d 页）"
                    % (name, EMPTY_STREAK_STOP, page)
                )
                break
            page += 1
            pace(REQUEST_INTERVAL)

    conn.close()
    log("阶段一结束。重新运行 list 可继续未完成的板块。")
