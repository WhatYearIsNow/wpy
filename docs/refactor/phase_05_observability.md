---
name: phase_05_observability
description: Phase 5 可观测性 — 结构化日志、指标收集、HTTP 请求追踪
metadata:
  phase: 5
  title: 可观测性
  status: completed
---

# Phase 5: 可观测性

## 目标

为抓取和服务端添加结构化日志与指标收集，使系统行为可量化、可调试、可告警。

## 变更清单

### 新增模块

**`wpy/metrics.py`** — 轻量级指标收集器

- `Counter`：线程安全计数器，支持 `inc(n)` / `get()`
- `Timer`：线程安全计时器，记录 `count`/`total`/`avg`
- `MetricsRegistry`：全局注册表，`snapshot()` 返回所有指标 JSON
- `StructuredLogger`：JSON lines 结构化日志，自动刷盘（每 50 行）
- 全局单例 `metrics` 和 `init_structured_logger()`

**设计原则：**
- 零外部依赖（仅标准库）
- 低开销（原子锁 + 批量写入）
- 不阻塞主流程（指标收集失败不抛异常）

### HTTP 层指标集成

**`wpy/http.py`** — `request_json()` 自动记录：
- `http.requests`：总请求数
- `http.status.{code}`：按状态码计数
- `http.errors`：异常请求数
- `http.duration`：请求耗时（秒）

### 服务端指标端点

**`wpy/server.py`** — 新增 `/api/metrics`：
```
GET /api/metrics → {"counters": {...}, "timers": {...}}
```
管理员可实时查看系统运行指标，无需查看日志文件。

### 配置

**`wpy/config.py`** — 新增 `METRICS_JSON_PATH`：
- 默认路径：`logs/结构化日志.jsonl`
- 可通过 `WPY_LOG_DIR` 环境变量覆盖

## 测试

**`tests/test_metrics.py`** — 17 个测试用例：
- Counter：初始值、递增、批量递增
- Timer：初始状态、单次观察、多次观察
- MetricsRegistry：计数器/计时器独立、snapshot、reset
- StructuredLogger：写入、多行、自动刷盘、空刷盘

## 测试结果

```
162 passed in 0.64s
```

离线冒烟测试 + 全量冒烟测试均通过。

## 后续建议

- 在 `crawler/shared.py` 的 `run_workers` 中记录阶段完成指标
- 在 `db.py` 的 `save_posts`/`save_floors` 中记录写入量指标
- 考虑将结构化日志接入外部日志系统（如 Loki、ELK）
- 为 `RateLimiter` 添加 `rate.current`/`rate.429` 指标
