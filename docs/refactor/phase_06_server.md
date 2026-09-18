# Phase 06 交接报告：修复 server / CLI Windows UTF-8

## 1. 当前阶段

Phase 06：Windows UTF-8 输出与 serve 可启动性

## 2. 本阶段目标

```text
目标：
在 cp1252 等非 UTF-8 控制台编码下，python -m wpy serve / crawl.py serve
打印中文横幅不再 UnicodeEncodeError，smoke 的 http serve suite 可稳定通过。
```

## 3. 实际完成内容

### 修改

- `wpy/cli.py`：新增 `_ensure_utf8_stdio()`；`main()` 入口先调用
  - 已是 utf-8/cp65001 则不动
  - 否则尝试 `sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")`
  - 失败静默，不阻断启动
- `wpy/server.py`：`serve()` 开头调用 `_ensure_utf8_stdio()`
- `tools/test_serve_utf8.py`：在 `PYTHONIOENCODING=cp1252` 下起 serve 并打 healthz

### 未改

- 看板 HTTP API、token、图片代理逻辑
- 日志文件仍按 `encoding="utf-8"` 写入 `data/抓取日志.txt`（与 stdout 策略独立）

## 4. 架构变化

```text
重构前：

serve() ──► print(中文) ──► cp1252 stdout ──► UnicodeEncodeError ──► 线程退出

重构后：

cli.main / server.serve
    └── _ensure_utf8_stdio()  # 安全 reconfigure
            └── print 中文正常
```

## 5. 行为变化检查

```text
CLI 子命令与参数：否
数据库：否
抓取 QPS/参数：否
看板 API 路径与鉴权：否
认证：否
输出编码：有变化
  - 目标：Windows 控制台输出改为 UTF-8（或 errors=replace）
  - 原因：避免中文 print 杀死 serve 线程
  - 影响：仅终端显示编码，不改业务数据
```

## 6. 测试结果

| 项目 | 结果 |
|---|---|
| compileall | PASS |
| smoke_test | PASS（含 healthz / token / api / detail） |
| tools/test_serve_utf8.py（PYTHONIOENCODING=cp1252） | PASS，stdout_encoding=utf-8，healthz 200 |
| crawl.py status --brief | PASS |
| crawl.py whoami | PASS |

## 7. 数据安全检查

```text
本阶段未修改数据库。
```

## 8. 删除内容

```text
无文件删除（新增测试脚本 tools/test_serve_utf8.py）
```

## 9. 暂未处理的问题

1. 极少数无法 reconfigure 的嵌入式终端仍可能乱码（errors=replace 兜底）
2. bat 脚本内 `chcp 65001` 与本修复互补，未改 bat
3. ui.py / 看板前端未在本阶段涉及

## 10. 风险判断

| 项 | 等级 |
|---|---|
| reconfigure 改变重定向日志文件编码 | 低（已有 try/except；文件日志仍 utf-8） |
| serve 行为回归 | 低（smoke + 专用测试通过） |
| 依赖真实公网抓取验证 | 中（本阶段未外网抓取） |

## 11. 本阶段修改文件清单

```text
新增：
  wpy-sitewide/tools/test_serve_utf8.py
修改：
  wpy-sitewide/wpy/cli.py
  wpy-sitewide/wpy/server.py
删除：
  无
```

## 12. Git / 文件差异摘要

```text
M wpy-sitewide/wpy/cli.py
M wpy-sitewide/wpy/server.py
A wpy-sitewide/tools/test_serve_utf8.py
```

## 13. 对下一阶段的建议

1. Phase 7 清理临时脚本与 `__pycache__`
2. 更新根 README 目录树（crawler 包、http/db/runlog）
3. 写 REFACTOR_CHANGELOG.md 与 docs/refactor/FINAL_REPORT.md
4. 全局搜索残留 `wpy-favorites` / 双实现
5. 将 phase 报告交给审查智能体

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
