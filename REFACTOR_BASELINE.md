# REFACTOR_BASELINE.md

> Phase 0 基线快照。记录重构前真实状态，供后续阶段对照。
> 生成时间：2026-09-18（会话内执行）
> 项目根：`D:\Documents\AI-System\Projects\snatch-wpy`
> Git：归属上级仓库 `D:/Documents/AI-System`，当前 HEAD `85c75dc`

---

## 1. 当前模块

### 1.1 目录（业务相关）

```text
snatch-wpy/
├── README.md
├── .gitignore
├── _fix_user.py                 # 一次性补丁脚本（引用已不存在的 dashboard.py）
├── docs/
│   ├── 复用手册_收藏导出与看板.md   # 服务 wpy-favorites，Phase 1 待清理
│   └── 选课流程录制.md
├── scripts/                     # 空目录
├── tools/                       # audit_loading.py / audit_ui.py / cdp.py（调试用）
├── wpy-favorites/               # 待废弃（Phase 1 删除）
├── wpy-sitewide/                # 核心业务
│   ├── crawl.py                 # 兼容入口
│   ├── 一键抓取.bat / 打开看板.bat / open_board.bat
│   ├── 使用说明.txt / README.md
│   ├── deploy/
│   ├── tools/smoke_test.py      # 注意：在 wpy-sitewide/tools/，不是根 tools/
│   ├── resources/
│   ├── data/sitewide.db         # 真实数据（约 154 MB，gitignore）
│   └── wpy/
│       ├── __init__.py          # 17 行
│       ├── __main__.py
│       ├── cli.py               # 13 行 → 委托 crawler.main
│       ├── config.py            # 48 行
│       ├── auth.py              # 127 行
│       ├── crawler.py           # 2469 行  ← God Module
│       ├── board_api.py         # 432 行
│       ├── server.py            # 300 行
│       └── ui.py                # 2319 行（Python+HTML+CSS+JS 单文件）
├── wepeiyang-src/               # 参考源码，只读，不重构
├── _backup/                     # 禁止触碰
├── _shots/ / _dl/               # 调试截图/下载，已 gitignore
```

### 1.2 行数与职责（重构前）

| 文件 | 行数 | 当前职责 |
|---|---:|---|
| `wpy/crawler.py` | 2469 | HTTP、限速、熔断、日志、DB schema/迁移/CRUD、list/body/comment/image/essence、export、CLI parser、pipeline、status |
| `wpy/ui.py` | 2319 | HTML/CSS/JS 内嵌 + `build_html` / `build_from_db` |
| `wpy/board_api.py` | 432 | 只读 BoardDB 查询层 |
| `wpy/server.py` | 300 | 看板 HTTP、token、图片代理 |
| `wpy/auth.py` | 127 | 登录/token/凭据/whoami/logout（自带 `http_json`） |
| `wpy/config.py` | 48 | 路径、QPS、API 常量 |
| `wpy/cli.py` | 13 | 转发 `crawler.main` |

`crawler.py` 内约 **75** 个 `def`/`class`，分节大致为：

| 约行范围 | 职责 |
|---|---|
| 32–141 | 日志、运行状态、进度、Ticker |
| 146–365 | HTTP、`api_get`、Fuse、RateLimiter、`with_retry` |
| 371–674 | schema、`open_db`、迁移、post/floor 序列化、`save_posts` |
| 679–1302 | list 阶段（含 essence/my_posts/probe/confirm） |
| 1307–1398 | body 阶段 |
| 1405–1737 | comment 阶段 |
| 1742–1831 | image 阶段 |
| 1836–1930 | export / verify / reopen |
| 1947–2469 | argparse CLI、status、pipeline modes、`main` |

---

## 2. 当前依赖关系

```text
crawl.py ──► wpy.cli ──► wpy.crawler
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
          wpy.auth        wpy.config       wpy.ui (build_from_db)
              │
              └── urllib/ssl（自带 http_json，不走 crawler）

wpy.server ──► wpy.board_api, wpy.config, wpy.ui.build_html
wpy.board_api ──► sqlite3（只读）
wpy-favorites/* ──► 独立脚本，不 import wpy
```

**HTTP 实现分散（3+ 处）：**

1. `crawler.request_json` + `api_get` + `RateLimiter` + `with_retry` + `Fuse`
2. `auth.http_json`（无全局限速/熔断）
3. `server._serve_img` 内联 `urllib.request` 图片下载
4. （调试）`tools/smoke_test.py`、`tools/cdp.py`

**DB 访问分散：**

1. `crawler.open_db` + 大量内联 SQL（写路径）
2. `board_api.BoardDB`（读路径，`query_only=ON`）

`wpy-favorites` 与 `wpy-sitewide` **无 import 级依赖**；仅文档交叉引用。

---

## 3. 当前入口

### CLI（必须保持）

```bash
cd wpy-sitewide
python crawl.py all|resume|partial|update|status|probe|list|body|comment|image|export|build|verify|diagnose|logout|whoami|reopen|serve
python -m wpy <同上>
```

### Bat

- `wpy-sitewide/一键抓取.bat` → 菜单调用 `python crawl.py ...`（含 `chcp 65001`）
- `wpy-sitewide/打开看板.bat` / `open_board.bat` → `python -m wpy serve ...`

### 测试入口

```bash
cd wpy-sitewide
python tools/smoke_test.py
```

**注意**：任务书中写的 `python tools/smoke_test.py` 相对路径，是在 `wpy-sitewide/` 下执行；项目根 `tools/` 没有 smoke_test。

---

## 4. 当前已知问题（任务书 + 本次核实）

| # | 问题 | 核实状态 | 风险 |
|---|---|---|---|
| 1 | `crawler.py` 2469 行 God Module | 已核实 | 高（可维护性） |
| 2 | HTTP 三处重复（crawler/auth/server） | 已核实 | 中 |
| 3 | DB schema/CRUD 与抓取耦合在 crawler | 已核实 | 中 |
| 4 | `wpy-favorites` 待废弃 | 文档仍多处引用；无代码依赖 | 低 |
| 5 | Windows cp1252 下 serve 可能 `UnicodeEncodeError` | **本基线环境未复现**（PYTHONUTF8=1 下 smoke 全过）；机制上仍存在（`server.py` 打印中文，未统一 reconfigure stdout） | 中（环境相关） |
| 6 | 任务书称 `http serve suite` 已知失败 | **本次基线 smoke 全部 PASS**（含 http serve suite） | 记录差异，后续以复跑为准 |
| 7 | `_fix_user.py` 引用不存在的 `wpy-sitewide/dashboard.py` | 已核实（历史一次性补丁） | 低（死文件） |
| 8 | 根 `tools/` 与 `wpy-sitewide/tools/` 两套工具目录 | 已核实 | 低 |
| 9 | `ui.py` 2319 行 | 已核实 | 中（优先级低于 crawler） |
| 10 | 双 README + 复用手册交叉描述 favorites | 已核实 | 低 |

---

## 5. 必须保留的机制（清单）

| 机制 | 实现位置（现状） | 约束 |
|---|---|---|
| 断点续抓 | `scrape_progress` + `do_list`/`do_resume` | `resume` 行为不变 |
| 全局 QPS | `RateLimiter` / `LIMITER` | 禁止回退 per-thread sleep |
| 429 降速 | `Throttled` → `LIMITER.penalize` | 保留 |
| 熔断 | `Fuse` + `BadRequest` 立刻 force_stop | 400 ≠ 普通网络错误 |
| 分页漂移 | `pagination_events` + `_record_pagination_event` | 表与语义保留 |
| 运行状态/日志 | `RUN_STATE_PATH` / `LOG_PATH` | 文件名与内容格式保留 |
| CLI / bat / crawl.py | 见上 | 命令与参数保持 |
| 业务默认值 | QPS=10、FUSE=12、并发=6 等 | 不得擅自改 |
| 数据库 | `data/sitewide.db` | 禁止 DROP / 重建 |

---

## 6. 数据库基线（重构前）

路径：`wpy-sitewide/data/sitewide.db`（约 161,681,408 bytes）

| 表 | 行数 |
|---|---:|
| posts | 202600 |
| floors | 212566 |
| official_replies | 2625 |
| images | 0 |
| scrape_progress | 13 |
| pagination_events | 21 |
| essence_ids | 198 |

`status --brief`：`已抓 202600 帖（正文 202588）｜评论 65248 帖 / 212566 楼｜板块 8/13 完成`

---

## 7. 当前测试结果（基线）

执行环境：

- Python：`3.12.10`
- 环境变量：`PYTHONUTF8=1`
- 工作目录：`wpy-sitewide/`

| 检查 | 命令 | 结果 |
|---|---|---|
| Python compile | `python -m compileall -q wpy crawl.py tools` | **PASS** |
| Smoke | `python tools/smoke_test.py` | **PASS / ALL SMOKE PASS (exit 0)** |
| CLI help | `python crawl.py --help` | **PASS (exit 0)** |
| CLI status | `python crawl.py status --brief` | **PASS (exit 0)** |
| DB 可读 | sqlite counts | **PASS** |

Smoke 明细（全部 PASS）：

```text
import package / build html / init_info / posts page / search known user
user 1 / list imgs field / concurrent queries
healthz / token required / api init with token / api posts / api user
api post detail / healthz no auth
```

### 与任务书的差异（如实记录）

任务书预期 `http serve suite` 失败；**本次基线该套通过**。可能原因：任务书基于更早快照，或当时未设 `PYTHONUTF8`。后续 Phase 6 需在**不依赖 PYTHONUTF8 的 cp1252 模拟**下复测编码问题。

---

## 8. Git / 文件差异摘要（Phase 0 开始时）

Phase 0 **未修改业务代码**，仅新增本基线文档与 `docs/refactor/` 报告。

- 本目录无独立 `.git`；归属 `D:/Documents/AI-System`
- 上一次提交：`85c75dc feat: add snatch-wpy Weipeiyang forum tools to version control`
- 仓库存在大量其它路径未提交变更（与本重构无关，不纳入本次范围）
- 会话尝试创建隔离 worktree `agents/snatch-wpy-refactor`：**分支已创建**；**worktree add 被隔离策略拦截**（见 §9）

---

## 9. 执行环境限制（交接必读）

1. **Auto-Worktree 隔离**：会话要求在独立 worktree 中改主仓库，但 `git worktree add` 被子会话策略拒绝（判定为共享 ref 变更）。  
   - 已创建本地分支 `agents/snatch-wpy-refactor`（基于 main `85c75dc`）。  
   - 后续写操作被限定在 `Projects/snatch-wpy/` 路径内；未切换主工作区 HEAD。  
2. 若 orchestrator 允许，应在独立 worktree 中执行 Phase 1+ 的删除与重构提交；当前以路径内改文件 + 仅暂存 snatch-wpy 为回退策略。
3. `_backup/`、真实 `data/sitewide.db` **未做任何写入**。

---

## 10. Phase 0 产出

| 文件 | 说明 |
|---|---|
| `REFACTOR_BASELINE.md` | 本文件：重构前总基线 |
| `docs/refactor/phase_00_baseline.md` | 标准化阶段交接报告 |

---

## 11. 下一阶段（Phase 1）入口条件

- [x] 目录与入口已摸清  
- [x] `wpy-favorites` 依赖搜索完成（无生产 import）  
- [x] compileall / smoke / CLI 基线 PASS  
- [x] DB 行数已记录  
- [ ] Phase 1：删除 `wpy-favorites/` + 清理 README/docs 引用 + 复跑测试  
