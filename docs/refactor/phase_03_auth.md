# Phase 03 交接报告：整理 auth 职责边界

## 1. 当前阶段

Phase 03：整理 auth 职责边界

## 2. 本阶段目标

```text
目标：
确保 wpy/auth.py 只负责登录、token、本地凭据、环境变量、logout/whoami；
认证 HTTP 统一走 wpy/http，不散落实现。
```

## 3. 实际完成内容

本阶段与 Phase 02 连续执行：auth 的 HTTP 抽离已在 02 完成；03 做职责核验与文档固化，未再引入双实现。

### auth 现状职责（核验通过）

| 职责 | 函数 |
|---|---|
| 登录 / token 换取 | `login` |
| token 获取优先级 | `get_token` |
| 本地凭据读写 ACL | `_save_cred` / `harden_file_permissions` |
| 清理 / 查看 | `do_logout` / `do_whoami` |
| HTTP 兼容入口 | `http_json` → `http.request_json(throttle=False)` |
| 异常 | `FatalError` 再导出（定义在 http.py） |

### 修改

- `wpy/auth.py`（docstring + 接入 http，随 Phase 02）
- `docs/refactor/phase_03_auth.md`（本报告）

### 未修改

- 登录 API 参数、ticket 组装、凭据文件路径与格式
- CLI `logout` / `whoami` 行为

## 4. 架构变化

```text
重构前：

crawler / tools ──► auth.http_json ──► urllib/ssl（auth 私有实现）

重构后：

crawler ──► auth.get_token / do_logout / do_whoami
auth    ──► http.request_json (认证专用，throttle=False)
             ▲
server/tools 不直接实现登录

auth 不再包含传输层实现，只保留凭据与 token 语义。
```

## 5. 行为变化检查

```text
CLI 行为是否变化：否
数据库字段是否变化：否
抓取 API 参数是否变化：否
默认 QPS 是否变化：否
看板 API 是否变化：否
认证行为是否变化：否
  - 仍优先环境变量 WPY_ACCOUNT/WPY_PASSWORD
  - 否则 data/凭据.json
  - 否则交互输入并可选保存
  - 认证请求仍不参与抓取全局限速
```

## 6. 测试结果

| 项目 | 结果 |
|---|---|
| compileall | PASS |
| smoke_test | PASS |
| `crawl.py whoami` | PASS（读到凭据文件账号） |
| `crawl.py logout` | 未执行删除（避免动用户凭据）；代码路径审查通过 |

## 7. 数据安全检查

```text
本阶段未修改数据库。
未删除 data/凭据.json。
```

## 8. 删除内容

```text
auth 内本地 urllib/ssl 传输实现（随 Phase 02 迁入 http，非双实现）。
```

## 9. 暂未处理的问题

1. `http_json` 名称为兼容保留，审查者可评估是否改名为 `request_json`
2. `FatalError` 定义在 http、auth 再导出——审查者可评估是否应放到独立 errors 模块
3. 登录失败打印中文依赖终端编码（Phase 6 一并处理 CLI stdout）

## 10. 风险判断

| 项 | 等级 |
|---|---|
| 认证行为回归 | 低（接口与优先级未改） |
| FatalError 双入口混淆 | 低（同一类对象） |
| God Module（crawler） | 高（与 auth 无关，仍待处理） |

## 11. 本阶段修改文件清单

```text
新增：
  docs/refactor/phase_03_auth.md

修改（功能代码，实为 02 连续变更）：
  wpy-sitewide/wpy/auth.py

删除：
  无

未修改：
  crawler 业务阶段逻辑
  server / board_api / ui / db 文件（db 尚未创建）
  data/sitewide.db
```

## 12. Git / 文件差异摘要

```text
相对 Phase 01：auth.py 被改为委托 http；本报告新增。
```

## 13. 对下一阶段的建议

1. 创建 `wpy/db.py`，迁出 POST/FLOOR/IMAGE 列定义与 `open_db`/迁移/索引
2. 迁出 `save_posts` / floors / official / images / scrape_progress / pagination_events / essence 写入函数
3. crawler 保留阶段编排，SQL 调用改为 db API
4. 迁移前后比对表结构 PRAGMA 与行数
5. 复跑 smoke

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
