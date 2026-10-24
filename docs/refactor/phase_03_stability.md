---
name: phase_03_stability
description: Phase 3 稳定性增强 — run_workers 异常传播、_img_locks 限容、分页导出
metadata:
  phase: 3
  title: 稳定性增强
  status: completed
---

# Phase 3: 稳定性增强

## 目标

提升系统在长时间运行、高并发、大数据量场景下的稳定性与可观测性。

## 变更清单

### 1. `wpy/crawler/shared.py` — `run_workers` 异常传播

**问题：** `run_workers(fn, n)` 以多线程并发执行 `fn()`，但 worker 抛异常时会被静默吞掉，导致爬虫任务失败却无报错日志。

**修复：** 引入 `_worker_errors` 列表 + 锁收集异常，所有线程 join 后重新抛出第一个异常。`FuseTripped` 除外（它是正常退出信号）。

```python
def run_workers(fn, n):
    if n <= 1:
        fn()
        return
    _worker_errors = []
    _errors_mu = threading.Lock()
    def _safe_fn():
        try:
            fn()
        except Exception as e:
            if isinstance(e, FuseTripped):
                return
            with _errors_mu:
                _worker_errors.append(e)
    threads = [threading.Thread(target=_safe_fn, daemon=True) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if _worker_errors:
        raise _worker_errors[0]
```

**影响范围：** 正文抓取、评论抓取、图片下载等多线程阶段。

### 2. `wpy/server.py` — `_img_locks` 字典限容

**问题：** 图片锁字典 `_img_locks` 在服务器长期运行时只增不减，每个新图片 URL 都会新增一个条目，存在内存泄漏风险。

**修复：** 添加 `_IMG_LOCKS_MAX = 5000` 上限，超过时清理最久未使用的锁（字典插入顺序近似 LRU）。

```python
_IMG_LOCKS_MAX = 5000
_img_locks = {}
_img_locks_mu = threading.Lock()

def _img_lock(name):
    with _img_locks_mu:
        lk = _img_locks.get(name)
        if lk is None:
            if len(_img_locks) >= _IMG_LOCKS_MAX:
                to_remove = list(_img_locks.keys())[:_IMG_LOCKS_MAX // 2]
                for k in to_remove:
                    del _img_locks[k]
            lk = threading.Lock()
            _img_locks[name] = lk
        return lk
```

### 3. `wpy/crawler/export_ops.py` — 分页导出

**问题：** `do_export()` 一次性 `SELECT * FROM posts` 加载全部 20 万+ 行到内存，再写入 CSV/JSON。对于 400MB+ 的数据库，这会导致峰值内存占用数 GB。

**修复：** 改为 5000 行一页的分页查询，CSV 逐批追加写入，JSON 分批累积后一次 dump。

```python
PAGE_SIZE = 5000
offset = 0
cur = conn.execute("SELECT * FROM posts ORDER BY id LIMIT ? OFFSET ?", (PAGE_SIZE, offset))
rows = [dict(zip(names, r)) for r in cur.fetchall()]
while len(rows) == PAGE_SIZE:
    offset += PAGE_SIZE
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    # CSV 追加、JSON 累积
```

## 测试验证

### 离线阶段冒烟测试

```
PASS compat import stages
PASS symbols ... (所有模块符号)
PASS do_list posts=2
PASS crawl_essence essence=1
PASS crawl_my_posts marked=1
PASS do_body body_ok=3
PASS do_comment floors=2 done_posts=2
PASS do_image refs=3 saved=3
PASS run_pipeline posts=4 html=True
PASS real sitewide.db intact (205568 posts, 1483841 floors)
ALL OFFLINE STAGE SMOKE PASS
```

### 全量冒烟测试

```
PASS import package
PASS build html bytes=120399
PASS init_info total=205568
PASS posts page ms=11 total=205568
PASS healthz
PASS token required code=401
PASS api posts
PASS api user
PASS api post detail floors=0/0
ALL SMOKE PASS
```

## 未完成的子任务

**全局状态显式化（依赖注入）** — 原计划将全局状态（数据库连接、配置等）改为显式参数传递，但因涉及改动面过大（数十个函数签名、多个模块的调用链），风险较高，决定延后到独立阶段处理。

## 后续建议

- 考虑使用 `sqlite3.Row` 替代 `dict(zip(names, r))` 减少内存分配
- 长期运行场景下，可考虑将 `_img_locks` 替换为 `collections.OrderedDict` 实现更精确的 LRU
- JSON 导出仍全量加载，可考虑 NDJSON 格式支持流式写入
