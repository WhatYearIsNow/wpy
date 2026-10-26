# -*- coding: utf-8 -*-
"""
看板的本地数据服务
==================
给「服务版看板」提供数据接口。之所以需要它：

  20 万帖 + 148 万条评论，若全部内嵌进单个 HTML，文件会超过 130 MB，
  浏览器直接打不开（不是慢，是内存爆掉）。而 file:// 下又无法分页取数据。

  于是改成：页面只留界面代码（几十 KB），数据在操作时向本地接口实时查询。
  SQLite 就在本机，查询通常在毫秒级，体验与内嵌版没有差别。

接口一览：
  GET /api/init                 板块列表、年份范围、总量等元数据
  GET /api/posts?<查询参数>      分页查询帖子（支持筛选/排序/搜索）
  GET /api/post?id=<帖子id>      单帖详情（含正文、图片、全部评论）
  GET /api/user?uid=<用户id>     某个用户的帖子与统计
"""

import json
import re
import sqlite3
import threading
import urllib.parse

# 排序白名单：只允许这些字段排序，避免 SQL 注入
SORT_MAP = {
    "new": '"id" DESC',
    "old": '"id" ASC',
    "vs": '"浏览量" DESC',
    "lk": '"点赞数" DESC',
    "cm": '"评论数" DESC',
    "fv": '"收藏数" DESC',
    "rel": None,  # 相关度，由搜索单独处理
}

# 板块「其他」包含的 id
OTHER_TYPES = [5, 6, 7, 8, 10]

# 板块名映射。接口没返回名时用它兜底，
# 否则前端会渲染出一个没有文字的空色块（看起来像一道横线）。
TYPE_NAMES = {
    0: "精华",
    1: "校务专区",
    2: "青年湖底",
    3: "学习新知",
    4: "交友恋爱",
    5: "二次元",
    6: "摆摊",
    7: "运动健身",
    8: "数码科技",
    9: "求职就业",
    10: "Bugs反馈",
    11: "闲置交易",
    12: "校园活动",
    13: "美食专区",
}


class BoardDB:
    """只读的看板数据访问层。所有查询都走这里。"""

    def __init__(self, db_path, pic_base="", use_proxy=True):
        self.db_path = db_path
        self.pic_base = pic_base  # 图片 CDN 前缀
        self.use_proxy = use_proxy  # True 时图片走 /img/ 代理
        # ThreadingTCPServer 多线程共用一个连接：不加锁会互相踩，
        # 表现为 /api/user 与 /api/posts 互相卡住（用户卡「统计中」、列表无响应）。
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self._ensure_indexes()
        try:
            # 之后只读；建索引必须在设 query_only 之前
            self.conn.execute("PRAGMA query_only=ON")
        except sqlite3.Error:
            pass

    def _ensure_indexes(self):
        """按 uid 查帖/评论是最热路径之一，没有索引会扫全表。"""
        stmts = [
            'CREATE INDEX IF NOT EXISTS idx_posts_uid ON posts("uid")',
            'CREATE INDEX IF NOT EXISTS idx_floors_uid ON floors("用户ID")',
        ]
        with self._lock:
            for sql in stmts:
                try:
                    self.conn.execute(sql)
                except sqlite3.Error:
                    pass

    def execute(self, sql, args=()):
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def execute_one(self, sql, args=()):
        with self._lock:
            return self.conn.execute(sql, args).fetchone()

    # ---------- 元数据 ----------
    def init_info(self):
        with self._lock:
            total = self.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            floors = self.conn.execute("SELECT COUNT(*) FROM floors").fetchone()[0]
            boards = []
            for r in self.conn.execute(
                'SELECT "板块ID" t, COUNT(*) n FROM posts GROUP BY "板块ID" ORDER BY n DESC'
            ):
                boards.append(
                    {"id": r["t"], "n": r["n"], "name": TYPE_NAMES.get(r["t"] or 0, "其他")}
                )
            yr = self.conn.execute(
                'SELECT MIN(substr("发布时间",1,4)) a, MAX(substr("发布时间",1,4)) b FROM posts'
            ).fetchone()
            years = []
            for r in self.conn.execute(
                'SELECT substr("发布时间",1,4) y, COUNT(*) n FROM posts GROUP BY y ORDER BY y'
            ):
                if r["y"]:
                    years.append({"y": r["y"], "n": r["n"]})
        return {
            "total": total,
            "floors": floors,
            "boards": boards,
            "yearFrom": yr["a"] or "",
            "yearTo": yr["b"] or "",
            "years": years,
        }

    # ---------- 图片地址 ----------
    # 列表/头像一律 thumb（实测 origin 321KB / thumb 17KB），
    # 详情大图由前端在缩放/导出时再换成 origin。
    def img(self, fname, size="thumb"):
        if not fname:
            return ""
        base = str(fname).split("#")[0]
        if not base:
            return ""
        if size not in ("thumb", "origin"):
            size = "thumb"
        if self.use_proxy:
            return "/img/%s/%s" % (size, _quote(base))
        return self.pic_base + size + "/" + _quote(base)

    def full_img(self, fname):
        """origin 地址：给导出/放大用，前端也会自行把 thumb 换成 origin。"""
        return self.img(fname, "origin") if fname else ""

    def _post_row(self, r, with_content=True):
        imgs = _json_list(r["图片链接"])
        d = {
            "id": r["id"],
            "uid": r["uid"] or 0,
            "t": r["标题"] or "",
            "d": (r["发布时间"] or "")[:19].replace("T", " "),
            "dd": (r["发布时间"] or "")[:10],
            "a": r["作者"] or "匿名",
            "av": self.img(r["头像"], "thumb") if r["头像"] else "",
            "lv": r["等级"] or 0,
            "ty": r["板块ID"] or 0,
            "tn": TYPE_NAMES.get(r["板块ID"] or 0, ""),  # 板块名，前端标签用
            "tag": r["标签"] or "",
            "lk": r["点赞数"] or 0,
            "fv": r["收藏数"] or 0,
            "cm": r["评论数"] or 0,
            "vs": r["浏览量"] or 0,
            "st": r["是否解决"] or "",
            "etag": r["置顶标记"] or "",
            "ess": 1 if r["精华"] else 0,
            "mine": 1 if r["是我的帖子"] else 0,
            "fav": 1 if r["是我的收藏"] else 0,
            "gone": 1 if (r["正文已补"] or 0) == -1 else 0,
            "got": 1 if (r["评论已抓"] or 0) == 1 else 0,
            # 列表缩略图最多 3 张
            "imgs": [self.img(u, "thumb") for u in imgs[:3]],
            "nc": 0,  # 列表不返回楼层，详情里再给
            "floors": [],
            "floorsTotal": 0,
            "floorsLoaded": 0,
            "offs": [],
        }
        if with_content:
            d["c"] = r["正文"] or ""
        else:
            # 列表只给摘要，省带宽
            c = r["正文"] or ""
            d["c"] = c if len(c) <= 200 else c[:200] + "…"
        return d

    # ---------- 通用筛选构造 ----------
    def _where(self, q):
        """把查询参数转成 WHERE 子句与参数列表。"""
        cond, args = [], []

        tab = q.get("tab", "all")
        if tab == "ess":
            cond.append('"精华"=1')
        elif tab == "other":
            cond.append('"板块ID" IN (%s)' % ",".join(map(str, OTHER_TYPES)))
        elif tab and tab != "all":
            if tab.isdigit():
                cond.append('"板块ID"=?')
                args.append(int(tab))

        if q.get("mine") == "1":
            cond.append('"是我的帖子"=1')
        if q.get("fav") == "1":
            cond.append('"是我的收藏"=1')
        if q.get("unc") == "1":
            # 评论数为 0 的帖子抓取阶段本就不处理（没评论可抓），不算「未抓」
            cond.append('"评论已抓"<>1 AND "评论数">0')
        if q.get("gone") == "1":
            cond.append('"正文已补"=-1')

        if str(q.get("uid") or "").strip().isdigit():
            cond.append('"uid"=?')
            args.append(int(q["uid"]))
        if q.get("d1"):
            cond.append('substr("发布时间",1,10)>=?')
            args.append(q["d1"])
        if q.get("d2"):
            cond.append('substr("发布时间",1,10)<=?')
            args.append(q["d2"])
        if q.get("y"):  # 按年份
            cond.append('substr("发布时间",1,4)=?')
            args.append(q["y"])

        # 关注列表：前端传 uid 集合（管理员存服务器、游客存本地，都这样传）
        if q.get("followUids") is not None:
            ids = [int(x.strip()) for x in q["followUids"].split(",") if x.strip().isdigit()]
            if ids:
                cond.append('"uid" IN (%s)' % ",".join("?" * min(len(ids), 1000)))
                args.extend(ids[:1000])
            else:
                cond.append("0")  # 关注列表为空 -> 无结果
        # 游客屏蔽名单：前端（服务端注入）传要屏蔽的作者 uid 集合
        if q.get("blockUids"):
            ids = [int(x.strip()) for x in q["blockUids"].split(",") if x.strip().isdigit()]
            if ids:
                cond.append('"uid" NOT IN (%s)' % ",".join("?" * min(len(ids), 1000)))
                args.extend(ids[:1000])
            else:
                cond.append("0")  # 游客屏蔽名单为空 -> 无结果
        # 游客本地收藏：前端传帖子 id 集合（存浏览器 localStorage）
        if q.get("favIds") is not None:
            ids = [int(x.strip()) for x in q["favIds"].split(",") if x.strip().isdigit()]
            if ids:
                cond.append('"id" IN (%s)' % ",".join("?" * min(len(ids), 1000)))
                args.extend(ids[:1000])
            else:
                cond.append("0")  # 本地收藏为空 -> 无结果
        return (" AND ".join(cond) if cond else "1"), args

    # ---------- 帖子查询 ----------
    def posts(self, q):
        where, args = self._where(q)
        keyword = (q.get("q") or "").strip()
        order = SORT_MAP.get(q.get("so", "new"), '"id" DESC')
        page = max(1, int(q.get("page", 1) or 1))
        size = min(200, max(1, int(q.get("size", 20) or 20)))

        if keyword:
            # 搜索词解析：除普通关键词外，还认三类「定点查询」——
            #   MP号   #MP123456 / 整词 MP123456 / App 分享链接 wpy://school_project/123456
            #          （从 App 复制的整段分享文字也能认出其中的 MP号，直接定位该帖）
            #   MPID   MPID12345 / MPID:12345 / uid 12345 -> 精确命中该用户的帖子
            #   纯数字 123456 -> 帖子 id / MPID 精确命中，正文模糊匹配兜底
            pids, uids = [], []
            for m in re.finditer(r"#MP(\d+)|school_project/(\d+)", keyword, re.IGNORECASE):
                pids.append(int(m.group(1) or m.group(2)))
            rest = re.sub(r"#MP\d+|school_project/\d+", " ", keyword, flags=re.IGNORECASE)
            # 不带 # 的整词 MP123 也当 MP号；嵌在长文本里时不算，避免误伤普通词
            m = re.fullmatch(r"MP(\d+)", rest.strip(), re.IGNORECASE)
            if m:
                pids.append(int(m.group(1)))
            if pids:
                # MP号本身就是「找这个帖子」的完整意图，其余文字（分享链接
                # 前缀、分享文案之类）不再参与匹配，否则 AND 会把结果清空
                rest = " "
            else:
                for m in re.finditer(r"(?:MPID|UID)[:：]?\s*(\d+)", rest, re.IGNORECASE):
                    uids.append(int(m.group(1)))
                rest = re.sub(r"(?:MPID|UID)[:：]?\s*\d+", " ", rest, flags=re.IGNORECASE)
                rest = rest.strip()

            # 普通关键词：空格分隔取 AND（都要命中），引号内的短语按整串匹配。
            # 「选课 建议」-> 两个词都出现的帖子；「"选课建议"」-> 精确短语。
            terms = []
            for m in re.finditer(r'"([^"]+)"', rest):
                terms.append(("phrase", m.group(1).strip()))
            rest = re.sub(r'"[^"]+"', " ", rest)
            for w in rest.split():
                w = w.strip()
                if not w:
                    continue
                if w.isdigit():
                    terms.append(("num", w))
                else:
                    terms.append(("word", w))
            if not terms and not pids and not uids:
                terms = [("word", keyword)]

            subs, largs = [], []
            if pids:
                subs.append('"id" IN (%s)' % ",".join("?" * len(pids)))
                largs += pids
            if uids:
                subs.append('"uid" IN (%s)' % ",".join("?" * len(uids)))
                largs += uids
            for kind, val in terms:
                v = val.replace("%", "").replace("_", "")
                if kind == "num":
                    subs.append(
                        '("标题" LIKE ? OR "正文" LIKE ? OR "作者" LIKE ? '
                        'OR "标签" LIKE ? OR "id"=? OR "uid"=?)'
                    )
                    largs += ["%" + v + "%"] * 4 + [int(val)] * 2
                elif kind == "phrase":
                    subs.append('("标题" LIKE ? OR "正文" LIKE ? OR "作者" LIKE ?)')
                    largs += ["%" + v + "%"] * 3
                else:
                    # 单个关键词要能匹配标题/正文/作者/标签
                    subs.append(
                        '("标题" LIKE ? OR "正文" LIKE ? OR "作者" LIKE ? OR "标签" LIKE ?)'
                    )
                    largs += ["%" + v + "%"] * 4

            like = "(" + " AND ".join(subs) + ")"
            cond = where + " AND " + like
            largs = args + largs
            count_sql = "SELECT COUNT(*) FROM posts WHERE " + cond
            total = self.execute_one(count_sql, largs)[0]
            # 排序：id / MPID 精确命中最优先，其次标题命中，之后尊重所选排序。
            # 以前这里把排序写死成相关度，导致「最早发布」在搜索时被忽略
            # （用户选了最早，结果仍按相关度给出最近几年的帖子）。
            when, rargs = [], []
            if pids:
                when.append('WHEN "id" IN (%s) THEN 0' % ",".join("?" * len(pids)))
                rargs += pids
            if uids:
                when.append('WHEN "uid" IN (%s) THEN %d' % (",".join("?" * len(uids)), len(when)))
                rargs += uids
            if terms:
                # 纯数字词的 id/uid 精确命中也要排在正文模糊命中前面
                for kind, val in terms:
                    if kind == "num":
                        when.append('WHEN ("id"=? OR "uid"=?) THEN %d' % len(when))
                        rargs += [int(val)] * 2
                first = terms[0][1].replace("%", "").replace("_", "")
                when.append('WHEN "标题" LIKE ? THEN %d' % len(when))
                rargs.append("%" + first + "%")
            rel = "CASE %s ELSE %d END" % (" ".join(when), len(when))
            secondary = order or '"id" DESC'
            order_sql = rel + ", " + secondary
            sql = "SELECT * FROM posts WHERE %s ORDER BY %s LIMIT ? OFFSET ?" % (cond, order_sql)
            rows = self.execute(sql, largs + rargs + [size, (page - 1) * size])
        else:
            count_sql = "SELECT COUNT(*) FROM posts WHERE " + where
            total = self.execute_one(count_sql, args)[0]
            sql = "SELECT * FROM posts WHERE %s ORDER BY %s LIMIT ? OFFSET ?" % (
                where,
                order or '"id" DESC',
            )
            rows = self.execute(sql, args + [size, (page - 1) * size])

        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [self._post_row(r, with_content=False) for r in rows],
            "keyword": keyword,
        }

    # ---------- 单帖详情 ----------
    # 楼层分页：热帖实测最多 3578 楼，一次全量返回会把 DOM 和内存一起打爆。
    # foff=已加载条数，flim=本页条数；nc 始终是「库里该帖楼层总数」。
    def post_detail(self, pid, floor_limit=150, floor_offset=0):
        r = self.execute_one("SELECT * FROM posts WHERE id=?", (pid,))
        if not r:
            return None
        d = self._post_row(r, with_content=True)
        floor_limit = max(1, min(500, int(floor_limit or 150)))
        floor_offset = max(0, int(floor_offset or 0))
        total = self.execute_one('SELECT COUNT(*) FROM floors WHERE "帖子ID"=?', (pid,))[0]
        floors = self.execute(
            'SELECT * FROM floors WHERE "帖子ID"=? ORDER BY "父楼ID", id LIMIT ? OFFSET ?',
            (pid, floor_limit, floor_offset),
        )
        d["floors"] = [self._floor_row(f) for f in floors]
        d["floorsTotal"] = total
        d["floorsLoaded"] = floor_offset + len(d["floors"])
        d["nc"] = total
        offs = self.execute('SELECT * FROM official_replies WHERE "帖子ID"=? ORDER BY id', (pid,))
        d["offs"] = [
            {
                "n": o["昵称"] or "官方",
                "c": o["内容"] or "",
                "a": self.img(o["头像"], "thumb") if o["头像"] else "",
                "t": (o["发布时间"] or "")[:19].replace("T", " "),
                "lk": o["点赞数"] or 0,
                "img": self.img(o["图片"], "thumb") if o["图片"] else "",
            }
            for o in offs
        ]
        return d

    def _floor_row(self, f):
        return {
            "id": f["id"],
            "pid": f["父楼ID"] or 0,
            "uid": f["用户ID"] or 0,
            "n": f["昵称"] or "匿名",
            "lv": f["等级"] or 0,
            "a": self.img(f["头像"], "thumb") if f["头像"] else "",
            "c": f["内容"] or "",
            "t": (f["发布时间"] or "")[:19].replace("T", " "),
            "r": f["回复对象"] or "",
            "lk": f["点赞数"] or 0,
            "d": f["点踩数"] or 0,
            "own": f["是楼主"] or 0,
            "img": self.img(f["图片"], "thumb") if f["图片"] else "",
        }

    # ---------- 用户 ----------
    def user(self, uid):
        # 聚合放 SQL：以前 SELECT * 全部发帖再 Python 求和，发帖多的号会拖慢
        row = self.execute_one(
            'SELECT COUNT(*) n, COALESCE(SUM("点赞数"),0) lk, '
            'COALESCE(SUM("评论数"),0) cm, '
            'MAX("作者") nm, MAX("头像") av, MAX("等级") lv '
            'FROM posts WHERE "uid"=?',
            (uid,),
        )
        n = row["n"] if row else 0
        name = (row["nm"] if row and row["nm"] else "") or ""
        av = self.img(row["av"], "thumb") if row and row["av"] else ""
        lv = int(row["lv"] or 0) if row else 0
        if not n:
            f = self.execute_one(
                'SELECT "昵称" n, "头像" a, "等级" lv FROM floors WHERE "用户ID"=? LIMIT 1', (uid,)
            )
            if f:
                name = f["n"] or ""
                av = self.img(f["a"], "thumb") if f["a"] else ""
                lv = f["lv"] or 0
        # 该用户评论过的帖子数（走 idx_floors_uid）
        cf_row = self.execute_one(
            'SELECT COUNT(DISTINCT "帖子ID") c FROM floors WHERE "用户ID"=?', (uid,)
        )
        cf = cf_row["c"] if cf_row else 0
        return {
            "uid": uid,
            "name": name,
            "av": av,
            "lv": lv,
            "posts": n,
            "likes": int(row["lk"] or 0) if row else 0,
            "comments": int(row["cm"] or 0) if row else 0,
            "commentedOn": cf,
        }

    # ---------- 批量取用户信息 ----------
    def users(self, uids):
        """一次拿多个用户的信息（昵称/头像/等级）。

        关注列表在浏览器里，打开页面时要按 uid 补齐头像，逐个请求太慢，
        所以做成一次批量查询。优先从帖子表取，没有的再从楼层表取
        （很多人只评论不发帖）。"""
        out = {}
        uids = [u for u in uids if u]
        if not uids:
            return out
        ph = ",".join("?" * len(uids))
        for r in self.execute(
            'SELECT "uid" u, "作者" n, "头像" a, "等级" lv, COUNT(*) c '
            'FROM posts WHERE "uid" IN (%s) GROUP BY "uid"' % ph,
            uids,
        ):
            out[r["u"]] = {
                "uid": r["u"],
                "name": r["n"] or "",
                "av": self.img(r["a"], "thumb") if r["a"] else "",
                "lv": r["lv"] or 0,
                "posts": r["c"],
            }
        # 帖子表里没有的（只评论过的用户）从楼层补
        rest = [u for u in uids if u not in out]
        if rest:
            ph2 = ",".join("?" * len(rest))
            for r in self.execute(
                'SELECT "用户ID" u, "昵称" n, "头像" a, "等级" lv '
                'FROM floors WHERE "用户ID" IN (%s) LIMIT %d' % (ph2, len(rest)),
                rest,
            ):
                if r["u"] not in out:
                    out[r["u"]] = {
                        "uid": r["u"],
                        "name": r["n"] or "",
                        "av": self.img(r["a"], "thumb") if r["a"] else "",
                        "lv": r["lv"] or 0,
                        "posts": 0,
                    }
        return out

    # ---------- 用户列表（按发言量） ----------
    def top_users(self, limit=200):
        rows = self.execute(
            'SELECT "uid" u, COUNT(*) n, "作者" a, "头像" av, "等级" lv '
            'FROM posts GROUP BY "uid" ORDER BY n DESC LIMIT ?',
            (limit,),
        )
        return [
            {
                "uid": r["u"],
                "n": r["n"],
                "name": r["a"] or "",
                "av": self.img(r["av"], "thumb") if r["av"] else "",
                "lv": r["lv"] or 0,
            }
            for r in rows
        ]


def _quote(s):
    out = []
    for ch in s:
        if ch.isalnum() or ch in "._-~/":
            out.append(ch)
        else:
            out.append("%%%02X" % ord(ch))
    return "".join(out)


def _json_list(s):
    if not s:
        return []
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []
