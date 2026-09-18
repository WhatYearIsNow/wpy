# FINAL_REPORT：snatch-wpy / wpy-sitewide 结构化重构

## 1. 重构前架构

```text
crawl.py → cli → crawler.py (2469 行 God Module)
                    ├── HTTP / RateLimiter / Fuse / retry
                    ├── DB schema / migrate / CRUD
                    ├── list / body / comment / image / essence
                    ├── export / status / argparse / main
                    └── auth 自带 urllib http_json
server.py 自带 urllib 图片下载
wpy-favorites/ 独立废弃工具仍在
```

## 2. 重构后架构

```text
crawl.py / python -m wpy
        ↓
      wpy.cli.main  (+ UTF-8 stdio)
        ↓
   wpy.crawler (package)
        ├── cli_app → pipeline
        │               ├── list / essence
        │               ├── body
        │               ├── comment
        │               ├── image
        │               └── export_ops → ui.build_from_db
        └── status_view
              ↓
     http.py (LIMITER / api_get / retry / fuse)
              ↓
     db.py (schema / save_* / progress) → data/sitewide.db

auth.py → http (登录/token/凭据 only)
server.py → board_api + ui + http.get_bytes
```

## 3. 删除内容

- `wpy-favorites/` 及文档/gitignore 引用
- `wpy/crawler.py` 单文件（备份于 `_backup/refactor_crawler_before_package/`）
- 分散的 urllib/ssl/json 请求实现、重复 RateLimiter/Fuse/日志/建表代码

## 4. 新增模块

- `wpy/http.py`
- `wpy/runlog.py`
- `wpy/db.py`
- `wpy/crawler/`（shared, list, essence, body, comment, image, export_ops, status_view, pipeline, cli_app）
- `docs/refactor/phase_00` … `phase_07_cleanup.md`（含 HANDOFF）
- `REFACTOR_BASELINE.md` / `REFACTOR_CHANGELOG.md`
- `tools/test_serve_utf8.py`

## 5. 核心依赖关系

```text
cli → crawler.* → http / db / runlog / auth / ui
auth → http
server → board_api, ui, http, cli._ensure_utf8_stdio
db → runlog, config
http → runlog, config
board_api → sqlite3 only（只读）
无生产循环依赖：crawler ↮ server；http ↮ db
```

## 6. 数据库兼容性

- 路径：`wpy-sitewide/data/sitewide.db`（未 DROP/重建）
- 迁移逻辑原样保留在 `db.open_db`（增量补列/改名）
- 最终计数与基线一致：posts 202600 / floors 212566 / official 2625 / pagination_events 21 / essence_ids 198

## 7. CLI 兼容性

保留命令：`all, resume, partial, update, status, probe, list, body, comment, image, export, build, verify, diagnose, logout, whoami, reopen, serve`

入口：`crawl.py`、`python -m wpy`、bat 脚本调用方式未改。

## 8. 测试结果

```text
compileall: PASS
smoke_test: PASS (ALL SMOKE PASS，含 http serve suite)
test_serve_utf8 (cp1252→utf-8): PASS
offline_stage_smoke: PASS（list/essence/body/comment/image/pipeline）
phase10_scope_test: PASS（repair ids/types/limit/global + invalid type）
crawl.py --help: PASS
crawl.py status --brief: PASS
crawl.py whoami: PASS
真实库：Phase9 合法增量后冻结；Phase10 测试前后计数一致（202607/212619/2625/21/198/3）
```

Phase 8 已关闭运行时符号阻断；Phase 9 完成真实小流量验证；Phase 10 收窄 repair 作用域并提示无效 type（见各 phase 报告）。
DB 边界：schema/通用 CRUD 下沉到 `db.py`；阶段业务查询仍可留在 crawler。

## 9. 已知技术债

| 项 | 风险 |
|---|---|
| ui.py 单文件大前端 | 中（可维护性） |
| do_list/do_comment 函数仍长 | 中 |
| 未在本会话跑真实全量抓取 | 中（需账号/时间） |
| 根 `_fix_user.py` 死脚本 | 低（已移 `_backup/dead_scripts/`） |
| board_api vs db 双访问层 | 低（读写分离） |
| 无独立项目 git worktree | 低（操作纪律） |
| comment CLI 未暴露 `--no-repair` | 低（代码层已支持作用域 repair） |
| 未跑真实全站抓取 | 中（已用 partial/stage 小流量验证） |

## 10. 后续建议

1. **重构收尾完成**：不再新增结构性模块，不再拆 `ui.py`
2. 文档定稿：CHANGELOG / FINAL_REPORT / phase_09–10 已对齐 Phase 10 行为
3. 可选：README 补一句「评论补抓与 --types/--ids 范围一致；无效板块 ID 会提示」
4. 可选：comment 子命令暴露 `--no-repair`
5. 有条件时再做更长时间的 update/partial 抓取观察

## Git / 目录

本目录无独立 `.git`；变更在 `D:/Documents/AI-System` 仓库路径内。

收尾清理后本地不再保留 `_backup/`、`_shots/`、`_dl/` 与 phase09 JSON 快照；旧 `crawler.py`/favorites 的回滚依赖 Git 历史（`85c75dc` 及本次提交）。

```text
docs/refactor/
├── phase_00_baseline.md
├── phase_01_remove_favorites.md
├── phase_02_http.md
├── phase_03_auth.md
├── phase_04_db.md
├── phase_05_crawler.md
├── phase_06_server.md
├── phase_07_cleanup.md
├── phase_08_runtime_fix.md
├── phase_09_real_integration.md
├── phase_10_final_cleanup.md
└── FINAL_REPORT.md
```

### 当前 wpy 包树（业务）

```text
wpy-sitewide/wpy/
├── __init__.py
├── __main__.py
├── cli.py
├── config.py
├── auth.py
├── http.py
├── runlog.py
├── db.py
├── board_api.py
├── server.py
├── ui.py
└── crawler/
    ├── __init__.py
    ├── shared.py
    ├── list.py
    ├── essence.py
    ├── body.py
    ├── comment.py
    ├── image.py
    ├── export_ops.py
    ├── status_view.py
    ├── pipeline.py
    └── cli_app.py
```
