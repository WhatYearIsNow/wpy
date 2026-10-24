# -*- coding: utf-8 -*-
"""SQLite 持久化层：schema、迁移、进度与帖子/楼层/图片写入。

抓取阶段只负责「取数据 → 调用 db 保存」；不要在这里发网络请求。
禁止 DROP 业务表；迁移只做增量补列/改名，保留已有数据。
"""
import json
import sqlite3
import time

from .config import (
    DB_PATH, DEFAULT_TYPE_NAMES, RECOMMEND_TYPE,
)
from .runlog import log

__all__ = [
    "POST_COLUMNS", "FLOOR_COLUMNS", "OFFICIAL_COLUMNS", "IMAGE_COLUMNS",
    "POST_COL_NAMES", "POST_BASE_NAMES", "open_db", "save_posts",
    "save_floors", "save_official_replies", "mark_posts_flag",
    "mark_essence_ids", "ensure_type_row", "next_page", "mark_status",
    "record_pagination_event", "status_summary",
]


POST_COLUMNS = [
    ("id", "INTEGER PRIMARY KEY"),
    ("uid", "INTEGER"),
    ("标题", "TEXT"),
    ("正文", "TEXT"),
    ("发布时间", "TEXT"),
    ("作者", "TEXT"),
    ("等级", "INTEGER"),
    ("头像", "TEXT"),
    ("板块ID", "INTEGER"),
    ("分管部门", "TEXT"),
    ("标签", "TEXT"),
    ("标签ID", "INTEGER"),
    ("点赞数", "INTEGER"),
    ("收藏数", "INTEGER"),
    ("评论数", "INTEGER"),
    ("浏览量", "INTEGER"),
    ("评分", "INTEGER"),
    ("校区", "INTEGER"),
    ("类型变体", "INTEGER"),
    ("是否解决", "TEXT"),
    ("置顶标记", "TEXT"),
    ("精华", "INTEGER DEFAULT 0"),
    # 来源标记：同一个帖子可能既在全站列表里，又是你发的/你收藏的。
    # 用两个布尔列表示「属于哪些集合」，不额外建表，查询也简单。
    ("是我的帖子", "INTEGER DEFAULT 0"),
    ("是我的收藏", "INTEGER DEFAULT 0"),
    ("图片数", "INTEGER"),
    ("图片链接", "TEXT"),
    ("正文已补", "INTEGER DEFAULT 0"),
    ("评论已抓", "INTEGER DEFAULT 0"),
    ("评论页数", "INTEGER DEFAULT 0"),
    ("图片已存", "INTEGER DEFAULT 0"),
]

FLOOR_COLUMNS = [
    ("id", "INTEGER PRIMARY KEY"),
    ("帖子ID", "INTEGER"),
    ("父楼ID", "INTEGER DEFAULT 0"),
    ("用户ID", "INTEGER"),
    ("昵称", "TEXT"),
    ("等级", "INTEGER"),
    ("头像", "TEXT"),
    ("内容", "TEXT"),
    ("发布时间", "TEXT"),
    ("回复对象", "TEXT"),
    ("点赞数", "INTEGER"),
    ("点踩数", "INTEGER"),
    ("子楼数", "INTEGER"),
    ("图片", "TEXT"),
    ("是楼主", "INTEGER"),
]

OFFICIAL_COLUMNS = [
    ("id", "INTEGER PRIMARY KEY"),
    ("帖子ID", "INTEGER"),
    ("用户ID", "INTEGER"),
    ("昵称", "TEXT"),
    ("头像", "TEXT"),
    ("内容", "TEXT"),
    ("发布时间", "TEXT"),
    ("点赞数", "INTEGER"),
    ("图片", "TEXT"),
]

IMAGE_COLUMNS = [
    ("文件名", "TEXT PRIMARY KEY"),
    ("来源", "TEXT"),        # post / floor
    ("对象ID", "INTEGER"),   # 帖子 id 或楼层 id
    ("已有本地", "INTEGER DEFAULT 0"),
    ("字节数", "INTEGER DEFAULT 0"),
    ("错误", "TEXT"),
]

POST_COL_NAMES = [n for n, _ in POST_COLUMNS]
# save_posts 写入的列（即「列表接口能提供的字段」）。
# 排除三类由别的阶段单独维护的列，否则每次写页都会把它们抹掉：
#   进度类：正文已补 / 评论已抓 / 评论页数 / 图片已存
#   集合类：是我的帖子 / 是我的收藏（由对应的抓取阶段打标记）
#   集合类：精华（由 crawl_essence 打标记；列表接口通常不返回该字段，
#           若参与写入会被反复重置为 0）
POST_BASE_NAMES = [n for n, _ in POST_COLUMNS
                   if n not in ("正文已补", "评论已抓", "评论页数", "图片已存",
                                "是我的帖子", "是我的收藏", "精华")]


def _int(v):
    if v is None or v == "":
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _create(conn, table, cols, extra=""):
    body = ",\n  ".join('"%s" %s' % (n, t) for n, t in cols)
    conn.execute('CREATE TABLE IF NOT EXISTS "%s" (\n  %s%s\n)' % (table, body, extra))


def open_db(path=None):
    # check_same_thread=False：详情/评论阶段是多线程共用一个连接写的，
    # 所有写操作都在调用方的 lock 内进行，串行化由 lock 保证。
    conn = sqlite3.connect(path or DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _create(conn, "posts", POST_COLUMNS)
    _create(conn, "floors", FLOOR_COLUMNS)
    _create(conn, "official_replies", OFFICIAL_COLUMNS)
    _create(conn, "images", IMAGE_COLUMNS)
    # 精华集合：推荐位返回的帖子 id。单独一张表，因为它是「集合」而非板块，
    # 且推荐位可能返回库里还没有的帖子（那些 id 先记下，等抓到再标记）。
    conn.execute("""CREATE TABLE IF NOT EXISTS essence_ids (
        "post_id"   INTEGER PRIMARY KEY,
        "marked_at" TEXT,
        "source"    TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS scrape_progress (
        "type"        INTEGER PRIMARY KEY,
        "name"        TEXT,
        "page_size"   INTEGER,
        "last_page"   INTEGER DEFAULT 0,
        "total"       INTEGER,
        "total_pages" INTEGER,
        "fetched"     INTEGER DEFAULT 0,
        "last_error"  TEXT,
        "updated_at"  TEXT,
        "status"      TEXT,
        "last_min_id" INTEGER,     -- 已抓到的最小 id，用于探测分页漂移
        "last_max_id" INTEGER
    )""")
    # 分页漂移检测日志：记录每次发现的重复/跳变，便于事后核对数据完整性
    conn.execute("""CREATE TABLE IF NOT EXISTS pagination_events (
        "id"        INTEGER PRIMARY KEY AUTOINCREMENT,
        "type"      INTEGER,
        "page"      INTEGER,
        "kind"      TEXT,       -- overlap(与上页重叠) / regress(id 回升=有帖子漏了)
        "detail"    TEXT,
        "created_at" TEXT
    )""")
    # 增量迁移：老库缺列时补上，不丢已有数据
    # 老库列改名（保留已有数据），必须在补列之前做
    _rename_columns(conn, "posts", {"板块": "分管部门"})
    _migrate(conn, "posts", POST_COLUMNS)
    _migrate(conn, "floors", FLOOR_COLUMNS)
    _migrate(conn, "official_replies", OFFICIAL_COLUMNS)
    _migrate(conn, "images", IMAGE_COLUMNS)
    # scrape_progress 也要迁移：老库没有 last_min_id/last_max_id 两列
    _migrate(conn, "scrape_progress", [
        ("type", "INTEGER PRIMARY KEY"), ("name", "TEXT"),
        ("page_size", "INTEGER"), ("last_page", "INTEGER DEFAULT 0"),
        ("total", "INTEGER"), ("total_pages", "INTEGER"),
        ("fetched", "INTEGER DEFAULT 0"), ("last_error", "TEXT"),
        ("updated_at", "TEXT"), ("status", "TEXT"),
        ("last_min_id", "INTEGER"), ("last_max_id", "INTEGER"),
    ])
    _fix_recommend_rows(conn)
    conn.execute('CREATE INDEX IF NOT EXISTS idx_floors_post ON floors("帖子ID")')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_floors_parent ON floors("父楼ID")')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_off_post ON official_replies("帖子ID")')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_posts_body ON posts("正文已补")')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_posts_cmt ON posts("评论已抓")')
    # 首页查询加速：板块、精华、集合标记、发布时间
    conn.execute('CREATE INDEX IF NOT EXISTS idx_posts_type ON posts("板块ID")')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_posts_essence ON posts("精华")')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_posts_mine ON posts("是我的帖子")')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_posts_fav ON posts("是我的收藏")')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_posts_created ON posts("发布时间")')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_posts_type_created ON posts("板块ID", "id" DESC)')
    conn.commit()
    return conn


def _migrate(conn, table, cols):
    have = {r[1] for r in conn.execute('PRAGMA table_info("%s")' % table)}
    for name, decl in cols:
        if name in have:
            continue
        # ALTER TABLE 不允许加 PRIMARY KEY，主键列在首次建表时已存在
        decl2 = decl.replace("PRIMARY KEY", "").strip()
        if decl2:
            conn.execute('ALTER TABLE "%s" ADD COLUMN "%s" %s' % (table, name, decl2))


def _fix_recommend_rows(conn):
    """一次性修正：把历史上误当成板块抓进来的「精华」数据收拾干净。

    以前 crawlable_types 包含 0，抓推荐位时帖子是按真实板块存的，
    但 scrape_progress 里留下了一行假的「精华(0)」进度，会让用户以为
    精华抓完了、也会让看板多出一个空板块。这里删掉那行假进度。
    （帖子本身不动——它们的板块ID 本来就是对的。）
    """
    try:
        row = conn.execute(
            'SELECT status FROM scrape_progress WHERE "type"=?',
            (RECOMMEND_TYPE,)).fetchone()
        if row:
            conn.execute('DELETE FROM scrape_progress WHERE "type"=?',
                         (RECOMMEND_TYPE,))
            log("已清理历史遗留的「精华(0)」假板块进度行（推荐位不按板块抓）")
    except sqlite3.Error:
        pass


def _rename_columns(conn, table, renames):
    """老库列改名，保留已有数据。

    坑：如果新旧列同时存在（例如改名中途失败、或被 ADD COLUMN 抢先创建），
    单纯 RENAME 会报错或被跳过，导致旧列的数据留在旧列里、新列全空。
    这里做完整处理：目标列已存在时，先把旧列数据搬过去，再删掉旧列。
    """
    have = {r[1] for r in conn.execute('PRAGMA table_info("%s")' % table)}
    for old, new in renames.items():
        if old not in have:
            continue
        if new not in have:
            conn.execute('ALTER TABLE "%s" RENAME COLUMN "%s" TO "%s"'
                         % (table, old, new))
        else:
            # 目标列已存在：搬运数据后删除旧列（只搬新列为空的行）
            conn.execute('UPDATE "%s" SET "%s"="%s" WHERE ("%s" IS NULL OR "%s"=\'\')'
                         ' AND "%s" IS NOT NULL AND "%s"<>\'\''
                         % (table, new, old, new, new, old, old))
            conn.execute('ALTER TABLE "%s" DROP COLUMN "%s"' % (table, old))


def post_tuple(it):
    """接口返回的帖子 JSON -> 与 POST_COLUMNS 顺序一致的元组（不含补全字段）。"""
    dep = it.get("department") or {}
    tag = it.get("tag") or {}
    ui = it.get("user_info") or {}
    imgs = it.get("image_urls") or []
    return (
        _int(it.get("id")), _int(it.get("uid")),
        it.get("title") or "", it.get("content") or "",
        it.get("created_at") or "", it.get("nickname") or "",
        _int(ui.get("level")), ui.get("avatar") or "",
        _int(it.get("type")), dep.get("name") or "",
        tag.get("name") or "", _int(tag.get("id")) if tag.get("id") else None,
        _int(it.get("like_count")), _int(it.get("fav_count")),
        _int(it.get("comment_count")), _int(it.get("visit_count")),
        _int(it.get("rating")), _int(it.get("campus")), _int(it.get("variant")),
        "是" if it.get("solved") else "否", it.get("e_tag") or "",
        len(imgs), json.dumps(imgs, ensure_ascii=False),
    )


def is_essence(it):
    """判断一条帖子是否属于「精华」。

    ⚠ 重要事实（源码 lake_notifier.dart:159-176 核实）：
    精华 tab 的请求是 `getPosts(type: 0, eTag: 'recommend')`，
    也就是说 **recommend 是列表接口的 etag 过滤器参数，不是帖子的属性**。
    服务端按它筛出精华帖返回，但返回体里这些帖子的 e_tag 未必回填 recommend。

    所以这里只在接口确实回填了相应字段时才生效——它是个「有就用」的补充，
    不能作为精华的来源。精华集合的正确做法是单独抓 etag=recommend 的列表
    （见 crawl_essence），把帖子 id 记进 ESSENCE_IDS。
    """
    etag = str(it.get("e_tag") or "").lower()
    if any(k in etag for k in ("recommend", "essence", "good", "digest")):
        return True
    for k in ("is_essence", "is_recommend", "essence", "recommend", "digest"):
        v = it.get(k)
        if v in (1, True, "1", "true", "True"):
            return True
    return False


def floor_tuple(f, post_id, parent_id=0):
    ui = f.get("user_info") or {}
    return (
        _int(f.get("id")), post_id, parent_id, _int(f.get("uid")),
        f.get("nickname") or "", _int(ui.get("level")), ui.get("avatar") or "",
        f.get("content") or "", f.get("created_at") or "",
        f.get("reply_to_name") or "", _int(f.get("like_count")),
        _int(f.get("value")), _int(f.get("sub_floor_cnt")),
        f.get("image_url") or "", 1 if f.get("is_owner") else 0,
    )


def iter_floors(floor_list, post_id, parent_id=0):
    """展平楼层与楼中楼：sub_floors 递归展开为独立行，保留父子关系。"""
    for f in floor_list or []:
        yield floor_tuple(f, post_id, parent_id)
        fid = _int(f.get("id"))
        for sub in iter_floors(f.get("sub_floors"), post_id, fid):
            yield sub


def save_posts(conn, items):
    """写入一页帖子（按 id 幂等）。

    ⚠ 必须用 UPSERT 而不是 INSERT OR REPLACE：
    REPLACE 的实现是「先删整行再插新行」，会把不在写入列表里的列
    ——正文已补/评论已抓/是我的帖子/是我的收藏/精华——全部重置为默认值。
    后果很隐蔽：抓「我的收藏」时会抹掉刚打好的「我的帖子」标记，
    补正文时会把已抓的评论状态清掉，等于白跑。
    UPSERT 只更新指定列，其余保持不动。
    """
    names = ",".join('"%s"' % n for n in POST_BASE_NAMES)
    ph = ",".join("?" * len(POST_BASE_NAMES))
    updates = ",".join('"%s"=excluded."%s"' % (n, n)
                       for n in POST_BASE_NAMES if n != "id")
    sql = ('INSERT INTO posts (%s) VALUES (%s) '
           'ON CONFLICT("id") DO UPDATE SET %s' % (names, ph, updates))
    with conn:
        conn.executemany(sql, [post_tuple(it) for it in items])


def save_floors(conn, tuples):
    conn.executemany(
        'INSERT OR REPLACE INTO floors (%s) VALUES (%s)'
        % (",".join('"%s"' % n for n, _ in FLOOR_COLUMNS),
           ",".join("?" * len(FLOOR_COLUMNS))), tuples)


def save_official_replies(conn, rows):
    conn.executemany(
        'INSERT OR REPLACE INTO official_replies (%s) VALUES (%s)'
        % (",".join('"%s"' % n for n, _ in OFFICIAL_COLUMNS),
           ",".join("?" * len(OFFICIAL_COLUMNS))), rows)


def official_row(f, post_id):
    ui = f.get("user_info") or {}
    return (_int(f.get("id")), post_id, _int(f.get("uid")),
            f.get("nickname") or "", ui.get("avatar") or "",
            f.get("content") or "", f.get("created_at") or "",
            _int(f.get("like_count")), f.get("image_url") or "")


def mark_posts_flag(conn, ids, column, value=1):
    """给帖子打集合/进度标记。column 必须是白名单列名。"""
    if column not in ("是我的帖子", "是我的收藏", "精华", "正文已补", "评论已抓",
                      "评论页数", "图片已存"):
        raise ValueError("不允许更新的列：" + column)
    if not ids:
        return 0
    ph = ",".join("?" * len(ids))
    with conn:
        cur = conn.execute('UPDATE posts SET "%s"=? WHERE id IN (%s)'
                           % (column, ph), tuple([value] + list(ids)))
    return cur.rowcount


def count_posts_flag(conn, column, value=1):
    return conn.execute('SELECT COUNT(*) FROM posts WHERE "%s"=?'
                        % column, (value,)).fetchone()[0]


def mark_essence_ids(conn, ids):
    """记录精华集合并按全集刷新 posts.精华。"""
    if not ids:
        return 0, 0
    with conn:
        conn.executemany(
            'INSERT OR IGNORE INTO essence_ids (post_id, marked_at, source) '
            'VALUES (?, ?, ?)',
            [(i, time.strftime("%Y-%m-%d %H:%M:%S"), "recommend") for i in ids])
        # 同步 posts 表的精华列。
        # 注意要按整个 essence_ids 集合刷新，而不是只刷本轮的 ids：
        # 精华集合是分批抓的，早先抓到的 id 对应的帖子可能后来才入库，
        # 只刷本轮会让 posts.精华 与 essence_ids 长期不一致（看板读数打架）。
        conn.execute('UPDATE posts SET "精华"=1 WHERE id IN '
                     '(SELECT post_id FROM essence_ids)')
    total = conn.execute("SELECT COUNT(*) FROM essence_ids").fetchone()[0]
    matched = conn.execute('SELECT COUNT(*) FROM posts WHERE "精华"=1').fetchone()[0]
    return total, matched


def ensure_type_row(conn, tid, name, page_size):
    conn.execute("INSERT OR IGNORE INTO scrape_progress "
                 "(type, name, page_size, status) VALUES (?,?,?,'pending')",
                 (tid, name, page_size))


def next_page(conn, tid):
    row = conn.execute('SELECT last_page, total_pages, status FROM '
                       'scrape_progress WHERE "type"=?', (tid,)).fetchone()
    if row is None:
        return 1, None
    last_page, total_pages, status = row
    if status == "done":
        return None, total_pages
    return (last_page or 0) + 1, total_pages


def mark_status(conn, tid, status, error=None):
    with conn:
        conn.execute('UPDATE scrape_progress SET status=?, last_error=?, '
                     'updated_at=? WHERE "type"=?',
                     (status, error, time.strftime("%Y-%m-%d %H:%M:%S"), tid))


def record_pagination_event(conn, tid, page, kind, detail):
    """记录一次分页异常，供事后核对数据完整性。"""
    try:
        with conn:
            conn.execute(
                'INSERT INTO pagination_events ("type", page, kind, detail, '
                'created_at) VALUES (?,?,?,?,?)',
                (tid, page, kind, detail[:300],
                 time.strftime("%Y-%m-%d %H:%M:%S")))
    except sqlite3.Error:
        pass


def update_scrape_page(conn, tid, page, total, total_pages, wrote_n):
    with conn:
        conn.execute(
            'UPDATE scrape_progress SET last_page=?, total=COALESCE(?,total)'
            ', total_pages=?, fetched=fetched+?, updated_at=?, status="running"'
            ' WHERE "type"=?',
            (page, total, total_pages, wrote_n,
             time.strftime("%Y-%m-%d %H:%M:%S"), tid))


def list_progress_rows(conn):
    return conn.execute(
        'SELECT "type",name,total_pages,last_page,'
        'fetched,status,last_error FROM scrape_progress '
        'ORDER BY "type"').fetchall()


def list_done_boards(conn):
    return conn.execute(
        'SELECT "type", name, last_page, fetched, status FROM scrape_progress '
        'WHERE status="done" ORDER BY "type"').fetchall()


def reopen_done_boards(conn):
    """把 done 板块标回 pending，返回本次打开的行数。"""
    with conn:
        cur = conn.execute('UPDATE scrape_progress SET status="pending" '
                           'WHERE status="done"')
    return cur.rowcount


def board_display_names(conn):
    """给看板/导出用：把库里出现过的板块ID 全部映射成名字。

    合并顺序：内置表 -> posttypes 快照(scrape_progress) -> 兜底「类型N」。
    """
    names = dict(DEFAULT_TYPE_NAMES)
    try:
        for tid, nm in conn.execute(
                'SELECT "type", name FROM scrape_progress'):
            if nm and _int(tid) not in names:
                names[_int(tid)] = nm
    except sqlite3.Error:
        pass
    return names


def max_post_id(conn):
    row = conn.execute("SELECT MAX(id) FROM posts").fetchone()
    return (row[0] or 0) if row else 0


def count_posts(conn):
    return conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]


def update_post_body(conn, pid, content, imgs, img_n, uid=None, nickname=None):
    """补全正文与图片；uid/昵称仅在接口返回非空时覆盖。"""
    with conn:
        conn.execute(
            'UPDATE posts SET "正文"=?, "图片链接"=?, "图片数"=?, '
            '"uid"=COALESCE(NULLIF(?,\'\'), "uid"), '
            '"作者"=COALESCE(NULLIF(?,\'\'), "作者"), '
            '"正文已补"=1 WHERE id=?',
            (content, json.dumps(imgs, ensure_ascii=False), img_n,
             _int(uid) or "",      # uid=0/缺失时保留库里已有值，避免被清成 0
             nickname or "", pid))


def mark_post_body_failed(conn, pid):
    with conn:
        conn.execute('UPDATE posts SET "正文已补"=-1 WHERE id=?', (pid,))


def mark_post_comment_status(conn, pid, status):
    """status: 1 成功 / -1 失败 / 0 待抓。"""
    with conn:
        conn.execute('UPDATE posts SET "评论已抓"=? WHERE id=?', (status, pid))


def reset_incomplete_comments(conn, types=None, ids=None, limit=0):
    """把「楼还没抓齐」的帖子标回未抓，让评论阶段补跑。

    两类不齐：
    1) 库内楼层数 < 帖子.评论数（整页/楼中楼漏抓）
    2) 某楼 子楼数 > 实际子楼行数（列表接口只内嵌前几条）

    作用域（避免小命令改全库状态）：
    - ids：只处理指定帖子
    - types：只处理指定板块
    - ids+types：交集
    - 无范围且 limit>0：跳过全局 repair（防止「先重置全库再只抓 N 条」）
    - 无范围且 limit==0：保持历史全局 repair
    只改 posts.评论已抓，不删 floors。
    """
    types = [int(t) for t in (types or [])]
    ids = [int(i) for i in (ids or [])]
    if not types and not ids and limit:
        log("补抓准备：本轮带 --limit 且未指定 --types/--ids，"
            "跳过全局评论 repair，避免先重置全库再只处理 %d 条" % limit)
        return 0

    scope_sql = ""
    scope_params = []
    if ids:
        scope_sql += ' AND id IN (%s)' % ",".join("?" * len(ids))
        scope_params.extend(ids)
    if types:
        scope_sql += ' AND "板块ID" IN (%s)' % ",".join("?" * len(types))
        scope_params.extend(types)
    scope_note = ""
    if scope_sql:
        scope_note = "（范围：%s%s）" % (
            ("ids=%s " % ",".join(str(i) for i in ids[:8])) if ids else "",
            ("types=%s" % ",".join(str(t) for t in types)) if types else "",
        )

    n_gap = conn.execute(
        'UPDATE posts SET "评论已抓"=0 WHERE "评论已抓"=1 AND "评论数">0 '
        'AND (SELECT COUNT(*) FROM floors f WHERE f."帖子ID"=posts.id) '
        '< "评论数"' + scope_sql, scope_params).rowcount
    n_sub = conn.execute(
        'UPDATE posts SET "评论已抓"=0 WHERE "评论已抓"=1 AND id IN ('
        '  SELECT DISTINCT f."帖子ID" FROM floors f '
        '  WHERE f."子楼数">0 AND f."子楼数">('
        '    SELECT COUNT(*) FROM floors c WHERE c."父楼ID"=f.id))' + scope_sql,
        scope_params).rowcount
    n = max(n_gap, n_sub)
    if n:
        conn.commit()
        log("补抓准备%s：楼层数不齐 %d 帖、子楼数不齐 %d 帖，已标回「评论已抓=0」"
            % (scope_note, n_gap, n_sub))
    else:
        log("补抓准备%s：未发现楼层数/子楼数不齐的帖子" % scope_note)
    return n


def collect_image_refs(conn):
    """从帖子与楼层里汇总所有图片引用。返回 [(source, oid, filename_or_url)]。"""
    out = []
    for pid, imgs in conn.execute('SELECT id, "图片链接" FROM posts '
                                  'WHERE "图片链接" IS NOT NULL').fetchall():
        try:
            v = json.loads(imgs) if imgs else []
            if not isinstance(v, list):
                v = []
        except (ValueError, TypeError):
            v = []
        for fn in v:
            out.append(("post", pid, fn))
    for fid, url in conn.execute('SELECT id, "图片" FROM floors '
                                 'WHERE "图片" IS NOT NULL AND "图片"<>\'\'').fetchall():
        out.append(("floor", fid, url))
    for pid, url in conn.execute('SELECT "帖子ID", "图片" FROM official_replies '
                                 "WHERE \"图片\" IS NOT NULL AND \"图片\"<>''").fetchall():
        out.append(("official", pid, url))
    return out


def image_done_names(conn):
    return {r[0] for r in conn.execute(
        'SELECT "文件名" FROM images WHERE "已有本地"=1').fetchall()}


def mark_image_ok(conn, base, src, oid, size):
    with conn:
        conn.execute(
            'INSERT OR REPLACE INTO images '
            '("文件名","来源","对象ID","已有本地","字节数","错误") '
            'VALUES (?,?,?,1,?,\'\')', (base, src, oid, size))


def mark_image_fail(conn, base, src, oid, err):
    with conn:
        conn.execute(
            'INSERT OR REPLACE INTO images '
            '("文件名","来源","对象ID","已有本地","字节数","错误") '
            'VALUES (?,?,?,0,0,?)', (base, src, oid, str(err)[:200]))


def status_summary(conn):
    """status --brief 与完整模式共用的计数。"""
    n_post = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    n_body = conn.execute('SELECT COUNT(*) FROM posts WHERE "正文已补"=1').fetchone()[0]
    n_cmt = conn.execute('SELECT COUNT(*) FROM posts WHERE "评论已抓"=1').fetchone()[0]
    n_floor = conn.execute("SELECT COUNT(*) FROM floors").fetchone()[0]
    n_off = conn.execute("SELECT COUNT(*) FROM official_replies").fetchone()[0]
    n_img = conn.execute('SELECT COUNT(*) FROM images WHERE "已有本地"=1').fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM scrape_progress WHERE status='done'").fetchone()[0]
    alln = conn.execute("SELECT COUNT(*) FROM scrape_progress").fetchone()[0]
    return {
        "posts": n_post, "body": n_body, "comments": n_cmt,
        "floors": n_floor, "official": n_off, "images": n_img,
        "boards_done": done, "boards_all": alln,
    }
