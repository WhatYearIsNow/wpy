#!/usr/bin/env python3
"""刷新旧帖的动态数据（点赞/浏览/收藏/评论数），评论增长则重抓评论。

用法：
  python3 refresh_stats.py [--days N] [--all] [--limit N] [--dry-run] [--rebuild] [--qps N]

  --all     刷全部帖子（默认 36500 天）
  --days N  只刷最近 N 天
  --rebuild 刷完重建看板

环境变量:
  自动加载 deploy/env.update 中的配置（学号密码等）。
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path

# 加载 wpy 凭据环境
ROOT = Path(__file__).resolve().parent.parent
_env_file = ROOT / "deploy" / "env.update"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# 确保 wpy 包可导入（scripts/ 在 wpy-sitewide 下，wpy/ 是子包）
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wpy.auth import get_token  # noqa: E402
from wpy.db import open_db  # noqa: E402
from wpy.http import api_get, Fuse, FuseTripped, with_retry, FatalError, pace  # noqa: E402
from wpy.runlog import log  # noqa: E402
from wpy.crawler.comment import crawl_one_post  # noqa: E402


class FakeArgs:
    """crawl_one_post 需要的参数对象。"""
    page_size = 20
    order = 0
    official = True
    interval = 0.1
    concurrency = 1


def refresh_one(conn, token, pid, fuse, lock, dry=False):
    """刷新单个帖统计字段；评论增长则重抓评论。返回统计 dict。"""
    row = conn.execute(
        "SELECT 点赞数, 收藏数, 评论数, 浏览量, 评论已抓 FROM posts WHERE id=?",
        (pid,),
    ).fetchone()
    if not row:
        return None
    old_like, old_fav, old_cmt, old_visit, cmt_done = row

    data = with_retry(lambda: api_get(token, "post", {"id": pid}),
                      f"帖子{pid}详情", fuse)
    post = data.get("post") or {}
    new_like = int(post.get("like_count") or 0)
    new_fav = int(post.get("fav_count") or 0)
    new_cmt = int(post.get("comment_count") or 0)
    new_visit = int(post.get("visit_count") or 0)

    changed = (new_like != old_like or new_fav != old_fav
               or new_cmt != old_cmt or new_visit != old_visit)
    cmt_grew = new_cmt > old_cmt and cmt_done == 1

    if changed and not dry:
        conn.execute(
            "UPDATE posts SET 点赞数=?, 收藏数=?, 评论数=?, 浏览量=? WHERE id=?",
            (new_like, new_fav, new_cmt, new_visit, pid),
        )
        if cmt_grew:
            # 删旧楼层，重抓全部评论
            conn.execute('DELETE FROM floors WHERE "帖子ID"=?', (pid,))
            conn.execute('DELETE FROM official_replies WHERE "帖子ID"=?', (pid,))
            conn.commit()
            try:
                n_floors = crawl_one_post(conn, token, pid, FakeArgs(),
                                          fuse, lock, {"done": 0, "fail": 0})
                log(f"  帖 {pid} 评论重抓完成：{old_cmt} -> {new_cmt}（{n_floors} 层）")
            except FatalError as e:
                log(f"  帖 {pid} 评论重抓失败: {e}")
            except Exception as e:
                log(f"  帖 {pid} 评论重抓异常: {e}")

    return {
        "pid": pid, "changed": changed, "cmt_grew": cmt_grew,
        "old_cmt": old_cmt, "new_cmt": new_cmt,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--all", action="store_true", help="刷全部帖子")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--qps", type=float, default=6.0)
    args = ap.parse_args()

    token = get_token(interactive=False)
    log("token 获取成功")

    conn = open_db()
    if args.all:
        rows = conn.execute(
            "SELECT id FROM posts WHERE 正文已补=1 ORDER BY id"
        ).fetchall()
        log("全量刷新模式：刷所有正文已补的帖")
    else:
        rows = conn.execute(
            "SELECT id FROM posts "
            "WHERE 发布时间 >= datetime('now', ?) AND 正文已补=1 ORDER BY id",
            (f"-{args.days} days",),
        ).fetchall()
    ids = [r[0] for r in rows]
    if args.limit:
        ids = ids[-args.limit:]
    total = len(ids)
    log(f"待刷新 {total} 条，qps={args.qps}，dry_run={args.dry_run}")

    fuse = Fuse()
    base_delay = 1.0 / args.qps
    import threading
    lock = threading.Lock()
    n_changed = 0
    n_cmt = 0
    n_fail = 0
    n = 0
    t0 = time.time()

    for pid in ids:
        try:
            r = refresh_one(conn, token, pid, fuse, lock, dry=args.dry_run)
            if r and r["changed"]:
                n_changed += 1
                if r["cmt_grew"]:
                    n_cmt += 1
            n += 1
            if n % 500 == 0:
                rate = n / (time.time() - t0)
                eta = (total - n) / rate if rate else 0
                log(f"  进度 {n}/{total}，字段变化 {n_changed}，评论增长 {n_cmt}，"
                    f"失败 {n_fail}，ETA {eta/60:.0f}min")
            # 每帖提交：单次写锁仅毫秒级，网络等待在事务外，避免与每小时 update 锁冲突
            if not args.dry_run:
                conn.commit()
        except FuseTripped:
            log("熔断触发，退出")
            break
        except Exception as e:
            n_fail += 1
            if n_fail % 20 == 1:
                log(f"  帖 {pid} 失败({n_fail}): {e}")
        # 随机抖动 0.8~1.2 倍
        time.sleep(base_delay * random.uniform(0.8, 1.2))

    if not args.dry_run:
        conn.commit()
    conn.close()
    log(f"完成：{n}/{total}，字段变化 {n_changed}，评论增长 {n_cmt}，失败 {n_fail}")

    if args.rebuild and not args.dry_run:
        log("重建看板...")
        import subprocess
        subprocess.run([sys.executable, "-m", "wpy", "build"], check=False)
        log("看板重建完成")


if __name__ == "__main__":
    main()
