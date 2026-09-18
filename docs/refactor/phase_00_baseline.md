# Phase 00 交接报告：基线调查

## 1. 当前阶段

Phase 00：基线调查（只读代码 + 跑测试 + 固化文档）

## 2. 本阶段目标

```text
目标：
在不修改业务代码的前提下，完成目录/依赖/入口/机制/数据/测试的基线固化，
产出 REFACTOR_BASELINE.md 与本报告，供后续 Phase 对照与独立审查。
```

## 3. 实际完成内容

### 新增

- `REFACTOR_BASELINE.md` — 重构前总基线
- `docs/refactor/phase_00_baseline.md` — 本报告
- `docs/refactor/` 目录

### 修改

- 无业务代码修改
- 无 `wpy/` 下任何文件修改

### 删除

- 无

### 调查动作（只读）

1. 阅读 `README.md`、`wpy-sitewide/README.md`、`config.py`、`auth.py`、`cli.py`、`server.py`、`crawl.py`、`board_api.py` 开头、`ui.build_html`、`smoke_test.py`
2. 扫描 `crawler.py` 全部 `def`/`class` 与分节（HTTP/DB/list/body/comment/image/export/CLI）
3. 全局搜索 `wpy-favorites`、`urllib.request`、`RateLimiter`、`pagination_events` 等
4. 统计核心文件行数与 DB 表行数
5. 执行 compileall、smoke、CLI help/status

## 4. 架构变化

```text
重构前（本阶段现状，未变）：

crawl.py → cli → crawler.py (2469)
                    ├── HTTP / RateLimiter / Fuse / retry
                    ├── DB schema / migrate / CRUD
                    ├── list / body / comment / image / essence
                    ├── export / status / argparse / main
                    └── auth.get_token（独立但自带 http_json）
                              │
                         config.py
server.py → board_api.py → sqlite (只读)
ui.py (2319，内嵌前端)
wpy-favorites/（独立，无 import 依赖，待删）

重构后：

本阶段无架构变化（Phase 0 只建立基线）。
```

## 5. 行为变化检查

```text
CLI 行为是否变化：否
数据库字段是否变化：否
抓取 API 参数是否变化：否
默认 QPS 是否变化：否
看板 API 是否变化：否
认证行为是否变化：否
```

## 6. 测试结果

环境：Python 3.12.10，`PYTHONUTF8=1`，cwd=`wpy-sitewide/`

| 项目 | 结果 | 命令 / 证据 |
|---|---|---|
| Python compile | **PASS** | `python -m compileall -q wpy crawl.py tools` |
| Smoke Test | **PASS** | `python tools/smoke_test.py` → `ALL SMOKE PASS`，exit 0 |
| 数据库完整性（只读计数） | **PASS** | posts=202600, floors=212566 等 |
| HTTP Server（含在 smoke） | **PASS** | healthz / 401 / token API / detail 均 PASS |
| CLI | **PASS** | `crawl.py --help` exit 0；`status --brief` exit 0 |

Smoke 摘要：

```text
PASS import package
PASS build html bytes=100004
PASS init_info total=202600
PASS posts page / search / user / imgs / concurrent
PASS healthz / token required / api init / posts / user / post detail
PASS healthz no auth
ALL SMOKE PASS
```

**与任务书差异**：任务书预期 `http serve suite` 失败；本次基线通过。已记入 BASELINE §7，不视为已修复任务书所述 cp1252 问题——该问题需在无 `PYTHONUTF8` 的 cp1252 输出编码下复现验证。

## 7. 数据安全检查

```text
sitewide.db：
是否修改：否

数据条数（重构前基线）：
posts: 202600
floors: 212566
official_replies: 2625
images: 0
scrape_progress: 13
pagination_events: 21
essence_ids: 198

表结构：
是否变化：否（仅 SELECT / PRAGMA 读取）

数据备份：
路径：未在本阶段新建备份
已有：_backup/ 保持原样，未触碰
```

本阶段未修改数据库。

## 8. 删除内容

```text
删除：无
```

## 9. 暂未处理的问题

1. `ui.py` 仍约 2319 行（任务书：UI 优先级低于 crawler）
2. `crawler.py` 仍含全部 DB + HTTP + CLI（待 Phase 2/4/5）
3. `auth.http_json` 与 crawler HTTP 未统一（待 Phase 2）
4. `wpy-favorites` 及 README/复用手册引用仍在（待 Phase 1）
5. Windows cp1252 编码问题未在本环境复现、未修复（待 Phase 6 针对性验证）
6. `_fix_user.py` 死脚本仍存在（待 Phase 7）
7. 隔离 worktree 未能创建（见风险）

## 10. 风险判断

| 项 | 等级 | 说明 |
|---|---|---|
| crawler God Module | 高 | 后续任何功能都继续堆入；耦合难测 |
| HTTP 三处实现 | 中 | 重试/限速/错误语义不一致 |
| DB 与抓取耦合 | 中 | 迁移与写路径难单测 |
| worktree 创建被拦 | 中 | 共享主工作区改文件有并发风险；需仅路径内操作、仅提交 snatch-wpy |
| cp1252 serve 崩溃 | 中 | 环境相关；基线未复现不等于不存在 |
| favorites 残留文档 | 低 | 无代码依赖 |
| ui.py 体量 | 中 | 可维护性；非本轮最高优先级 |

## 11. 本阶段修改文件清单

```text
新增：
  REFACTOR_BASELINE.md
  docs/refactor/phase_00_baseline.md

修改：
  （无业务代码）

删除：
  （无）

未修改：
  wpy-sitewide/wpy/* 全部
  wpy-favorites/*
  data/sitewide.db
  _backup/*
  wepeiyang-src/*
  一键抓取.bat / 打开看板.bat / open_board.bat
```

## 12. Git / 文件差异摘要

```text
仓库根：D:/Documents/AI-System
分支：main（HEAD 85c75dc）
另有：agents/snatch-wpy-refactor 已创建（本阶段用于后续，未 checkout）
worktree add：被隔离策略 blocked

git status（snatch-wpy 范围，Phase 0 报告写入后预期）：
  A/?? REFACTOR_BASELINE.md
  A/?? docs/refactor/phase_00_baseline.md
  其余业务文件相对 85c75dc 无改动

git diff --stat（业务代码）：空
```

## 13. 对下一阶段的建议

建议下一阶段（Phase 1：删除 wpy-favorites）：

1. 再次全局搜索 `wpy-favorites` / `收藏导出` / `复用手册`，确认仅文档与目录本身
2. 删除 `wpy-favorites/` 整目录
3. 更新根 `README.md`（去掉收藏导出章节与目录树条目）；评估删除或改写 `docs/复用手册_收藏导出与看板.md`（任务书：删除其相关说明文档）
4. 核对 `wpy-sitewide/README.md` 第 47 行「不依赖 wpy-favorites」表述是否保留为历史说明
5. 复跑 `compileall` + `smoke_test.py` + `crawl.py --help`，写 `phase_01_remove_favorites.md`

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
