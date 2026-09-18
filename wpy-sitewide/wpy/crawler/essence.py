# -*- coding: utf-8 -*-
"""精华集合与「我的帖子/收藏」。"""
from __future__ import annotations

from ..config import PAGE_SIZE, RECOMMEND_TYPE
from ..db import _int, mark_essence_ids, mark_posts_flag, open_db, save_posts
from ..http import FatalError, Fuse, api_get, pace
from ..runlog import log

def crawl_my_posts(token, args, mark_column, api_path, what):
    """抓「我的帖子」或「我的收藏」，并把命中的帖子打上来源标记。

    两个接口形状一样（分页返回帖子列表），区别只在路径：
      posts/user  -> 我发的帖子
      posts/fav   -> 我收藏的帖子

    mark_column 是要置 1 的列名。帖子若已在库里就更新标记，
    不在库里则先存基本信息（这些帖子可能不属于我抓的那 8 个板块）。
    """
    conn = open_db()
    fuse = Fuse()
    page_size = args.page_size
    log("=== 抓取「%s」===" % what)
    ids, page = set(), 1
    while True:
        try:
            data = api_get(token, api_path, {"page": page, "page_size": page_size})
        except FatalError:
            conn.close()
            raise
        except Exception as e:
            log("第 %d 页失败，停止本轮：%s" % (page, str(e)[:120]))
            break
        items = data.get("list") or []
        if not items:
            break
        if page == 1 or page % 5 == 0:
            log("  第 %d 页，本页 %d 条，累计 %d 条" % (page, len(items), len(ids)))
        try:
            save_posts(conn, items)          # 不在库里就存基本信息
        except Exception as e:
            log("  第 %d 页入库失败：%s" % (page, str(e)[:100]))
        for it in items:
            pid = _int(it.get("id"))
            if pid:
                ids.add(pid)
        if len(items) < page_size:
            break
        page += 1
        pace(args.interval)

    if ids:
        mark_posts_flag(conn, ids, mark_column, 1)
    total = conn.execute('SELECT COUNT(*) FROM posts WHERE "%s"=1'
                         % mark_column).fetchone()[0]
    conn.close()
    log("「%s」：本轮 %d 条，库中累计标记 %d 条" % (what, len(ids), total))
    return ids


def crawl_essence(token, args, page_size=None):
    """单独抓「精华」集合：etag=recommend 的列表。

    为什么不能当板块抓：源码里精华 tab 是 `type=0 & etag=recommend`，
    recommend 是**过滤器**不是帖子的属性。直接按 type=0 抓会把返回的帖子
    存进它们各自的真实板块（这是对的），但无法标记哪些是精华。
    正确做法是把这个列表里的 id 记下来，标记成精华，帖子本身不动。

    返回本次记录到的精华帖子 id 集合。
    """
    page_size = page_size or PAGE_SIZE
    conn = open_db()
    fuse = Fuse()
    log("=== 抓取精华集合（etag=recommend）===")
    ids, page = set(), 1
    while True:
        try:
            data = api_get(token, "posts", {
                "type": RECOMMEND_TYPE, "search_mode": 0, "etag": "recommend",
                "content": "", "tag_id": "", "department_id": "",
                "page_size": page_size, "page": page,
            })
        except FatalError:
            conn.close()
            raise
        except Exception as e:
            log("精华第 %d 页失败，停止本轮：%s" % (page, str(e)[:120]))
            break
        items = data.get("list") or []
        if not items:
            break
        for it in items:
            pid = _int(it.get("id"))
            if pid:
                ids.add(pid)
        if len(items) < page_size:
            break
        if page == 1 or page % 5 == 0:
            log("  精华第 %d 页，累计 %d 条" % (page, len(ids)))
        page += 1
        pace(args.interval)

    if ids:
        total, matched = mark_essence_ids(conn, ids)
    else:
        total = conn.execute("SELECT COUNT(*) FROM essence_ids").fetchone()[0]
        matched = conn.execute(
            'SELECT COUNT(*) FROM posts WHERE "精华"=1').fetchone()[0]
    conn.close()
    log("精华集合：本轮 %d 条，累计记录 %d 条，其中已在本库的 %d 条"
        % (len(ids), total, matched))
    return ids

