# -*- coding: utf-8 -*-
"""运行状态与抓取进度展示。"""
from __future__ import annotations

import os

from ..config import DB_PATH, RUN_STATE_PATH
from ..db import _int, list_progress_rows, open_db, status_summary
from ..runlog import log

def read_run_state():
    """读运行状态文件；返回 (state, updated, detail, pid, alive)。"""
    if not os.path.exists(RUN_STATE_PATH):
        return None
    try:
        txt = open(RUN_STATE_PATH, encoding="utf-8").read()
    except OSError:
        return None
    info = {}
    for line in txt.splitlines():
        if "：" in line:
            k, v = line.split("：", 1)
            info[k.strip()] = v.strip()
    pid = _int(info.get("进程号"))
    alive = False
    if pid:
        try:
            os.kill(pid, 0)      # 仅探测进程是否存在
            alive = True
        except (OSError, ProcessLookupError, PermissionError):
            alive = False
    return (info.get("状态"), info.get("更新时间"), info.get("当前步骤"), pid, alive)


def do_status(brief=False):
    if not os.path.exists(DB_PATH):
        if brief:
            print("尚未开始抓取（还没有 sitewide.db）")
        else:
            print("还没有抓取记录。先运行 probe 看规模，再运行 list 抓数据。")
        return
    conn = open_db()
    if brief:
        s = status_summary(conn)
        print("已抓 %d 帖（正文 %d）｜评论 %d 帖 / %d 楼｜板块 %d/%d 完成"
              % (s["posts"], s["body"], s["comments"], s["floors"],
                 s["boards_done"], s["boards_all"]))
        conn.close()
        return

    # 完整模式：先说清楚「现在到底在不在跑」
    rs = read_run_state()
    print("=" * 60)
    if rs is None:
        print("运行状态：没有记录（可能从未运行过）")
    else:
        state, updated, detail, pid, alive = rs
        if alive and state and state.startswith("运行中"):
            print("运行状态：★ 正在运行 —— %s" % detail)
            print("           进程号 %d，状态更新时间 %s" % (pid, updated))
            print("           如果这个时间很久没变，说明卡在网络等待上。")
        elif state and state.startswith("运行中"):
            print("运行状态：已中断（进程 %s 不在了）" % pid)
            print("           最后记录：%s，时间 %s" % (detail, updated))
            print("           直接重新运行即可从断点继续。")
        else:
            print("运行状态：%s（时间 %s）" % (state, updated))
    print("=" * 60)
    print()

    print("%-5s %-12s %9s %9s %9s  %s"
          % ("type", "板块", "总页数", "已抓页", "已抓条", "状态"))
    print("-" * 68)
    for r in conn.execute('SELECT "type",name,total_pages,last_page,'
                          'fetched,status,last_error FROM scrape_progress '
                          'ORDER BY "type"'):
        tid, name, tp, lp, ft, st, err = r
        print("%-5s %-12s %9s %9s %9s  %s%s"
              % (tid, name, tp if tp else "?", lp, ft, st or "-",
                 ("  最近错误：" + str(err)[:60]) if err and st == "error" else ""))
    print("-" * 68)
    print("注：总页数来自服务端 total 字段（实测为页数），? 表示尚未探测到。")
    s = status_summary(conn)
    print("帖子 %d 条｜正文已补 %d｜评论已抓 %d 帖 / %d 楼（官方回复 %d）｜图片已存 %d 张"
          % (s["posts"], s["body"], s["comments"], s["floors"], s["official"], s["images"]))
    conn.close()

