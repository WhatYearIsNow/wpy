# Phase 02 交接报告：统一 HTTP 层

## 1. 当前阶段

Phase 02：统一 HTTP 层

## 2. 本阶段目标

```text
目标：
将 crawler / auth / server 中重复的 urllib HTTP、限速、熔断、重试
实现统一到 wpy/http.py；运行日志抽到 wpy/runlog.py 以避免循环依赖。
业务层改为调用 client api（request_json / api_get / get_bytes）。
```

## 3. 实际完成内容

### 新增

- `wpy/http.py`：HTTP + RateLimiter + Fuse + with_retry + api_get + get_bytes + pace + 异常类型
- `wpy/runlog.py`：log / write_run_state / mark_finished / progress / Ticker

### 修改

- `wpy/auth.py`：删除本地 `http_json` 的 urllib/ssl 实现；改为委托 `http.request_json(..., throttle=False)`；`FatalError` 从 `http` 再导出
- `wpy/crawler.py`：删除 HTTP/日志/限速/熔断/重试约 340 行本地实现；改为 `from .http import ...` / `from .runlog import ...`；兼容再导出同一对象；图片下载改 `get_bytes(..., throttle=False)`
- `wpy/server.py`：图片代理删除内联 urllib/ssl，改 `get_bytes(..., throttle=False, max_bytes=...)`

### 删除

- crawler 内：`request_json` / `api_get` / `Fuse` / `RateLimiter` / `LIMITER` / `with_retry` / `pace` / `Throttled` / `BadRequest` / `log` / `Ticker` 等**定义**
- auth 内：本地 urllib 请求实现
- server 内：图片代理的内联 urllib 请求

> 定义只保留一套；`from wpy.crawler import api_get` 等通过再导出仍指向 `http` 同一对象。

## 4. 架构变化

```text
重构前：

crawler.py
 ├── log / Ticker / run_state
 ├── HTTP request_json / api_get
 ├── RateLimiter / Fuse / with_retry
 ├── DB schema / stage CRUD
 └── list/body/comment/image + CLI

auth.py ── 自带 urllib http_json
server.py ── 自带 urllib 图片下载

重构后：

cli → crawler（pipeline/阶段/CLI/DB 暂留）
        ├── from http import api_get/with_retry/LIMITER/Fuse...
        └── from runlog import log/Ticker...

auth ──► http.request_json (throttle=False)
server ──► http.get_bytes (throttle=False)
http ──► runlog.log / config
runlog ──► config

业务层不再直接写 urllib.request / ssl.create_default_context / json.loads。
```

## 5. 行为变化检查

```text
CLI 行为是否变化：否
数据库字段是否变化：否
抓取 API 参数是否变化：否
默认 QPS 是否变化：否（LIMITER 默认 TARGET_QPS=10.0）
看板 API 是否变化：否
认证行为是否变化：否（认证仍 throttle=False，不受抓取限速影响）
图片下载限速：否（保持历史：JSON API 限速，图片/代理不限速）
```

## 6. 测试结果

| 项目 | 结果 | 命令 |
|---|---|---|
| Python compile | **PASS** | `python -m compileall -q wpy crawl.py tools` |
| Smoke Test | **PASS** | `python tools/smoke_test.py` → ALL SMOKE PASS |
| CLI status | **PASS** | `python crawl.py status --brief` |
| CLI whoami | **PASS** | `python crawl.py whoami` |
| 对象同一性 | **PASS** | `crawler.api_get is http.api_get`；`crawler.LIMITER is http.LIMITER` |
| 数据库行数 | **未变** | posts 202600 / floors 212566 / official 2625 / pagination_events 21 / essence_ids 198 |

## 7. 数据安全检查

```text
sitewide.db：是否修改：否
本阶段未修改数据库。
备份：未新建（本阶段无 schema/数据变更）
```

## 8. 删除内容

```text
见「实际完成内容 → 删除」。
urllib/ssl 请求实现现仅存在于 wpy/http.py。
```

## 9. 暂未处理的问题

1. `crawler.py` 仍包含 DB schema/CRUD 与全部阶段逻辑（God Module 仍在）
2. `auth.py` 职责边界已清晰，但可选清理：`http_json` 名称是否保留（兼容保留）
3. `ui.py` 仍约 2319 行
4. Windows cp1252 下 serve 中文打印未在本阶段专门处理（Phase 6）
5. 根 `_fix_user.py` 死脚本仍在
6. crawl 内大量阶段函数仍直接拼 SQL（Phase 4）

## 10. 风险判断

| 项 | 等级 |
|---|---|
| HTTP 语义被搬坏导致行为回归 | 中（已用 smoke + 对象同一性缓解；真实抓取 API 未在本阶段外网跑） |
| 图片下载误走限速 | 低（已显式 throttle=False） |
| God Module | 高（未处理） |
| 循环依赖 | 低（http→runlog→config；auth/crawler/server→http） |

## 11. 本阶段修改文件清单

```text
新增：
  wpy-sitewide/wpy/http.py
  wpy-sitewide/wpy/runlog.py

修改：
  wpy-sitewide/wpy/auth.py
  wpy-sitewide/wpy/crawler.py
  wpy-sitewide/wpy/server.py
  docs/refactor/phase_02_http.md（本报告）

删除：
  （无文件删除；代码定义迁移）

未修改：
  board_api.py
  ui.py
  cli.py
  crawl.py
  config.py
  data/sitewide.db
  _backup/
```

## 12. Git / 文件差异摘要

```text
项目无独立 git；变更位于 AI-System 主仓库内路径：
  A  wpy-sitewide/wpy/http.py
  A  wpy-sitewide/wpy/runlog.py
  M  wpy-sitewide/wpy/auth.py
  M  wpy-sitewide/wpy/crawler.py
  M  wpy-sitewide/wpy/server.py
  A  docs/refactor/phase_02_http.md
```

## 13. 对下一阶段的建议

建议下一阶段（Phase 03/04）：

1. 确认 `auth` 只暴露认证相关 API（已完成主体；报告固化）
2. 新建 `wpy/db.py`，迁出 schema/migration/open_db/posts/floors/progress SQL
3. crawler 阶段函数改为调用 `db.*`，删除内联建表/迁移
4. 迁移前后核对 sitewide.db 表结构与行数，禁止 DROP
5. 复跑 compileall + smoke + status

---

=== HANDOFF ===

以下内容将交给下一位架构审查智能体。

请不要默认接受本阶段实现。

请基于：
1. 当前代码
2. 本阶段报告
3. 测试结果
4. 文件差异

独立判断当前重构是否合理。

重点检查：
- 是否真正降低耦合
- 是否存在重复实现
- 是否引入循环依赖
- 是否改变业务行为
- 是否破坏数据库兼容性
- 是否存在过度工程化
- 是否仍有明显 God Module
- 是否存在临时 / 历史代码残留

请输出：

A. 已确认正确的部分
B. 发现的问题
C. 必须修复的问题
D. 可以延后的问题
E. 下一阶段具体任务
F. 下一阶段执行提示词

不要输出总体评分、排名或空泛评价。

下一位智能体应当以当前真实代码为准，而不是以本报告的自我描述为准。
