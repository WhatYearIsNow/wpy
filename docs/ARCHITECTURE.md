# 微北洋「青年湖底」全站看板 — 架构文档

> 最后更新：2026-09-25

## 1. 系统概述

本项目是一个基于本地 SQLite 的论坛看板系统，用于抓取、存储和浏览「微北洋」青年湖底论坛的全部帖子和评论。

**核心能力：**
- 多阶段爬虫流水线：列表 → 正文 → 评论 → 图片 → 导出 → 看板
- 本地 SQLite 数据库（20 万+ 帖子，148 万+ 评论）
- HTTP 看板服务（只读浏览 + 管理员写操作代理）
- 零第三方依赖：全部使用 Python 标准库

## 2. 架构图

```
┌─────────────────────────────────────────────────────┐
│                    CLI 入口                           │
│              python -m wpy <command>                  │
│  (serve / update / all / resume / partial / build)   │
└──────────┬──────────────────────────────┬────────────┘
           │                              │
           ▼                              ▼
┌─────────────────────┐        ┌─────────────────────┐
│   爬虫流水线          │        │   看板服务            │
│  crawler/pipeline.py │        │   server.py         │
│                      │        │   ┌───────────────┐ │
│  阶段一: list.py     │        │   │  BoardDB      │ │
│  阶段二: body.py     │        │   │  (查询层)     │ │
│  阶段三: comment.py  │        │   └───────┬───────┘ │
│  阶段四: image.py    │        └───────────┼───────┘ │
│  阶段五: export_ops  │                    │         │
│  阶段六: ui.py       │                    ▼         │
│  阶段七: essence.py  │        ┌─────────────────────┐
└──────────┬───────────┘        │   SQLite DB         │
           │                    │   sitewide.db       │
           ▼                    │   ┌───────────────┐ │
┌─────────────────────┐        │   │ posts         │ │
│   HTTP 层           │        │   │ floors        │ │
│   http.py           │        │   │ images        │ │
│   - RateLimiter     │        │   │ official_...  │ │
│   - Fuse            │        │   └───────────────┘ │
│   - retry           │        └─────────────────────┘
└──────────┬───────────┘
           │
           ▼
┌─────────────────────┐
│   认证 / 凭据        │
│   auth.py           │
│   config.py         │
└─────────────────────┘
```

## 3. 模块说明

### 核心模块

| 模块 | 职责 | 关键函数/类 |
|------|------|------------|
| `config.py` | 路径与常量定义 | `DB_PATH`, `LOG_PATH`, `CSV_PATH` |
| `config_models.py` | 配置数据模型与验证 | `ServerConfig`, `CrawlerConfig`, `validate_all()` |
| `auth.py` | 登录与凭据管理 | `get_credentials()`, `login()` |
| `http.py` | HTTP 请求、限速、熔断、重试 | `RateLimiter`, `Fuse`, `api_get()`, `request_json()` |
| `db.py` | SQLite schema、迁移、写入 | `open_db()`, `save_posts()`, `save_floors()`, `post_tuple()` |
| `runlog.py` | 日志与进度输出 | `log()`, `Ticker`, `progress()` |
| `metrics.py` | 指标收集与结构化日志 | `MetricsRegistry`, `Counter`, `Timer`, `StructuredLogger` |
| `board_api.py` | 看板查询层（BoardDB） | `BoardDB.posts()`, `BoardDB.post_detail()`, `BoardDB._where()` |
| `server.py` | HTTP 看板服务 | `Handler`, `/api/*` 路由 |
| `ui.py` | 前端页面生成 | `build_html()`, `HTML_SHELL` |
| `content_type.py` | 统一 Content-Type 映射 | `guess_type()` |

### 爬虫模块 (`crawler/`)

| 模块 | 阶段 | 职责 |
|------|------|------|
| `list.py` | 一 | 帖子列表抓取，分页遍历 |
| `body.py` | 二 | 帖子正文与图片链接补全 |
| `comment.py` | 三 | 评论与楼中楼抓取 |
| `image.py` | 四 | 图片本地化下载 |
| `export_ops.py` | 五 | CSV/JSON 导出 |
| `essence.py` | 六 | 精华集合、我的帖子、我的收藏 |
| `pipeline.py` | 编排 | 串联各阶段，处理 `all`/`resume`/`partial` |
| `shared.py` | 公用 | 板块过滤、并发 worker、ID 解析 |
| `status_view.py` | 公用 | 运行状态展示 |
| `cli_app.py` | CLI | argparse 定义 |

### 其他模块

| 模块 | 职责 |
|------|------|
| `cli.py` | 命令行入口，配置验证 |
| `lake_actions.py` | 湖底写操作代理（发帖/点赞/评论） |
| `notifications.py` | 本人消息轮询、持久化去重与待发邮件队列 |
| `notification_mail.py` | course 兼容 SMTP 配置、邮件渲染及标准库发送 |
| `notification_cli.py` | 五分钟后台检查、单次合成测试与只补发入口 |
| `ask.py` | 智能问答（AI API 集成） |
| `ai_agent.py` | 天大模型自主动作、校务区限制、目标校验、持久化去重与额度 |
| `ai_agent_cli.py` | 独立本人账号的单轮预演、真实执行、常驻启停 |
| `ui_templates/` | 前端 CSS/JS 模板 |

## 4. 数据模型

### SQLite Schema

```sql
-- 帖子表
posts (
    id INTEGER PRIMARY KEY,
    uid INTEGER,
    标题 TEXT,
    正文 TEXT,
    发布时间 TEXT,
    作者 TEXT,
    等级 INTEGER,
    头像 TEXT,
    板块ID INTEGER,
    板块名 TEXT,
    标签 TEXT,
    标签ID INTEGER,
    点赞数 INTEGER,
    收藏数 INTEGER,
    评论数 INTEGER,
    浏览量 INTEGER,
    评分 INTEGER,
    校区 INTEGER,
    变体 INTEGER,
    是否解决 TEXT,
    置顶标记 TEXT,
    图片数 INTEGER,
    图片链接 TEXT,
    正文已补 INTEGER,     -- 0=未抓, 1=已抓, -1=失败
    评论已抓 INTEGER,     -- 0=未抓, 1=已抓
    评论页数 INTEGER,
    图片已存 INTEGER,
    是我的帖子 TEXT,       -- "是"/"否"
    是我的收藏 TEXT,       -- "是"/"否"
    精华 INTEGER          -- 0/1
)

-- 评论表（含楼中楼）
floors (
    id INTEGER,
    帖子ID INTEGER,
    父楼ID INTEGER,       -- 0 表示主评论，>0 表示楼中楼
    uid INTEGER,
    作者 TEXT,
    等级 INTEGER,
    头像 TEXT,
    内容 TEXT,
    时间 TEXT,
    回复到 TEXT,
    点赞数 INTEGER,
    价值 INTEGER,
    子评论数 INTEGER,
    图片 TEXT,
    是楼主 INTEGER
)

-- 官方回复
official_replies (...)

-- 图片
images (...)

-- 精华集合
essence_ids (post_id, marked_at, source)

-- 抓取进度
scrape_progress (...)
```

## 5. 爬虫流水线

### 阶段顺序

```
list → body → comment → image → export → build
  │       │        │        │       │        │
  └───────┴────────┴────────┴───────┘        └── essence
                                              └── mine_posts
                                              └── my_favorites
```

### 断点续抓

每个阶段完成后在 `scrape_progress` 表中记录进度。重新运行时会：
1. 检查 `scrape_progress` 表
2. 从上次完成的页码继续
3. 跳过已完成的板块

### 并发模型

- 正文抓取：`run_workers(fn, n)` 多线程并发
- 评论抓取：`run_workers(fn, n)` 多线程并发
- 图片下载：`run_workers(fn, n)` 多线程并发
- 全局限速：`RateLimiter` 令牌桶，所有线程共享
- 熔断保护：`Fuse` 连续失败达上限时停止

### 限速与熔断

```
RateLimiter (令牌桶):
  target: 目标 QPS（默认 10）
  current: 当前速率（429 时减半，空闲时逐步恢复）
  acquire(): 取配额，必要时 sleep
  penalize(): 被 429 时调用，速率减半

Fuse (熔断器):
  limit: 连续失败上限（默认 12）
  ok(): 成功时重置计数
  fail(): 失败时计数 +1，达上限时 stopped=True
  check(): 已熔断时抛 FuseTripped
```

## 6. 看板服务

### 路由

| 路径 | 说明 | 鉴权 |
|------|------|------|
| `/` | 看板首页 | 游客免登（只读） |
| `/login` | 登录页 | — |
| `/logout` | 登出 | — |
| `/healthz` | 健康检查 | 免鉴权 |
| `/api/init` | 元数据 | 免鉴权 |
| `/api/posts` | 帖子列表 | 免鉴权 |
| `/api/post` | 帖子详情 | 免鉴权 |
| `/api/user` | 用户信息 | 免鉴权 |
| `/api/me` | 当前角色 | 免鉴权 |
| `/api/follow` | 关注列表 | 管理员 |
| `/api/metrics` | 指标快照 | 管理员 |
| `/api/wpy/*` | 湖底动作代理 | 管理员 |
| `/img/*` | 图片代理 | 免鉴权 |

### 查询参数

`/api/posts` 支持丰富的筛选：
- `tab`: `all`/`ess`/`other`/板块ID
- `mine`: 我的帖子
- `fav`: 我的收藏
- `unc`: 未抓评论
- `gone`: 已删除正文
- `uid`: 按用户筛选
- `d1`/`d2`: 日期范围
- `y`: 年份
- `followUids`: 关注用户
- `q`: 搜索关键词（支持 `#MP123456`、`MPID12345`）
- `so`: 排序（`new`/`old`/`hot`/`cold`/`ess`/`none`）
- `page`/`size`: 分页

## 7. 配置

### 环境变量

所有配置项通过 `WPY_*` 前缀环境变量覆盖，详见 `config_models.py`。

### 默认路径

| 路径 | 默认 | 可覆盖 |
|------|------|--------|
| 数据目录 | `data/` | `WPY_DATA_DIR` |
| 构建目录 | `build/` | `WPY_BUILD_DIR` |
| 日志目录 | `logs/` | `WPY_LOG_DIR` |
| 数据库 | `data/sitewide.db` | — |
| 结构化日志 | `logs/结构化日志.jsonl` | `WPY_LOG_DIR` |

## 8. 安全

- 凭据仅存储在 `data/凭据.json` 或环境变量
- `APP_SECRET` 通过 `WPY_APP_SECRET` 环境变量设置
- 公网部署必须设置 `WPY_BOARD_TOKEN`
- 所有 SQL 查询使用参数化（防注入）
- 图片下载有大小限制（12MB）
- 登录失败有 IP 级限流

## 9. 测试

```bash
python -m pytest tests/          # 单元测试
python tools/smoke_test.py       # 全量冒烟测试
python tools/offline_stage_smoke.py  # 离线阶段测试
```

## 10. 重构历史

详见 `docs/refactor/` 目录：
- `phase_00_baseline.md` — 基线分析
- `phase_01_security_and_config.md` — 安全与配置
- `phase_02_code_quality.md` — 代码质量
- `phase_03_stability.md` — 稳定性增强
- `phase_04_testing.md` — 测试体系
- `phase_05_observability.md` — 可观测性
