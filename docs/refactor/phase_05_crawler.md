# Phase 05 交接报告：拆分 crawler 包

## 1. 当前阶段

Phase 05：将 God Module `crawler.py` 拆为 `wpy/crawler/` 包

## 2. 本阶段目标

```text
目标：
按 list / essence / body / comment / image / pipeline / cli 拆分抓取器；
删除单文件 God Module；保持 CLI / crawl.py / 兼容 import 不变。
禁止 crawler_old + crawler_new 双实现。
```

## 3. 实际完成内容

### 新增包结构

```text
wpy/crawler/
├── __init__.py     # 兼容再导出 main / api_get / open_db / stages...
├── shared.py       # 板块映射、过滤、确认计划、解析、run_workers
├── list.py         # 列表 / probe / 分页漂移
├── essence.py      # 精华集合 + 我的帖子/收藏
├── body.py         # 正文补全
├── comment.py      # 评论 / 楼中楼 / 官方回复 / diagnose
├── image.py        # 图片本地化
├── export_ops.py   # export / verify / reopen
├── status_view.py  # status / run_state 读取
├── pipeline.py     # run_pipeline / resume / update / partial
└── cli_app.py      # argparse + main
```

### 修改

- `wpy/cli.py`：仍 `from . import crawler; crawler.main()`（现指向包）
- `wpy/server.py`：serve 入口调用 `_ensure_utf8_stdio`（Phase 6 连带）
- `docs/refactor/phase_05_crawler.md`

### 删除 / 移出

- `wpy/crawler.py` 单文件模块（备份至 `_backup/refactor_crawler_before_package/crawler.py`）
- 包内**不保留** HTTP/DB schema 第二套实现（继续 import `http`/`db`）

## 4. 架构变化

```text
重构前：

crawler.py (1690 行)
 ├── HTTP（已迁）
 ├── DB（已迁）
 ├── list/essence/body/comment/image
 ├── export/status/CLI
 └── pipeline

重构后：

crawl.py → cli.py → wpy.crawler (package)
                      ├── cli_app.main
                      │     └── pipeline.run_pipeline
                      │           ├── list.do_list / essence
                      │           ├── body.do_body
                      │           ├── comment.do_comment
                      │           └── export_ops / ui.build_from_db
                      └── 各阶段 ──► http.api_get
                                 └── db.save_*
```

## 5. 行为变化检查

```text
CLI 行为是否变化：否（子命令/参数/入口路径未改）
数据库字段是否变化：否
抓取 API 参数是否变化：否
默认 QPS 是否变化：否
看板 API 是否变化：否
认证行为是否变化：否
reopen 确认：仍需要 y
```

## 6. 测试结果

| 项目 | 结果 |
|---|---|
| compileall (wpy/crawler package) | PASS |
| smoke_test | PASS |
| crawl.py --help | PASS，子命令列表完整 |
| crawl.py status --brief | PASS，与基线文案一致 |
| 兼容 import | `from wpy.crawler import main, api_get, open_db, do_list, run_pipeline, Fuse, LIMITER` PASS |
| 模块路径 | `wpy.crawler.__file__` = `wpy/crawler/__init__.py` |

## 7. 数据安全检查

```text
sitewide.db：是否修改：否
行数：posts 202600 / floors 212566 未变
备份：旧 crawler.py 已存 _backup/refactor_crawler_before_package/
```

## 8. 删除内容

```text
删除运行时模块：
- wpy/crawler.py

保留备份：
- _backup/refactor_crawler_before_package/crawler.py

工具脚本 _split_crawler.py 仍留在 wpy-sitewide/（Phase 7 清理）
```

## 9. 暂未处理的问题

1. 部分阶段函数仍较长（do_list / do_comment）——逻辑迁出但未再算法级重构
2. `shared.estimate_work` 仍依赖 `LIMITER`（编排层，可接受）
3. `ui.py` 未拆
4. `_split_crawler.py` 临时脚本待删
5. 真实外网抓取未在本阶段执行（依赖 token/耗时；行为靠代码对照 + smoke）

## 10. 风险判断

| 项 | 等级 |
|---|---|
| 拆包时函数边界/import 漏符号 | 中（compile+smoke+help 已过） |
| 行为回归 | 中（源码按行迁出，未改算法；审查者应抽查 do_list/comment） |
| 循环依赖 | 低（package→http/db/runlog；pipeline→stages） |
| 过度拆分 | 低（按阶段职责，非 20 行拆 8 文件） |

## 11. 本阶段修改文件清单

```text
新增：
  wpy/crawler/*.py（11 个文件）
修改：
  wpy/cli.py（无逻辑变化，入口仍指向 crawler.main）
  wpy/server.py（+ UTF-8 ensure）
删除：
  wpy/crawler.py（已备份）
未修改：
  board_api.py / ui.py / auth.py / http.py / db.py / crawl.py
  data/sitewide.db
```

## 12. Git / 文件差异摘要

```text
A  wpy-sitewide/wpy/crawler/__init__.py
A  wpy-sitewide/wpy/crawler/{shared,list,essence,body,comment,image,export_ops,status_view,pipeline,cli_app}.py
D  wpy-sitewide/wpy/crawler.py
M  wpy-sitewide/wpy/cli.py
M  wpy-sitewide/wpy/server.py
```

## 13. 对下一阶段的建议

1. Phase 6：固化 UTF-8 CLI/serve，并在 cp1252 环境模拟下测 serve
2. Phase 7：删除 `_split_crawler.py`、清理 `__pycache__`、更新 README 目录树
3. 审查 do_list/comment 是否还有可下沉到 db 的纯 SQL
4. 写 REFACTOR_CHANGELOG.md 与 FINAL_REPORT.md
5. 可选：ui 模板拆分（低优先级）

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
