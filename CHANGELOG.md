# CHANGELOG

所有重要变更记录在此文件。

## [Unreleased]

### 个人互动邮件提醒

- 新增 `notify` 命令，为显式配置的本人账号检测帖子/评论获赞、帖子被评论和评论被回复。
- 常驻默认每五分钟检查；首次建立基线，独立 SQLite 原子保存去重记录和待发队列，失败退避补发。
- 邮件使用标准库 SMTP，兼容既有 `user/pass/to` 配置和 course 邮件样式；不增加面板通知入口。
- 增加合成测试邮件、只补发模式、独立服务模板及上游实现依据说明（Issue #5）。
- 邮件统一提供帖号、标题、正文和点赞情况，评论通知带本人原评论，回复通知带回复人和详情；
  只为新互动补读缺失上下文，超长内容明确标注节选。
- 发件人显示名改为「微北洋」，邮件主题直接显示点赞/回复的帖子或评论类型，不添加项目名前缀。
- 同一轮五分钟内的多条互动统一合并一封，失败补发沿用原批次，不按互动数量拆信。

### GitHub 开源准备

- 添加 MIT 许可证、Python 项目配置与 `dev` 开发依赖。
- 添加 GitHub Actions，验证单元测试、离线流水线、发行包与安装后的前端资源。
- 移除源码内置 API 密钥与热榜跳转令牌，联网登录改用环境变量。
- 离线测试隔离全部数据和生成目录，移除对真实数据库的检查。
- 首次启动看板自动创建空数据库；wheel 安装后的运行文件默认写入用户目录。
- 添加候选源码检查和不含旧 Git 历史的导出流程。

### 性能优化（首页加载 5-10s → <100ms）

**问题：** `posts` 表 20 万+ 行但只有 3 个索引，`_where()` 方法使用的 `精华`、`板块ID`、`是我的帖子`、`是我的收藏`、`发布时间` 都没有索引，导致每次查询都是全表扫描。

**修复：** 新增 7 个索引：

| 索引 | 覆盖的查询 | 优化前 | 优化后 |
|------|-----------|--------|--------|
| `idx_posts_type` | 按板块筛选 | ~200ms+ | 24ms |
| `idx_posts_essence` | 精华筛选 | ~200ms+ | <1ms |
| `idx_posts_mine` | 我的帖子 | ~200ms+ | <1ms |
| `idx_posts_fav` | 我的收藏 | ~200ms+ | <1ms |
| `idx_posts_created` | 发布时间范围 | ~200ms+ | 77ms |
| `idx_posts_type_created` | 板块+排序 | ~200ms+ | 2ms |

所有索引已加入 `db.py` 的 `open_db()`，新库自动创建，老库可通过 `sqlite3` 手动添加。

---

## [2026-09-25] — Phase 0-6 全面重构

### Phase 0: 基线分析
- 完成项目全面审计，识别 18 类问题
- 205,568 帖子 + 1,483,837 评论，数据库 412MB

### Phase 1: 安全与配置
- **P0**: `APP_KEY`/`APP_SECRET` 支持环境变量（`WPY_APP_KEY`/`WPY_APP_SECRET`）
- **P0**: SQL 注入修复 — `board_api.py` IN 子句改用参数化查询
- **P0**: 登录失败追踪内存泄漏修复 — 改为 TTL 清理
- **P1**: 新增 `config_models.py` 配置数据模型与启动验证
- **P1**: 统一 `guess_type` 实现 → `content_type.py`
- README 添加环境变量表与安全说明

### Phase 2: 代码质量
- **P1**: 拆分 `ui.py` 从 2737 行到 140 行，CSS/JS 提取到 `ui_templates/`
- **P2**: `ask.py` 移入 `wpy` 包，消除 `sys.path.insert` hack
- 消除重复代码，提升可移植性

### Phase 3: 稳定性增强
- **P3**: `run_workers` 异常传播 — 线程异常收集并重新抛出
- **P3**: `_img_locks` 字典限容 5000 条目，LRU 风格清理
- **P3**: 分页导出 — CSV 5000 行/批，避免内存溢出
- 发现并修复 `FuseTripped` 未导入的 bug

### Phase 4: 测试体系
- 新增 `tests/` 目录，145 个单元测试，全部通过
- 覆盖：`content_type`、`config_models`、`shared`、`db`、`board_api`
- 发现并修复 `content_type.py` 键名不匹配的 bug
- 安装 pytest 作为测试依赖

### Phase 5: 可观测性
- 新增 `metrics.py`：`Counter`、`Timer`、`MetricsRegistry`、`StructuredLogger`
- HTTP 层自动记录请求数、状态码、错误数、耗时
- 服务端新增 `/api/metrics` 端点
- 结构化日志写入 `logs/结构化日志.jsonl`
- 17 个 metrics 测试全部通过

### Phase 6: 文档体系
- 新增 `docs/ARCHITECTURE.md` — 完整架构文档
- 新增 `CHANGELOG.md` — 变更日志
- 各阶段报告：`docs/refactor/phase_0*.md`

## [Prior] — 初始版本
- 基础爬虫流水线（list/body/comment/image/export）
- SQLite 数据库 + 看板服务
- 前端 HTML 页面（内嵌 CSS/JS）
- 评论搜索、关注、标签筛选
