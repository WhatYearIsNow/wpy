# Phase 04 交接报告：抽离数据库层

## 1. 当前阶段

Phase 04：抽离数据库层 `wpy/db.py`

## 2. 本阶段目标

```text
目标：
把 schema、migration、索引、posts/floors/official/images/scrape_progress/
pagination_events/essence_ids 的读写从 crawler 迁到 wpy/db.py；
抓取阶段只调用 storage API，不再自带建表与大量内联 SQL。
禁止 DROP / 重建真实数据库。
```

## 3. 实际完成内容

### 新增

- `wpy/db.py`：列定义、`open_db`/迁移/索引、`save_posts`/floors/official/images、
  progress CRUD、`status_summary`、`reset_incomplete_comments`、essence 集合写入等

### 修改

- `wpy/crawler.py`：删除本地 schema/CRUD 定义；改为 `from .db import ...`；
  阶段函数改调 `save_posts` / `update_scrape_page` / `save_floors` /
  `mark_post_comment_status` / `update_post_body` / `collect_image_refs` /
  `status_summary` / `list_done_boards` 等
- `docs/refactor/phase_04_db.md`（本报告）

### 有意未迁（阶段查询仍留在 crawler）

- `build_post_filter` 等业务筛选 WHERE 构造（属阶段语义，不是 schema）
- export 读全表写 CSV/JSON（编排层）
- diagnose / probe 等诊断查询

## 4. 架构变化

```text
重构前：

crawler.py
 ├── open_db / schema / migrate
 ├── save_posts / floors / images / progress SQL
 └── list/body/comment 阶段逻辑

重构后：

crawler（阶段编排）
    │  save_* / mark_* / status_summary
    ▼
  db.py ──► SQLite data/sitewide.db
    ▲
board_api.py（只读看板查询，仍独立）

依赖方向：CLI/pipeline → crawler stages → db → sqlite
http 与 db 互不依赖（均依赖 config/runlog）
```

## 5. 行为变化检查

```text
CLI 行为是否变化：否
数据库字段是否变化：否（沿用同一 schema 与迁移逻辑）
抓取 API 参数是否变化：否
默认 QPS 是否变化：否
看板 API 是否变化：否
认证行为是否变化：否

特别核对：
- save_posts 仍用 UPSERT + POST_BASE_NAMES（不抹进度/集合列）
- reopen 仍需用户 y 确认后才改 scrape_progress
- update_post_body 保留 uid/作者 COALESCE 语义（body 阶段）
- pagination_events 表与写入路径保留
```

## 6. 测试结果

| 项目 | 结果 |
|---|---|
| compileall | PASS |
| smoke_test | PASS / ALL SMOKE PASS |
| crawl.py status --brief | PASS，文案与基线一致 |
| crawl.py whoami | PASS |
| open_db 同一性 | `crawler.open_db is db.open_db` → True |
| status_summary | posts=202600 body=202588 comments=65248 floors=212566 official=2625 boards 8/13 |

## 7. 数据安全检查

```text
sitewide.db：
是否修改：否（本阶段仅只读连接与 smoke 测试读库）
数据条数：
  重构前：posts 202600 / floors 212566 / official 2625 / pagination 21 / essence 198
  重构后：相同（status_summary 与直接 COUNT 一致）
表结构：未变化（迁移代码逻辑原样迁入 db.py，未 DROP）
数据备份：本阶段未新建备份（无 schema 破坏性变更）
```

## 8. 删除内容

```text
删除（迁入 db.py，非丢弃）：
- crawler 内 POST_COLUMNS/FLOOR_COLUMNS/... 定义
- crawler 内 open_db/_migrate/_rename_columns/_fix_recommend_rows
- crawler 内 save_posts/post_tuple/floor_tuple/iter_floors 等实现
- crawler 内部分进度/正文/评论/图片写 SQL（改为调用 db）

未删除任何业务表或数据文件。
```

## 9. 暂未处理的问题

1. `crawler.py` 仍约 1700 行：阶段编排 + CLI 未拆（Phase 5）
2. 部分阶段查询仍内联 SQL（可后续收口到 db，非必须）
3. `board_api.py` 与 `db.py` 双访问层（读写分离，合理保留，审查者可评估）
4. `ui.py` 仍大文件
5. Windows 编码未在本阶段修（Phase 6）

## 10. 风险判断

| 项 | 等级 |
|---|---|
| 写库语义搬错导致进度/标记被清 | 中（UPsert/COALESCE 已对照保留；需审查者核对 SQL） |
| 循环依赖 | 低（db→runlog/config；crawler→db/http） |
| God Module | 中高（文件变短但仍集中 CLI+各阶段） |
| 数据损坏 | 低（未 DROP；行数一致） |

## 11. 本阶段修改文件清单

```text
新增：
  wpy-sitewide/wpy/db.py
  docs/refactor/phase_04_db.md

修改：
  wpy-sitewide/wpy/crawler.py
  docs/refactor/phase_04_db.md

未修改：
  board_api.py / server.py / ui.py / auth.py / http.py / runlog.py
  crawl.py / cli.py / config.py
  data/sitewide.db
  _backup/
```

## 12. Git / 文件差异摘要

```text
A  wpy-sitewide/wpy/db.py
M  wpy-sitewide/wpy/crawler.py
A  docs/refactor/phase_04_db.md
```

## 13. 对下一阶段的建议

1. 将 `crawler.py` 拆为 `wpy/crawler/` 包：list / body / comment / image / essence / pipeline / cli
2. `wpy/crawler/__init__.py` 再导出 `main` 与兼容符号，保持 `cli.py`/`crawl.py` 不变
3. 禁止出现 crawler_old/crawler_new 双份
4. 拆分后复跑 compileall + smoke + CLI status
5. 再处理 server UTF-8 与清理

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
