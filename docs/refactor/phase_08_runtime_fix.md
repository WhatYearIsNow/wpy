# Phase 08 交接报告：重构后运行时缺陷修复与离线阶段回归

## 1. 当前阶段

Phase 08：修复拆包遗漏的运行时符号，并建立阶段级离线 smoke

## 2. 本阶段目标

```text
目标：
修复审查确认的 NameError/缺 import（compileall 无法发现）；
新增不访问外网的阶段 smoke；
修正 UTF-8 回归测试的 cp1252 伪造方式；
不扩大重构范围，不改业务算法，不破坏 sitewide.db。
```

## 3. 实际完成内容

### 修复（运行时阻断）

| 文件 | 问题 | 修复 |
|---|---|---|
| `wpy/crawler/body.py` | 用 `open_db`/`threading` 未导入 | 增加 `import threading`、`from ..db import open_db` |
| `wpy/crawler/comment.py` | 同上 | 同上 |
| `wpy/crawler/essence.py` | 用 `open_db` 未导入 | `from ..db import open_db` |
| `wpy/crawler/list.py` | 缺 `open_db`；调用 `_record_pagination_event` | 导入 `open_db`；统一为 `record_pagination_event` |
| `wpy/crawler/list.py` | 用 `_parse_since_date` 未导入 | `from .shared import _parse_since_date` |
| `wpy/crawler/shared.py` | `run_workers` 用 `threading` 未导入 | `import threading` |
| `wpy/crawler/pipeline.py` | `from .ui import build_from_db` → 指向不存在的 `wpy.crawler.ui` | 改为 `from ..ui import build_from_db` |
| `wpy/crawler/cli_app.py` | `from .ui` / `from .server` 错误相对导入 | 改为 `from ..ui` / `from ..server` |

### 新增

- `tools/offline_stage_smoke.py`：临时 `WPY_DATA_DIR` + mock `api_get`/`with_retry`/`get_bytes`，真实执行 `do_list` / `crawl_essence` / `crawl_my_posts` / `do_body` / `do_comment` / `do_image` / `run_pipeline`，并只读核对真实库行数

### 修正

- `tools/test_serve_utf8.py`：先 `sys.stdout/stderr.reconfigure(encoding="cp1252")`，再调用 `_ensure_utf8_stdio()`，断言恢复 utf-8 且 `/healthz=200`
- `tools/offline_stage_smoke.py`：启动时主动调用 `_ensure_utf8_stdio()`，避免宿主控制台编码导致 smoke 在业务阶段之前退出
- `REFACTOR_BASELINE.md` 日期：2026-09-19 → 2026-09-18
- `FINAL_REPORT.md` 阶段文件描述：补全 phase_00…phase_07
- `REFACTOR_CHANGELOG.md` / phase_07 补充 Phase 8 与边界表述（「DB 基础能力下沉」而非完全 storage-only）

### 未做

- 不恢复 `wpy-favorites` / `crawler.py`
- 不改 QPS/熔断/接口参数/看板 API
- 不做算法重构、不删除 `_fix_user.py`
- 不执行真实全网抓取

## 4. 架构变化

```text
审查认定（Phase 5 实况）：

crawl.py → cli → crawler 包
                  ├── list/body/comment/essence ← 缺 open_db/threading（阻断）
                  └── pipeline/cli_app          ← 相对导入 .ui/.server 错误

Phase 8 修复后：

crawl.py → cli (+ UTF-8)
        → wpy.crawler（符号完整）
              stages ──► http.api_get / get_bytes
              stages ──► db.open_db / save_*
              pipeline ──► wpy.ui（..ui）
              cli serve ──► wpy.server（..server）

边界（修正表述）：
- HTTP：单一实现（PASS）
- DB：schema/通用写入在 db.py；阶段业务查询仍可 conn.execute（基础能力下沉，非 100% storage-only）
```

## 5. 行为变化检查

```text
CLI 行为是否变化：否（仅修复会抛 NameError 的路径）
数据库字段是否变化：否
抓取 API 参数是否变化：否
默认 QPS 是否变化：否
看板 API 是否变化：否
认证行为是否变化：否
分页事件函数：名统一为 record_pagination_event（原 .pyc/旧包内别名不存在）
```

## 6. 测试结果

| 项目 | 命令 | 结果 |
|---|---|---|
| Python compile | `python -m compileall -q wpy crawl.py tools` | **PASS** |
| 符号检查 | import body/comment/essence/list/shared | **PASS**（open_db/threading/record_pagination_event 齐全） |
| Offline stage smoke | `python tools/offline_stage_smoke.py` | **PASS**（list/essence/body/comment/image/pipeline） |
| UTF-8 回归 | `python tools/test_serve_utf8.py` | **PASS**（BEFORE cp1252 → AFTER utf-8 → healthz 200） |
| 既有 smoke | `python tools/smoke_test.py` | **PASS** |
| CLI help | `python crawl.py --help` | **PASS** |
| CLI status | `python crawl.py status --brief` | **PASS** |
| 兼容 import | `from wpy.crawler import main, api_get, do_list...` | **PASS** |

## 7. 数据安全检查

```text
sitewide.db：未修改
重构前：posts=202600 floors=212566 official=2625 pagination=21 essence=198 images=0
重构后：相同（offline smoke 只读核对 + status --brief）
测试数据：仅写入临时目录 %TEMP%/wpy_offline_smoke_*
```

## 8. 删除内容

```text
本阶段无业务文件删除。
代码中删除了错误符号调用名 _record_pagination_event（替换为已存在的 record_pagination_event）。
```

## 9. 暂未处理的问题

1. crawler 阶段模块仍直接 `conn.execute` 业务查询（边界：DB 基础下沉，非完全解耦）
2. `ui.py` 仍大文件
3. `_fix_user.py` 死脚本仍在
4. 未跑真实外网抓取（账号/时长）；offline mock 不能证明接口契约正确
5. `do_list`/`do_comment` 函数仍偏长
6. 仓库无独立 snatch-wpy git worktree

## 10. 风险判断

| 项 | 等级 |
|---|---|
| 运行时 NameError 阻断 | **低**（审查点已修 + offline smoke 覆盖） |
| 真实抓取行为回归 | 中（未外网验证） |
| 数据损坏 | 低（真实库只读） |
| DB 边界仍偏“半解耦” | 中（文档已修正表述） |
| 测试覆盖不足 | 低（已补阶段 smoke；仍非集成测试） |

## 11. 本阶段修改文件清单

```text
修改：
  wpy-sitewide/wpy/crawler/body.py
  wpy-sitewide/wpy/crawler/comment.py
  wpy-sitewide/wpy/crawler/essence.py
  wpy-sitewide/wpy/crawler/list.py
  wpy-sitewide/wpy/crawler/shared.py
  wpy-sitewide/wpy/crawler/pipeline.py
  wpy-sitewide/wpy/crawler/cli_app.py
  wpy-sitewide/tools/test_serve_utf8.py
  REFACTOR_BASELINE.md
  REFACTOR_CHANGELOG.md
  docs/refactor/FINAL_REPORT.md
  docs/refactor/phase_07_cleanup.md

新增：
  wpy-sitewide/tools/offline_stage_smoke.py
  docs/refactor/phase_08_runtime_fix.md

未修改：
  data/sitewide.db
  board_api.py / ui.py / auth.py / http.py / db.py 业务逻辑
  crawl.py / cli.py 主流程结构
  wpy-favorites（保持删除）
```

## 12. Git / 文件差异摘要

```text
M  wpy/crawler/{body,comment,essence,list,shared,pipeline,cli_app}.py
A  tools/offline_stage_smoke.py
M  tools/test_serve_utf8.py
M  REFACTOR_*.md / docs/refactor/*
```

## 13. 对下一阶段的建议

1. 将审查 A–F 结论与本报告一并交给架构审查者，确认 Phase 8 是否关闭阻断项
2. 有条件时：真实账号下 `partial --types 2 --limit 5 --no-confirm` 做小流量抓取
3. 评估是否将 list/comment 剩余业务 SQL 进一步下沉 `db.py`（可选，非必须）
4. 确认后删除 `_fix_user.py`
5. 再考虑 ui 模板拆分

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
- Phase 5 审查提出的运行时符号问题是否已全部关闭
- offline smoke 是否真实执行了阶段入口而非只做 import

请输出：

A. 已确认正确的部分
B. 发现的问题
C. 必须修复的问题
D. 可以延后的问题
E. 下一阶段具体任务
F. 下一阶段执行提示词

不要输出总体评分、排名或空泛评价。

下一位智能体应当以当前真实代码为准，而不是以本报告的自我描述为准。
