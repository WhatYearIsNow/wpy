# WPY · 微北洋「青年湖底」全站看板

基于本地 SQLite 的论坛看板 + 可选抓取更新。数据在库里，浏览走 Web 服务，
抓取只是可选的更新通道。

主代码在 `wpy/` 包，入口统一是 `python -m wpy <cmd>`。

WPY 由 `snatch-wpy` 演进而来，是同一项目的最新版本。
后续代码、文档和问题跟踪统一在本仓库维护。
前身版本与历史接入方式见[项目沿革](docs/PROJECT_HISTORY.md)。

## 快速开始

在已有正式服务的服务器上开发，请优先使用[隔离开发入口](docs/MAINTENANCE.md)，
它使用独立数据和 18765/18766 端口。正式[发布与回滚](docs/RELEASES.md)与开发命令分开。

需要 Python 3.10+。在项目根目录创建开发环境：

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell：.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
```

运行主体只使用 Python 标准库；`dev` 额外安装测试和打包工具。

```bash
python -m wpy serve              # 起看板服务，默认 127.0.0.1:8765
python -m wpy serve --no-open    # 不自动弹浏览器
```

浏览器打开 `http://127.0.0.1:8765/`。首次启动自动初始化空数据库，
无需账号或 API 密钥即可查看空看板。源码和发行包不附带论坛数据。
安装后也可使用 `wpy serve --no-open`，与 `python -m wpy` 等价。

需要抓取更新时，在当前终端设置 `WPY_APP_SECRET`，再运行抓取命令。
账号密码可以交互输入；服务器定时任务使用环境变量，见[部署说明](deploy/README部署.md)。
环境文件模板不会由 Python 自动加载。

### 常用命令

```bash
python -m wpy status                  # 抓取进度摘要
python -m wpy build                   # 生成看板空壳页
python -m wpy update --no-confirm     # 抓新帖（需凭据）
python -m wpy all                     # 全量：列表->正文->评论->导出->看板
python -m wpy resume                  # 接着上次没抓完的继续
python -m wpy partial --types 2,3     # 按板块/日期/Mp 号指定范围
python -m wpy notify --watch          # 本人账号互动邮件，默认每五分钟检查（需单独配置）
python -m wpy agent                   # 天大模型自主互动，默认单轮预演（需独立配置）
python tools/offline_stage_smoke.py   # 离线阶段测试（不联网）
python -m pytest tests/               # 单元测试
python scripts/check_public_source.py # 检查候选发布源码
```

`tools/smoke_test.py` 是针对已有数据库的集成探针，包含已有数据规模断言，
不适用于空仓库，也不作为 CI 检查。日常验证使用上面的单元测试与离线阶段测试。

### 文档

- [项目状态与后续接续](docs/PROJECT_STATE.md) — 当前约定、GitHub 跟踪方式与维护入口
- [架构文档](docs/ARCHITECTURE.md) — 系统架构、模块说明、数据模型
- [开发指南](docs/DEVELOPMENT.md) — 开发流程、编码规范、配置说明
- [维护约定与源码阅读路线](docs/MAINTENANCE.md) — 固定工具、隔离预览、检查和格式修复
- [发布与回滚](docs/RELEASES.md) — 固定版本、健康检查、备份和数据兼容
- [变更日志](CHANGELOG.md) — 所有重要变更记录
- [个人互动邮件提醒](docs/EMAIL_NOTIFICATIONS.md) — 五分钟检查、去重补发、配置与上游依据
- [AI 自主互动](docs/AI_AGENT.md) — 约每天十轮、校务区限制、本人账号与天大模型配置
- [部署说明](deploy/README部署.md) — Caddy + systemd 云部署
- [开源发布说明](docs/OPEN_SOURCE.md) — 源码快照、历史隔离与发布步骤

### 独立脚本

```bash
python scripts/build_hot.py       # 生成热榜页 build/hot.html
python scripts/refresh_stats.py --days 7    # 刷新旧帖的点赞/浏览/评论数
python ask.py "物理实验怎么选课"             # 学习板块智能问答（需 .tju_key）
```

## 云部署

见 `deploy/README部署.md`（Caddy 域名 + systemd 看板 + 定时 update）。

要点：

- 看板默认只监听 `127.0.0.1:8765`，前面加 Caddy 做 HTTPS
- 公网务必设 `WPY_BOARD_TOKEN`
- 定时任务用环境变量提供学号密码，不要写进代码库

## 目录

```
wpy-sitewide/
├── wpy/                   主包
│   ├── config.py          # 路径与常量（所有目录的唯一定义处）
│   ├── auth.py            # 登录 / 凭据
│   ├── http.py            # 限速、熔断、重试
│   ├── db.py              # SQLite schema / 迁移 / 写入
│   ├── runlog.py          # 日志与进度
│   ├── board_api.py       # 看板查询（BoardDB）
│   ├── server.py          # HTTP 服务：页面 + /api/* + 图片/动作代理
│   ├── ui.py              # 前端 CSS/JS/页面空壳
│   ├── lake_actions.py    # 湖底写操作代理（发帖/点赞/评论）
│   ├── cli.py             # 命令行入口
│   └── crawler/           # 抓取流水线
│       ├── list.py        #   阶段一：帖子骨架
│       ├── body.py        #   阶段二：正文与图片链接
│       ├── comment.py     #   阶段三：评论（含楼中楼）
│       ├── image.py       #   阶段四：下载图片
│       ├── export_ops.py  #   导出 CSV / JSON
│       ├── pipeline.py    #   串联各阶段（all/resume/partial/update）
│       ├── essence.py     #   精华 / 我的帖子 / 我的收藏
│       ├── shared.py      #   板块过滤、并发、计划确认
│       ├── status_view.py #   进度展示
│       └── cli_app.py     #   argparse 定义
├── scripts/               # 独立运维脚本（build_hot / refresh_stats）
├── tools/                 # 测试与探针
├── tests/                 # pytest 测试
├── deploy/                # systemd / Caddy / 部署说明
├── docs/                  # 文档
│   ├── ARCHITECTURE.md    # 架构文档
│   ├── DEVELOPMENT.md     # 开发指南
│   └── refactor/          # 各阶段重构报告
├── data/                  # 不可重建的数据
│   ├── sitewide.db        #   主库（帖子/楼层/官方回复）
│   ├── img/               #   帖子图片
│   ├── follows.json       #   关注列表
│   └── 运行状态.txt
├── build/                 # 页面与导出，清理前确认生成方式及源数据齐全
│   ├── 全站看板.html      #   看板空壳页
│   ├── hot.html           #   热榜
│   ├── lab.html           #   物理实验经验手册
│   ├── 全站帖子.csv / _raw.json
│   └── icon-*.png         #   PWA 图标
├── pages/                 # 本地手册内容；公开源码仅含说明与空 JSON 模板
├── logs/                  # 抓取日志
└── .backup/               # 历史文件备份（无 git，保留作回滚）
```

`data/` 保存数据库和图片，`pages/` 保存可能包含账号与正文的本地手册内容，两者都需要备份。
公开源码仅包含 `pages/README.md` 与空的 `*.example.json`，不包含真实 `exp_*.json`。
`build/` 保存页面和导出文件；清理前应确认生成方式与输入数据齐全，手工维护或只有一份的页面应先备份。
目录位置都能用环境变量覆盖
（`WPY_DATA_DIR` / `WPY_BUILD_DIR` / `WPY_LOG_DIR` / `WPY_PAGES_DIR`）。

从源码运行时，默认使用项目目录；安装 wheel 后，默认使用用户数据目录
（Linux 为 `${XDG_DATA_HOME:-~/.local/share}/wpy`，macOS 为
`~/Library/Application Support/wpy`，Windows 为 `%LOCALAPPDATA%\wpy`）。

## 安全

- 凭据仅本地 `data/凭据.json` 或环境变量，勿提交仓库
- 联网登录必须通过 `WPY_APP_SECRET` 环境变量提供 API 应用密钥，源码不内置密钥
- 热榜默认不携带跳转令牌；仅需要预授权跳转时设置 `WPY_HOT_TOKEN`，它会写入生成的 HTML
- 库含公开帖昵称/头像，对外分发请自行评估合规
- 看板浏览是只读 GET；发帖/点赞/评论经服务端以单一账号身份代理，
  游客只能浏览，管理员口令见 `WPY_BOARD_TOKEN`

## 环境变量

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `WPY_APP_KEY` | API 应用 Key | `banana` |
| `WPY_APP_SECRET` | API 应用 Secret（联网登录必填） | 空 |
| `WPY_HOT_TOKEN` | 热榜跳转令牌（可选，会嵌入 HTML） | 空 |
| `WPY_DATA_DIR` | 数据目录 | `data/` |
| `WPY_BUILD_DIR` | 构建目录 | `build/` |
| `WPY_LOG_DIR` | 日志目录 | `logs/` |
| `WPY_PAGES_DIR` | 页面源数据目录 | `pages/` |
| `WPY_PORT` | 看板服务端口 | `8765` |
| `WPY_HOST` | 看板服务监听地址 | `127.0.0.1` |
| `WPY_BOARD_TOKEN` | 看板访问口令（公网部署必须设置） | 空 |
| `WPY_ACCOUNT` | 微北洋学号 | — |
| `WPY_PASSWORD` | 微北洋密码 | — |

## 许可证

项目代码采用 [MIT 许可证](LICENSE)，许可文本参见 [Open Source Initiative](https://opensource.org/license/mit)。
论坛帖子、评论、图片和本地账号数据不随源码分发。
