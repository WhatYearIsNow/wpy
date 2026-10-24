# Phase 0: 项目基线报告

> 生成时间: 2026-09-25
> 项目: 微北洋「青年湖底」全站看板
> 规模: 412MB SQLite 数据库，20 万+ 帖子，148 万+ 评论，2000+ 图片

---

## 1. 当前架构

### 1.1 项目结构

```
wpy-sitewide/
├── wpy/                          # 主 Python 包 (入口: python -m wpy)
│   ├── __init__.py               # 再导出 main
│   ├── __main__.py               # CLI 入口
│   ├── cli.py                    # CLI 主函数 (延迟导入 crawler)
│   ├── config.py                 # 路径/常量/全局配置 (唯一定义处)
│   ├── auth.py                   # 登录/凭据管理
│   ├── http.py                   # HTTP/限速/重试/熔断
│   ├── db.py                     # SQLite schema/迁移/写入
│   ├── runlog.py                 # 日志与进度
│   ├── board_api.py              # 看板查询 (BoardDB, 只读)
│   ├── server.py                 # HTTP 服务 (页面 + API + 图片代理 + 动作代理)
│   ├── ui.py                     # 前端 CSS/JS/页面生成
│   ├── lake_actions.py           # 湖底写操作代理 (发帖/点赞/评论)
│   └── crawler/                  # 抓取流水线 (6 个子模块)
│       ├── __init__.py           # 再导出所有公共符号
│       ├── cli_app.py            # argparse CLI 定义
│       ├── pipeline.py           # 流水线编排 (all/resume/partial/update)
│       ├── list.py               # 阶段一: 帖子骨架
│       ├── body.py               # 阶段二: 正文与图片链接
│       ├── comment.py            # 阶段三: 评论/楼中楼/官方回复
│       ├── image.py              # 阶段四: 图片本地化
│       ├── export_ops.py         # 导出 CSV/JSON/verify/reopen
│       ├── essence.py            # 精华/我的帖子/我的收藏
│       ├── shared.py             # 板块过滤/并发/计划确认
│       ├── status_view.py        # 进度展示
│       └── cli_app.py            # argparse 定义
├── scripts/                      # 独立运维脚本
│   ├── build_hot.py              # 生成热榜页 hot.html
│   └── refresh_stats.py          # 刷新旧帖统计/重抓评论
├── tools/                        # 测试与探针
│   ├── smoke_test.py             # 冒烟测试 (需联网+DB)
│   ├── offline_stage_smoke.py    # 离线阶段测试 (mock 外网)
│   ├── phase09_*.py              # 历史测试脚本
│   └── test_serve_utf8.py        # UTF-8 编码测试
├── deploy/                       # 部署文件
│   ├── README部署.md             # 部署说明
│   ├── Caddyfile                 # Caddy 反代配置
│   ├── env.board                 # 看板服务凭据 (含口令, 权限 600)
│   ├── env.update                # 更新任务凭据 (含学号密码, 权限 600)
│   ├── wpy-board.service         # systemd 看板服务
│   ├── wpy-update.timer          # systemd 定时更新
│   ├── wpy-refresh-stats.timer   # systemd 定时刷新统计
│   └── *.example                 # 凭据模板
├── data/                         # 不可重建数据
│   ├── sitewide.db               # 主库 (412MB, 20万帖/148万评论)
│   ├── img/                      # 帖子图片 (2000+ 文件)
│   ├── follows.json              # 关注列表
│   └── 运行状态.txt              # 运行状态 (当前正在抓评论)
├── build/                        # 生成物 (可重建)
│   ├── 全站看板.html             # 看板空壳页 (服务版)
│   ├── hot.html                  # 热榜
│   ├── lab.html                  # 物理实验经验手册
│   ├── 全站帖子.csv              # 导出
│   ├── 全站帖子_raw.json         # 导出
│   └── icon-*.png                # PWA 图标
├── pages/                        # lab.html 源数据
│   ├── exp_add.json
│   ├── exp_refined.json
│   └── exp_search.json
├── logs/                         # 抓取日志
│   └── 抓取日志.txt
├── .backup/                      # 历史文件备份 (无 git, 回滚用)
├── README.md                     # 项目说明
└── ask.py                        # 学习板块智能问答
```

### 1.2 模块职责

| 模块 | 职责 | 依赖 | 线程安全 |
|------|------|------|----------|
| `config.py` | 路径/常量/全局配置 | 无 (os.environ) | N/A |
| `auth.py` | 登录/凭据加载/token 获取 | config, http | 否 |
| `http.py` | HTTP 请求/限速/重试/熔断 | config, runlog | 是 (Limiter/Fuse 有锁) |
| `db.py` | SQLite schema/迁移/写入 | config, runlog | 否 (调用方负责锁) |
| `runlog.py` | 日志/进度/心跳 | config | 是 (Ticker 有锁) |
| `board_api.py` | 看板只读查询 (BoardDB) | json/re/sqlite | 是 (RLock) |
| `server.py` | HTTP 服务 (页面+API+代理) | 所有 wpy 模块 | 是 (ThreadingTCPServer) |
| `ui.py` | 前端 CSS/JS/页面生成 | config, db | 否 |
| `lake_actions.py` | 湖底写操作代理 | auth, http, config | 是 (token 缓存+锁) |
| `crawler/__init__` | 公共符号再导出 | 所有 crawler 子模块 | N/A |
| `crawler/cli_app` | argparse CLI | 各阶段函数 | N/A |
| `crawler/pipeline` | 流水线编排 | 各阶段函数 | 否 |
| `crawler/list` | 阶段一: 帖子骨架 | db, http, runlog | 否 (顺序执行) |
| `crawler/body` | 阶段二: 正文补全 | db, http, runlog | 是 (多线程+锁) |
| `crawler/comment` | 阶段三: 评论抓取 | db, http, runlog | 是 (多线程+锁) |
| `crawler/image` | 阶段四: 图片下载 | db, http, runlog | 是 (多线程+锁) |
| `crawler/essence` | 精华/我的帖子/收藏 | db, http, runlog | 否 (顺序执行) |
| `crawler/shared` | 板块过滤/并发/确认 | config, http, db | 是 (run_workers 有锁) |
| `crawler/status_view` | 进度展示 | db, runlog | 否 |

### 1.3 入口与运行方式

```
python -m wpy serve              # 看板服务 (HTTP, 默认 127.0.0.1:8765)
python -m wpy all                # 全量抓取: 列表→正文→评论→导出→看板
python -m wpy resume             # 继续上次未完成的
python -m wpy update --no-confirm # 补充抓取新帖
python -m wpy partial --types 2,3 # 按板块指定范围
python -m wpy list               # 仅阶段一
python -m wpy body               # 仅阶段二
python -m wpy comment            # 仅阶段三
python -m wpy image              # 仅阶段四
python -m wpy export             # 导出 CSV/JSON
python -m wpy build              # 生成看板 HTML
python -m wpy status             # 查看进度
python -m wpy whoami             # 查看当前账号
python -m wpy logout             # 清除凭据
python -m wpy reopen             # 重新打开已完成的板块
python -m wpy diagnose           # 诊断评论接口参数
python -m wpy probe              # 探测板块可达性

# 独立脚本
python scripts/build_hot.py      # 生成热榜
python scripts/refresh_stats.py  # 刷新旧帖统计
python ask.py "问题"             # 智能问答
```

### 1.4 数据流

```
用户命令 → cli.py → crawler/cli_app.py → pipeline.py → 各阶段函数
                                                         ↓
                                    list.py → body.py → comment.py → image.py → export_ops.py
                                                         ↓
                                                    db.py (SQLite)
                                                         ↓
                                    server.py (HTTP API) → ui.py (前端) → 浏览器
                                                         ↓
                                    lake_actions.py (写操作代理) → 湖底 API
```

### 1.5 核心调用链

1. **看板浏览**: `server.py` → `board_api.py` → `db.py` (只读 SQLite)
2. **图片浏览**: `server.py` → `get_bytes()` → 本地文件 / CDN 下载
3. **写操作代理**: `server.py` → `lake_actions.py` → `auth.py` → `http.py` → 湖底 API
4. **全量抓取**: `pipeline.py` → list → essence → body → comment → image → export → build
5. **补充抓取**: `pipeline.py` → list (since_id) → body → comment → export → build

### 1.6 外部依赖

| 依赖 | 用途 | 类型 |
|------|------|------|
| 微北洋湖底 API | 帖子列表/详情/评论/写操作 | HTTP (twt.edu.cn) |
| 微北洋图片 CDN | 帖子图片 | HTTP (qnhdpic.twt.edu.cn) |
| 天津大学 AI API | 智能问答 | HTTP (ai.tju.edu.cn) |
| SQLite | 本地数据库 | 内置 |
| Python 标准库 | HTTP/SSL/JSON/线程/日志 | 内置 |

### 1.7 配置来源

| 配置项 | 来源 | 默认值 |
|--------|------|--------|
| 数据目录 | `WPY_DATA_DIR` 环境变量 | `data/` |
| 构建目录 | `WPY_BUILD_DIR` 环境变量 | `build/` |
| 日志目录 | `WPY_LOG_DIR` 环境变量 | `logs/` |
| 页面目录 | `WPY_PAGES_DIR` 环境变量 | `pages/` |
| 看板端口 | `WPY_PORT` 环境变量 | 8765 |
| 看板主机 | `WPY_HOST` 环境变量 | 127.0.0.1 |
| 看板口令 | `WPY_BOARD_TOKEN` 环境变量 | "" |
| 账号密码 | `WPY_ACCOUNT` / `WPY_PASSWORD` | 从 `data/凭据.json` |
| 并发数 | `--concurrency` CLI 参数 | 6 |
| QPS | `--qps` CLI 参数 | 10 |
| 最大重试 | `config.MAX_RETRY` | 3 |
| 熔断阈值 | `config.FUSE_LIMIT` | 12 |

---

## 2. 测试状态

### 2.1 现有测试

| 测试文件 | 类型 | 覆盖范围 | 是否需要外网 |
|----------|------|----------|-------------|
| `tools/smoke_test.py` | 冒烟测试 | 导入/HTML构建/BoardDB/API服务 | 是 (需要DB) |
| `tools/offline_stage_smoke.py` | 离线测试 | 各阶段入口 (mock 外网) | 否 |
| `tools/test_serve_utf8.py` | 编码测试 | UTF-8 输出 | 否 |

### 2.2 测试覆盖缺口

- **无单元测试**: 没有任何 `unittest` / `pytest` 文件
- **无集成测试**: 没有测试模块间的交互
- **无回归测试**: 没有自动化回归套件
- **测试不隔离**: `offline_stage_smoke.py` 会读取真实 DB 并验证其完整性
- **无 CI**: 无持续集成配置

---

## 3. 已知问题与风险

### 3.1 架构层面

1. **`ui.py` 过大**: 2737 行，包含完整的 CSS (600行) + JS (1700行) + Python 生成逻辑。这是典型的 God Module。
2. **`server.py` 职责过多**: 包含 HTTP 路由、鉴权、图片代理、湖底动作代理、登录页面 HTML 等。
3. **硬编码敏感信息**: `config.py` 中有 `APP_KEY` 和 `APP_SECRET` 硬编码。
4. **热榜脚本硬编码 Token**: `scripts/build_hot.py` 曾包含固定跳转令牌，具体值已从公开文档移除。
5. **`ask.py` 独立于主包**: 不在 `wpy` 包内，没有 CLI 入口，无法通过 `python -m wpy ask` 调用。

### 3.2 稳定性风险

6. **全局状态**: `http.py` 的 `LIMITER` 是全局单例，`lake_actions.py` 的 `_token` 是全局缓存。多线程环境下需要锁保护。
7. **`run_workers` 过于简单**: 使用 `threading.Thread` 直接创建，没有异常传播机制，worker 异常只静默消失。
8. **`server.py` 的 `_login_fails`**: 内存态，重启即清；10 分钟窗口内失败满 8 次暂时拒绝。但 `if len(_login_fails) > 1000: _login_fails.clear()` 会误删其他 IP 的记录。
9. **`_img_locks` 无上限**: 并发下载图片时锁字典无限增长，不会清理。
10. **`server.py` 图片代理**: 没有超时后的重试机制，图片获取失败只记录日志。
11. **`refresh_stats.py` 单线程**: 刷新统计是顺序执行，没有并发控制。
12. **`export_ops.py` 全量导出**: `do_export()` 一次性 `SELECT * FROM posts`，412MB 数据库会占用大量内存。

### 3.3 安全性风险

13. **`APP_SECRET` 硬编码**: 在 `config.py` 中明文存储。
14. **热榜 Token 硬编码**: 在 `build_hot.py` 中明文存储。
15. **`ask.py` 未验证 AI API 响应**: 直接读取 `data["choices"][0]["message"]["content"]`，没有异常处理。
16. **`server.py` 的 `_read_form`**: 没有对 Content-Length 做足够校验 (虽然限制了 65536)。
17. **`board_api.py` SQL 注入防护不完整**: 虽然有 `SORT_MAP` 白名单，但 `_where()` 方法中 `IN (%s)` 直接使用拼接的字符串。

### 3.4 功能完整性

18. **`ask.py` 缺少错误处理**: 网络失败、AI API 不可用等情况没有用户友好的错误提示。
19. **`refresh_stats.py` 的 `--days` 默认值不一致**: README 说 7 天，实际代码默认 30 天。
20. **`build_hot.py` 的 `archaeology` 功能未实现**: 返回 `"status": "collecting"` 占位数据。
21. **`server.py` 的 `/api/wpy/posttypes`**: 角色检查在 `role != "admin"` 时返回 403，但 `role` 未登录时是空字符串，会被当作 admin 处理 (因为 `self.token` 非空时 `_role()` 返回 "admin")。
22. **`_ensure_utf8_stdio` 重复定义**: `cli.py` 和 `server.py` 各自导入了 `_ensure_utf8_stdio`，`server.py` 从 `cli` 导入。

### 3.5 工程化

23. **无 `requirements.txt` / `pyproject.toml`**: 完全依赖 Python 标准库，但也没有明确声明。
24. **无 `.gitignore`**: 项目没有 git 仓库，但有 `.backup/` 目录作为回滚手段。
25. **`__pycache__/` 未忽略**: 目录中存在 `__pycache__` 文件。
26. **`ask.py` 的 `sys.path.insert`**: 使用绝对路径 hack 来导入 `wpy.config`，在其他机器上可能失效。
27. **`refresh_stats.py` 同样使用 `sys.path.insert`**: 同样的问题。
28. **`export_ops.py` 中的 `guess_type`**: 与 `server.py` 中的 `guess_type` 重复实现。
29. **`export_ops.py` 中的 `do_verify`**: 使用 `random.seed(seed)` 但 `seed` 参数来自 CLI，每次抽样结果不一致。

---

## 4. 问题地图

### P0: 必须处理 (导致核心功能错误/数据损坏/安全问题)

| # | 问题 | 影响 | 修复方向 |
|---|------|------|----------|
| 13 | `APP_SECRET` 硬编码 | 安全风险 | 移至环境变量 |
| 14 | 热榜 Token 硬编码 | 安全风险 | 移至环境变量 |
| 17 | SQL 注入防护不完整 | 安全风险 | 参数化查询 |
| 8 | `_login_fails.clear()` 误删 | 安全/可用性 | 改用 TTL 清理 |

### P1: 强烈建议处理 (架构缺陷/高耦合/测试缺失)

| # | 问题 | 影响 | 修复方向 |
|---|------|------|----------|
| 1 | `ui.py` 过大 (2737行) | 可维护性 | 拆分 CSS/JS/Python 逻辑 |
| 2 | `server.py` 职责过多 | 可维护性 | 拆分路由/鉴权/代理 |
| 5 | `ask.py` 独立于主包 | 可维护性 | 纳入 CLI |
| 7 | `run_workers` 无异常传播 | 稳定性 | 线程异常收集 |
| 9 | `_img_locks` 无上限 | 稳定性 | 锁字典限容 |
| 12 | `do_export` 全量导出 | 稳定性 | 分页导出 |
| 23 | 无测试体系 | 工程质量 | 补充单元测试 |
| 28 | `guess_type` 重复实现 | 代码质量 | 统一到一处 |

### P2: 能力提升 (性能/UX/可观测性/自动化)

| # | 问题 | 影响 | 修复方向 |
|---|------|------|----------|
| 18 | `ask.py` 缺少错误处理 | UX | 异常处理 |
| 19 | `refresh_stats.py` 默认值不一致 | UX | 统一默认值 |
| 20 | `archaeology` 未实现 | 功能 | 实现或移除占位 |
| 26-27 | `sys.path.insert` hack | 可移植性 | 使用包导入 |
| 6 | 全局状态管理 | 稳定性 | 显式依赖注入 |

### P3: 长期优化

| # | 问题 | 影响 | 修复方向 |
|---|------|------|----------|
| 23 | 无依赖声明 | 工程化 | 添加 requirements.txt |
| 25 | `__pycache__/` 未忽略 | 工程化 | 添加 .gitignore |
| 29 | `do_verify` 随机种子 | 可重现性 | 固定种子或移除 |

---

## 5. 潜在风险 (长期维护视角)

1. **数据一致性**: 看板服务 (只读连接 + query_only) 与抓取 (写连接) 共用同一个 SQLite 文件。WAL 模式缓解了对立冲突，但没有显式的事务隔离策略。
2. **长时间运行**: `server.py` 使用 `http.server` (非异步)，长时间运行后内存增长不可控 (如 `_CACHE`、`_login_fails`、`_img_locks`)。
3. **网络连续失败**: 熔断器会停止所有线程，但恢复机制依赖下次手动运行。没有自动恢复能力。
4. **程序重启丢状态**: `server.py` 的 `_login_fails` 和 `_token` 缓存是内存态，重启后丢失。`lake_actions.py` 的 token 会在 6 小时后过期。
5. **模块失败级联**: 如果评论阶段失败，正文阶段的帖子仍然标记为"正文已补"，看板会显示帖子但评论显示"未抓"。没有级联回滚机制。
6. **未来扩展**: 如果要增加新板块/新接口/新数据源，需要修改多个模块。没有插件化或扩展点。
7. **另一个开发者维护**: `ui.py` 中 1700 行 JS 内嵌在 Python 字符串中，调试困难。`board_api.py` 的 SQL 拼接逻辑复杂，新增查询需要深入理解。

---

## 6. 可提升能力

1. **自动化测试**: 补充单元测试 (pytest) + CI 配置
2. **可观测性**: 结构化日志 (JSON)、指标收集、错误追踪
3. **配置管理**: 配置验证、默认值文档化、迁移工具
4. **状态恢复**: 服务崩溃后的自动恢复、断点续传增强
5. **性能**: 数据库查询缓存、图片 CDN 回源优化、导出流式化
6. **安全**: 凭据管理、输入校验增强、速率限制审计
7. **扩展性**: 插件化抓取阶段、自定义看板组件
8. **文档**: API 文档、架构文档、开发指南

---

## 7. 建议的目标架构

### 核心原则

- **保留现有核心行为和数据格式** (兼容性优先)
- **渐进式重构** (不为了"看起来高级"而重写)
- **小步可验证** (每个改动都可独立测试)

### 目标结构

```
wpy-sitewide/
├── wpy/                          # 主包
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                    # CLI 入口 (统一)
│   ├── config.py                 # 配置 (保留, 增强: 验证+文档)
│   ├── config_models.py          # [新增] 配置数据模型 (验证)
│   ├── auth.py                   # 认证 (保留)
│   ├── http.py                   # HTTP 层 (保留, 增强: 指标)
│   ├── db.py                     # 数据库层 (保留, 增强: 分页导出)
│   ├── runlog.py                 # 日志 (保留, 增强: 结构化)
│   ├── board_api.py              # 看板查询 (保留)
│   ├── server.py                 # [拆分] HTTP 服务
│   │   ├── routes.py             # [新增] 路由处理
│   │   ├── auth_middleware.py    # [新增] 鉴权逻辑
│   │   └── proxy.py              # [新增] 图片/动作代理
│   ├── ui.py                     # [拆分] 前端资源
│   │   ├── css.py                # [新增] CSS 提取
│   │   └── js.py                 # [新增] JS 提取
│   ├── lake_actions.py           # 写操作代理 (保留)
│   └── crawler/                  # 抓取流水线 (保留结构)
│       ├── __init__.py
│       ├── cli_app.py
│       ├── pipeline.py
│       ├── list.py
│       ├── body.py
│       ├── comment.py
│       ├── image.py
│       ├── export_ops.py
│       ├── essence.py
│       ├── shared.py
│       ├── status_view.py
│       └── worker_pool.py        # [新增] 线程池 (替代 run_workers)
├── scripts/                      # 独立脚本 (保留)
├── tools/                        # 测试与探针 (保留)
│   └── tests/                    # [新增] 测试目录
│       ├── conftest.py
│       ├── test_config.py
│       ├── test_db.py
│       ├── test_http.py
│       ├── test_board_api.py
│       └── test_crawler/
├── deploy/                       # 部署 (保留)
├── data/                         # 数据 (保留)
├── build/                        # 生成物 (保留)
├── docs/                         # [新增] 文档
│   └── refactor/                 # 重构报告
├── ask.py                        # 纳入 CLI (保留)
├── requirements.txt              # [新增] 依赖声明
└── .gitignore                    # [新增] Git 忽略
```

### 关键接口保持兼容

- CLI 命令不变 (`python -m wpy <cmd>`)
- 数据库 schema 不变 (增量迁移)
- HTTP API 不变 (`/api/*`)
- 环境变量不变 (`WPY_*`)
- 消息协议不变 (湖底 API 调用)

---

## 8. 分阶段改造路线

### Phase 1: 安全加固 + 配置管理 (P0)

**目标**: 消除安全风险，增强配置管理

1. 将 `APP_SECRET` 移至环境变量
2. 将热榜 Token 移至环境变量
3. 修复 `_login_fails.clear()` 误删问题
4. 增强 SQL 注入防护 (参数化查询)
5. 配置验证 (config_models.py)

**预计影响**: 低风险，纯内部改动

### Phase 2: 代码质量 + 消除重复 (P1)

**目标**: 消除明显技术债

1. 统一 `guess_type` 实现
2. 修复 `sys.path.insert` hack (ask.py, refresh_stats.py)
3. 拆分 `ui.py` (CSS/JS/Python 分离)
4. 拆分 `server.py` (路由/鉴权/代理)
5. 将 `ask.py` 纳入 CLI

**预计影响**: 中风险，需要充分测试

### Phase 3: 稳定性增强 (P1)

**目标**: 提升系统稳定性

1. 增强 `run_workers` (异常传播)
2. `_img_locks` 限容
3. 分页导出 (替代全量导出)
4. 全局状态显式化 (依赖注入)

**预计影响**: 中风险，核心改动

### Phase 4: 测试体系 (P1)

**目标**: 建立测试基础设施

1. 添加 `requirements.txt`
2. 补充单元测试 (config, db, http, board_api)
3. 增强集成测试
4. 添加 CI 配置 (可选)

**预计影响**: 低风险，纯新增

### Phase 5: 可观测性 + 能力提升 (P2)

**目标**: 提升可维护性和能力

1. 结构化日志
2. 指标收集
3. 错误追踪
4. 状态恢复增强
5. 性能优化 (缓存/流式)

**预计影响**: 中风险，需要灰度

### Phase 6: 文档 + 工程化 (P3)

**目标**: 完善文档和工程资产

1. 架构文档 (ARCHITECTURE.md)
2. 开发指南
3. API 文档
4. CHANGELOG
5. 重构报告

**预计影响**: 低风险

---

## 9. 第一阶段具体实施内容

### Phase 1: 安全加固 + 配置管理

#### 1.1 凭据环境变量化

- `config.py`: 添加 `APP_SECRET_ENV = os.environ.get("WPY_APP_SECRET")`
- `auth.py`: 优先使用环境变量，回退到硬编码值 (兼容)
- 更新 README 和部署文档
- 风险: 低，保留兼容层

#### 1.2 热榜 Token 环境变量化

- `scripts/build_hot.py`: 从 `WPY_HOT_TOKEN` 环境变量读取
- 风险: 低，独立脚本

#### 1.3 修复 `_login_fails` 误删

- `server.py`: 改用 TTL 清理，不 `clear()` 全部
- 风险: 低，单函数改动

#### 1.4 SQL 注入防护增强

- `board_api.py`: `_where()` 中 `IN (%s)` 改用参数化
- `board_api.py`: 确保所有用户输入都经过白名单/参数化
- 风险: 中，需要充分测试查询正确性

#### 1.5 配置验证

- `config_models.py`: 新增配置数据模型
- 启动时验证关键配置
- 风险: 低，纯新增

---

## 10. 验证计划

每个 Phase 完成后:

1. **Level 1**: 静态检查 (import 测试, 语法检查)
2. **Level 2**: 单元测试 (新增/修改的函数)
3. **Level 3**: 集成测试 (`offline_stage_smoke.py` 通过)
4. **Level 4**: 真实运行验证 (冒烟测试通过)

---

*报告生成完毕。请审阅后确认整体方向，我将开始实施 Phase 1。*
