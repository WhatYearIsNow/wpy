# REFACTOR_CHANGELOG.md

> snatch-wpy / wpy-sitewide 结构化重构变更记录  
> 阶段报告见 `docs/refactor/`

## 删除

- `wpy-favorites/` 整目录（个人收藏导出工具，已废弃）
- `docs/复用手册_收藏导出与看板.md`
- README / `.gitignore` / auth 注释中的 favorites 引用
- `wpy/crawler.py` 单文件 God Module（备份：`_backup/refactor_crawler_before_package/crawler.py`）
- 运行时双实现：HTTP/限速/熔断/重试/日志/schema 不再在 crawler/auth/server 各写一份

## 新增

| 路径 | 职责 |
|---|---|
| `wpy/http.py` | GET/JSON/token/429/重试/全局 QPS RateLimiter/Fuse/get_bytes |
| `wpy/runlog.py` | log / 运行状态文件 / progress / Ticker |
| `wpy/db.py` | schema、迁移、posts/floors/images/progress/essence CRUD |
| `wpy/crawler/` 包 | list / essence / body / comment / image / export / pipeline / cli |
| `docs/refactor/phase_*.md` | 各阶段交接报告 |
| `tools/test_serve_utf8.py` | cp1252 下 serve 中文输出回归测试 |

## 迁移

- `auth.http_json` → `http.request_json(..., throttle=False)`
- `crawler` 内 HTTP/日志/DB 定义 → `http` / `runlog` / `db`（`crawler` 包只再导出兼容符号）
- server 图片代理 → `http.get_bytes`
- CLI/serve 启动 → `_ensure_utf8_stdio()`

## 修复

- Windows 非 UTF-8 控制台下 `看板服务已启动` 可能触发 `UnicodeEncodeError` 导致 serve 线程退出  
  → 入口安全 reconfigure stdout/stderr 为 utf-8

## 测试结果（最终）

| 检查 | 结果 |
|---|---|
| `python -m compileall -q wpy crawl.py tools` | PASS |
| `python tools/smoke_test.py` | PASS（含 http serve suite） |
| `python tools/offline_stage_smoke.py` | PASS（阶段入口 + 临时库写入） |
| `python tools/phase10_scope_test.py` | PASS（repair 作用域 + invalid type） |
| `python tools/test_serve_utf8.py` | PASS |
| `python crawl.py --help` | PASS |
| `python crawl.py status --brief` | PASS |
| DB 行数（Phase 10 最终） | posts=202607 floors=212619 official=2625 pagination=21 essence=198 images=3（Phase 9 合法增量后） |

## 兼容性

- `python crawl.py all|resume|update|partial|status|logout|whoami|diagnose|serve|...` 保留
- `python -m wpy` 保留
- `一键抓取.bat` / `打开看板.bat` 未改调用方式
- `from wpy.auth import get_token` 保留
- `from wpy.crawler import main, api_get, open_db, ...` 保留（指向统一实现）
- 禁止 DROP 的 `data/sitewide.db` 未重建

## 已知剩余问题

1. `ui.py` 仍约 2300 行（Python+HTML/CSS/JS 单文件），优先级低于抓取层
2. `do_list` / `do_comment` 单函数仍偏长（逻辑已迁包，未再算法重构）
3. `_fix_user.py` 已移入 `_backup/dead_scripts/`，生产路径不再保留
4. crawler 阶段模块仍有部分 `conn.execute` 业务查询（属“DB 基础能力下沉”，非完全 storage-only）
5. 真实外网全站抓取未在重构会话内重跑（需账号与长时间）；依赖 offline stage smoke + CLI + smoke_test
6. `board_api.py` 与 `db.py` 双访问层（读写分离）仍并存，属设计选择
7. 仓库无独立 snatch-wpy git；变更位于 AI-System 主仓库路径内

## Phase 8（审查后修复）

- 修复拆包遗漏：`body/comment/essence/list` 缺失 `open_db`/`threading`；`list._record_pagination_event` → `record_pagination_event`；`list` 补 `_parse_since_date`；`shared` 补 `threading`
- 新增 `tools/offline_stage_smoke.py`（临时库 + mock API，覆盖 list/essence/body/comment/image/pipeline）
- 修正 `tools/test_serve_utf8.py`：显式 reconfigure stdout 为 cp1252 后再测 `_ensure_utf8_stdio`
- 文档：BASELINE 日期与 FINAL_REPORT 阶段列表对齐

- 真实小流量验证（Phase 9）：`partial/body/comment --ids/list --since-id/image --limit`；posts 202600→202607，floors 212566→212619（+53），schema 不变
- offline smoke 真实库断言改为结构完整 + 计数下限（允许合法抓取增量）
- `_fix_user.py` 移入 `_backup/dead_scripts/`（全局无代码引用）
- **Phase 10 收尾**：`reset_incomplete_comments` 支持 `types/ids/limit` 作用域；仅 `--limit` 且无范围时跳过全局 repair；`list/partial` 对无效 `--types` 明确警告并停止该阶段（不再静默空跑）
- 临时库专项测试：`tools/phase10_scope_test.py` PASS；真实库 Phase10 前后计数一致

- 重构结束后清理本地临时/废弃产物：`_shots/` `_dl/` `_backup/`（含旧 crawler 与 dead_scripts 备份）、空 `scripts/`、根目录调试脚本 `tools/audit_*.py`/`cdp.py`、`docs/refactor/phase09_db_*.json` 临时快照；`.gitignore` 同步忽略上述路径
- 测试脚本保留在 `wpy-sitewide/tools/`：smoke / offline / phase10_scope / test_serve_utf8 / phase09 辅助脚本

## 回滚

- favorites：Git 历史或备份策略
- crawler 单文件：`_backup/refactor_crawler_before_package/crawler.py`
- 数据：`data/sitewide.db` 本次未破坏性迁移
