# -*- coding: utf-8 -*-
"""阶段共用：板块名、过滤、确认计划、解析与 worker 池。"""
from __future__ import annotations

import re
import threading

from ..config import COMMENT_PAGE_SIZE, DEFAULT_TYPE_NAMES, RECOMMEND_TYPE
from ..db import _int
from ..http import LIMITER, api_get, FuseTripped
from ..runlog import log

def map_type_names(token):
    """type id -> 板块名。

    以接口 posttypes 为准，并用内置表补全接口没返回的板块，
    否则看板会把 5/6/10/13 这类显示成「类型N」。
    """
    names = dict(DEFAULT_TYPE_NAMES)
    try:
        data = api_get(token, "posttypes", allow_null_data=True)
        got = 0
        for x in (data or {}).get("list") or []:
            tid = _int(x.get("id"))
            nm = (x.get("name") or "").strip()
            if nm:
                names[tid] = nm
                got += 1
        if got:
            log("板块名：接口返回 %d 个，合并内置表共 %d 个" % (got, len(names)))
            return names
    except Exception as e:
        log("posttypes 获取失败，使用内置板块映射：" + str(e)[:120])
    return names


def crawlable_types(type_names):
    """要遍历的板块 ID 列表，排除「精华」（它是推荐位，不是板块）。

    踩坑记录：以前把 0 也当成板块抓，导致推荐位的帖子混进库里——
    推荐位返回的帖子服务端会按真实板块归属，所以既重复又无法用板块筛选。
    """
    return sorted(t for t in type_names if t != RECOMMEND_TYPE)


def only_recommend(type_names):
    """用户是不是只选了「精华」（0）这一个伪板块。

    用于给出明确提示：精华不在板块列表里抓，而是第 2 步单独抓集合。
    以前这里不提示，用户选了 0 就静默什么都不抓，看起来像卡住。
    """
    keys = list(type_names)
    return bool(keys) and all(t == RECOMMEND_TYPE for t in keys)


def warn_if_only_recommend(type_names, where=""):
    """只选了精华时给出人话提示；返回 True 表示确实只有精华。"""
    if only_recommend(type_names):
        log("提示：你只选了「精华(0)」。精华不是板块，不能在板块列表里遍历；"
            "它会在「%s」这一步单独抓成精华集合。"
            % (where or "抓取精华集合"))
        return True
    return False


def warn_unknown_types(requested, type_names, where=""):
    """校验 --types 是否在当前可用板块映射内；返回无法识别的 ID 列表。"""
    if not requested:
        return []
    requested = [int(t) for t in requested]
    unknown = [t for t in requested if t not in type_names]
    if not unknown:
        return []
    avail = "、".join("%s=%s" % (k, type_names[k]) for k in sorted(type_names))
    log("警告：%s无法识别的板块 ID：%s"
        % (("%s：" % where) if where else "",
           ",".join(str(t) for t in unknown)))
    log("当前可用板块：%s" % (avail or "（空）"))
    return unknown


def usable_types(requested, type_names):
    """返回 requested 中存在于 type_names 的 ID；空 requested 表示不限。"""
    if not requested:
        return list(type_names)
    return [int(t) for t in requested if int(t) in type_names]


def estimate_work(conn, types, since_date, limit):
    """估算这次要抓多少，并按当前 QPS 给出预计耗时。

    返回 (待抓帖数, 待抓评论帖数, 预计请求数, 预计秒数)。只读数据库，不发请求。
    """
    where, params = ['"正文已补"=0'], []
    if types:
        where.append('"板块ID" IN (%s)' % ",".join("?" * len(types)))
        params += list(types)
    n_body = conn.execute('SELECT COUNT(*) FROM posts WHERE %s'
                          % " AND ".join(where), params).fetchone()[0]

    where2, params2 = ['"评论已抓"=0 AND "正文已补">=0'], []
    if types:
        where2.append('"板块ID" IN (%s)' % ",".join("?" * len(types)))
        params2 += list(types)
    rows = conn.execute('SELECT "评论数" FROM posts WHERE %s'
                        % " AND ".join(where2), params2).fetchall()
    n_cmt = len(rows)
    # 评论每页 COMMENT_PAGE_SIZE 楼，至少 1 页
    cmt_pages = sum(
        max(1, (_int(r[0]) + COMMENT_PAGE_SIZE - 1) // COMMENT_PAGE_SIZE)
        for r in rows)
    if limit:
        n_body = min(n_body, limit)
        n_cmt = min(n_cmt, limit)
        cmt_pages = min(cmt_pages, limit)

    reqs = n_body + cmt_pages + 30      # 预留列表/精华/我的帖子等固定开销
    qps = LIMITER.target if LIMITER.target > 0 else 1.0
    return n_body, n_cmt, reqs, reqs / qps


def _fmt_dur(secs):
    secs = int(secs)
    if secs < 60:
        return "约 %d 秒" % secs
    if secs < 3600:
        return "约 %d 分 %d 秒" % (secs // 60, secs % 60)
    return "约 %d 小时 %d 分" % (secs // 3600, (secs % 3600) // 60)


def confirm_plan(conn, types, type_names, since_date, limit, auto_yes=False):
    """打印「这次要抓什么、大概多久」，等用户确认后开始。

    auto_yes=True（非交互环境）时跳过等待。
    """
    n_body, n_cmt, reqs, secs = estimate_work(conn, types, since_date, limit)
    names = ("、".join(type_names[t] for t in sorted(types))
             if types else "全部板块")
    pages = max(0, reqs - n_body - 30)
    print()
    print("=" * 60)
    print("  本次抓取计划")
    print("=" * 60)
    print("  范围板块：%s" % names)
    if since_date:
        print("  日期下限：%s 及以后" % since_date)
    if limit:
        print("  条数上限：%d" % limit)
    print("  ----------------------------------------")
    print("  待补正文：%d 帖" % n_body)
    print("  待抓评论：%d 帖（约 %d 页）" % (n_cmt, pages))
    print("  预计请求：约 %d 次" % reqs)
    print("  抓取速率：%.0f QPS" % (LIMITER.target if LIMITER.target > 0 else 1))
    print("  预计耗时：%s" % _fmt_dur(secs))
    print("=" * 60)
    if auto_yes:
        print("  （非交互模式，自动开始）")
        return True
    try:
        ans = input("  回车开始，输入 n 取消：").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans not in ("n", "no", "q")


def _parse_board_ids(s):
    """解析板块编号（只接受数字，不接受 #MP 之类）。

    与 _parse_ids 分开是有原因的：板块编号和帖子 id 是两回事，
    复用同一个解析器会让 `--types "#MP5"` 被当成「板块 5」（不存在），
    用户看到的是「什么都没抓」，很难查。
    """
    if not s:
        return []
    if isinstance(s, (list, tuple, set)):
        return [int(x) for x in s if str(x).strip().isdigit()]
    out = []
    for part in str(s).replace("，", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def build_post_filter(args, stage):
    """把 types / ids / since_date 组合成一个 WHERE 片段。

    以前每个阶段各自手写 WHERE，导致两个问题：
      1. --ids 会直接覆盖 --types（用户说了"只要板块13"却抓了别的板块）
      2. 各阶段漏掉 since_date，选了日期范围却不生效
    这里统一构造，保证三种条件永远取交集，且各阶段行为一致。

    stage: "body" 看正文进度，"comment" 看评论进度
    """
    if stage == "comment":
        # 跳过评论数为 0 的帖子：服务端已告知没有评论，无需再请求。
        # 实测 20 万帖中约 7.9 万帖属于这种，能省掉近四成请求。
        # 若想强制全部逐帖确认，可用 --no-comment-filter 关闭此优化。
        cond = ['"评论已抓"=0', '"正文已补">=0']
        if not getattr(args, "no_comment_filter", False):
            cond.append('"评论数">0')
    else:
        cond = ['"正文已补"=0']

    types = getattr(args, "types", None)
    ids = getattr(args, "ids", None)
    # ids 与 types 取交集：两边都限制时，只处理同时满足的
    if ids:
        cond.append('id IN (%s)' % ",".join(str(int(i)) for i in ids))
    if types:
        cond.append('"板块ID" IN (%s)' % ",".join(str(int(t)) for t in types))
    sd = getattr(args, "since_date", "")
    if sd:
        cond.append('substr("发布时间",1,10) >= ?')
    return " AND ".join(cond), ([sd] if sd else [])


def _parse_since_date(s):
    """把 2026-09-01 这类日期转成可比较的 ISO 前缀，失败返回 ''。"""
    if not s:
        return ""
    s = s.strip()
    import re
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        log("日期格式不对（应为 2026-09-01 这样），忽略该条件：" + s)
        return ""
    return s


def run_workers(fn, n):
    """并发执行 fn() n 次。

    改进: 收集线程异常并传播。如果任何 worker 抛出非 FuseTripped 异常，
    记录到 _worker_errors 列表，所有线程 join 后重新抛出第一个异常。
    这解决了以前 worker 异常静默消失的问题。
    """
    if n <= 1:
        fn()
        return

    # 线程安全的异常收集器
    _worker_errors = []
    _errors_mu = threading.Lock()

    def _safe_fn():
        try:
            fn()
        except Exception as e:
            # FuseTripped 是正常退出信号，不算错误
            if isinstance(e, FuseTripped):
                return
            with _errors_mu:
                _worker_errors.append(e)

    threads = [threading.Thread(target=_safe_fn, daemon=True) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 传播第一个异常（如果有）
    if _worker_errors:
        raise _worker_errors[0]


def _parse_ids(s):
    """解析帖子 id 列表；同时兼容 App 分享口令。

    支持形式：#MP123456 / wpy://school_project/123456 / 纯数字，
    也支持整段分享文本（会自动把里面的 id 抠出来），以及以上混用。
    """
    if not s:
        return []
    import re
    ids = []
    # 先取出所有口令形式的 id，并把这些片段从文本里移除
    rest = s
    for m in re.finditer(r"#MP(\d+)|school_project/(\d+)", s):
        v = m.group(1) or m.group(2)
        if v:
            ids.append(int(v))
        rest = rest.replace(m.group(0), " ")
    # 剩下的按逗号/空白切分，裸数字也算（兼容 "123,456" 或 "123 456"）
    for part in re.split(r"[,，\s]+", rest):
        if part.isdigit():
            ids.append(int(part))
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out

