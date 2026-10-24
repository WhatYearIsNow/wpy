# 开发指南

## 环境要求

- Python 3.10+（运行时仅使用标准库）
- SQLite 3.x（系统自带）
- 测试与打包依赖通过 `dev` 额外依赖安装

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell：.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
```

## 项目结构

```
wpy-sitewide/
├── wpy/                       主包
│   ├── __main__.py            python -m wpy 入口
│   ├── cli.py                 命令分发 + 配置验证
│   ├── config.py              路径/常量（模块级副作用）
│   ├── config_models.py       配置数据模型（dataclass + 验证）
│   ├── auth.py                登录/凭据
│   ├── http.py                HTTP/限速/熔断/重试
│   ├── db.py                  SQLite schema/迁移/写入
│   ├── runlog.py              日志/进度条
│   ├── metrics.py             指标收集/结构化日志
│   ├── board_api.py           看板查询层（BoardDB）
│   ├── server.py              HTTP 看板服务
│   ├── ui.py                  前端页面生成（HTML 壳）
│   ├── ui_templates/          前端 CSS/JS
│   │   ├── __init__.py
│   │   ├── board.css
│   │   └── board.js
│   ├── lake_actions.py        湖底写操作代理
│   ├── ask.py                 智能问答
│   └── crawler/               爬虫流水线
│       ├── pipeline.py        阶段编排
│       ├── list.py            阶段一：帖子列表
│       ├── body.py            阶段二：正文补全
│       ├── comment.py         阶段三：评论抓取
│       ├── image.py           阶段四：图片下载
│       ├── export_ops.py      阶段五：导出
│       ├── essence.py         阶段六：精华/我的帖子
│       ├── shared.py          公用：过滤/并发/解析
│       ├── status_view.py     进度展示
│       └── cli_app.py         argparse 定义
├── tests/                     pytest 单元测试
├── tools/                     冒烟测试
├── scripts/                   独立运维脚本
├── deploy/                    部署配置
├── docs/                      文档
│   ├── ARCHITECTURE.md        架构文档
│   ├── OPEN_SOURCE.md         开源发布步骤
│   └── refactor/              各阶段报告
├── CHANGELOG.md               变更日志
├── LICENSE                    MIT 许可证
├── pyproject.toml             包元数据、开发依赖、pytest 配置
├── .github/workflows/         自动测试与打包验证
├── data/                      不可重建数据
├── build/                     生成物
├── pages/                     本地手册内容；公开版本仅含说明与空模板
└── logs/                      抓取日志
```

## 开发流程

### 1. 运行测试

```bash
# 所有单元测试
python -m pytest tests/ -v

# 特定模块
python -m pytest tests/test_db.py -v

# 带覆盖率（dev 依赖包含 pytest-cov）
python -m pytest tests/ --cov=wpy --cov-report=term-missing
```

### 2. 冒烟测试

```bash
# 离线阶段测试（模拟网络，数据/页面/日志全部使用临时目录）
python tools/offline_stage_smoke.py
```

`tools/smoke_test.py` 是已有数据库的集成探针，含已有数据规模断言，
会读取当前配置的数据目录，不作为空仓库的验证步骤。

### 3. 本地开发

```bash
# 起看板服务
python -m wpy serve

# 生成看板页
python -m wpy build

# 查看抓取进度
python -m wpy status
```

首次 `serve` 会初始化空数据库。看板浏览、页面生成与帮助无需联网密钥；
只有登录与抓取更新需要配置 `WPY_APP_SECRET`。

### 4. 添加新功能

1. 确定功能所属模块
2. 编写单元测试（放在 `tests/` 目录）
3. 运行 `python -m pytest tests/` 确认通过
4. 运行冒烟测试确认无回归
5. 更新 `docs/refactor/` 下的阶段报告
6. 更新 `CHANGELOG.md`

## 编码规范

### 命名

- 模块名：`snake_case`
- 类名：`PascalCase`
- 函数/变量：`snake_case`
- 私有函数：`_snake_case`
- 常量：`UPPER_SNAKE_CASE`

### 导入

- 标准库优先
- 包内相对导入：`from .config import ...`
- 禁止 `sys.path.insert`  hacks

### 错误处理

- 网络请求失败走重试 + 熔断
- 文件 I/O 失败静默处理（try/except pass）
- 配置验证在启动时执行，非致命错误用 `log_warnings()`

### 数据库

- 所有查询使用参数化（`?` 占位符）
- 禁止字符串拼接 SQL
- WAL 模式：`PRAGMA journal_mode=WAL`
- UPSERT 而非 REPLACE：保留已更新的列

### 线程安全

- 共享状态用 `threading.Lock()` 保护
- `run_workers` 异常通过列表收集后传播
- `FuseTripped` 是正常退出信号，不算错误

## 配置

### 环境变量

路径、认证和服务配置使用 `WPY_*` 前缀环境变量。抓取速率、并发和页大小
优先通过 CLI 参数设置，例如 `python -m wpy update --qps 6 --concurrency 3`。

```bash
# 认证
WPY_APP_KEY=banana
WPY_APP_SECRET=your_valid_api_secret
WPY_ACCOUNT=your_student_id
WPY_PASSWORD=your_password

# 路径
WPY_DATA_DIR=/path/to/data
WPY_BUILD_DIR=/path/to/build
WPY_LOG_DIR=/path/to/logs
WPY_PAGES_DIR=/path/to/pages

# 服务
WPY_HOST=127.0.0.1
WPY_PORT=8765
WPY_BOARD_TOKEN=your_board_token
```

上面是配置项示意，Python 不会自动加载环境文件。在终端使用 `export`（PowerShell
使用 `$env:变量名 = '值'`）设置，或按部署指南交由 systemd 的 `EnvironmentFile` 加载。
从源码运行时，默认目录位于项目根目录；安装 wheel 后使用当前用户的数据目录，
各个 `WPY_*_DIR` 仍可覆盖。

### 配置验证

启动时 `validate_all()` 默认校验本地服务与抓取配置；认证配置在联网登录前校验。
缺少 API 密钥不会阻止浏览已有数据库，登录时会提示：

```
API 登录配置错误：WPY_APP_SECRET 不能为空（或设置环境变量）
```

## 爬虫流水线

### 阶段详解

1. **list** — 遍历板块列表页，抓取帖子 ID/标题/作者/时间等骨架
2. **body** — 逐帖调详情接口，补全正文和图片链接
3. **comment** — 逐帖抓评论（含楼中楼），分页遍历
4. **image** — 下载正文中的图片到本地 `data/img/`
5. **export** — 导出 CSV/JSON（分页，5000 行/批）
6. **essence** — 抓精华集合、我的帖子、我的收藏
7. **build** — 生成看板 HTML 空壳页

### 断点续抓

每个阶段完成后在 `scrape_progress` 表记录进度。`resume` 命令从断点继续。

### 并发

- 正文/评论/图片阶段使用 `run_workers(fn, n)` 多线程
- 全局限速器 `RateLimiter` 保证整体 QPS 不超标
- 熔断器 `Fuse` 连续失败达上限时停止

## 看板服务

### 架构

```
HTTP Server (ThreadingTCPServer)
├── /                    → ui.py 生成的 HTML
├── /api/init            → BoardDB.init_info()
├── /api/posts           → BoardDB.posts()
├── /api/post            → BoardDB.post_detail()
├── /api/user            → BoardDB.user()
├── /api/topusers        → BoardDB.top_users()
├── /api/users           → BoardDB.users()
├── /api/me              → 当前角色
├── /api/follow          → 关注列表（管理员）
├── /api/metrics         → 指标快照（管理员）
├── /api/wpy/*           → 湖底动作代理（管理员）
├── /img/*               → 图片代理
├── /healthz             → 健康检查
└── /login / /logout     → 认证
```

### 图片代理

看板中图片通过 `/img/thumb/...` 和 `/img/origin/...` 代理，服务端从本地 `data/img/` 读取。取不到时返回 1x1 透明 PNG 兜底。

## 测试策略

### 单元测试（pytest）

- 测试纯函数、数据转换、状态机
- 纯函数测试不访问外网；需要文件系统或数据库的测试使用临时目录
- 放在 `tests/` 目录，`test_*.py` 命名

### 冒烟测试

- `offline_stage_smoke.py` — 隔离所有运行目录，用模拟 API 验证爬虫各阶段
- `tests/test_server_startup.py` — 使用临时数据库和本地 HTTP 验证首次启动与已有数据
- `smoke_test.py` — 依赖已有真实数据的集成探针，手动使用

## 打包与发布检查

```bash
python -m build
python scripts/check_public_source.py
```

GitHub Actions 会在 Python 3.10 和 3.13 下运行测试、离线流水线和包构建，
并在独立环境安装 wheel，核对前端资源与 CLI。`pages/` 的真实内容可能包含论坛账号、
正文和联系信息，保留在本地并单独备份。源码快照与源码发行包只包含该目录的说明和
空 `*.example.json` 模板，wheel 不包含该目录。普通看板不需要这些本地手册内容即可启动。

首次发布前按 [开源发布说明](OPEN_SOURCE.md) 导出源码快照，避免带上现有开发历史。

### 测试原则

- 测试代码与生产代码分离
- 测试不修改生产数据
- 测试失败不继续后续阶段
