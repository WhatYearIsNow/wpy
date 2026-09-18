# 微北洋「青年湖底」全站看板

基于本地 SQLite 的论坛只读看板 + 可选抓取更新。  
主代码在 `wpy/` 包；`crawl.py` 是兼容入口。

## 快速开始（本机）

```powershell
# 双击
open_board.bat          # 桌面快捷方式同理
# 或
python -m wpy serve
python crawl.py serve --port 8765
```

浏览器打开 `http://127.0.0.1:8765/`。关窗口 = 停服务。

### 常用命令

```powershell
python -m wpy status          # 进度摘要
python -m wpy build           # 生成看板空壳页
python -m wpy serve --no-open # 起服务不弹浏览器
python -m wpy update --no-confirm   # 抓新帖（需凭据）
python tools\smoke_test.py    # 冒烟测试
```

## 云部署

见 `deploy/README部署.md`（Caddy 域名 + systemd 看板 + 定时 update）。

要点：

- 看板只读，默认 `127.0.0.1:8765`，前面加 Caddy 做 HTTPS
- 公网务必设 `WPY_BOARD_TOKEN`
- 定时任务用环境变量提供学号密码，不要写进代码库

## 目录

```
wpy-sitewide/
├── crawl.py           # 兼容入口
├── open_board.bat     # 桌面/本机一键看板
├── 一键抓取.bat
├── wpy/
│   ├── config.py      # 路径与常量
│   ├── auth.py        # 登录 / 凭据
│   ├── crawler.py     # 抓取流水线
│   ├── board_api.py   # SQLite 查询
│   ├── server.py      # HTTP 看板服务
│   ├── ui.py          # 前端页面
│   └── cli.py
├── deploy/            # systemd / Caddy / 说明
├── resources/         # 图标
└── data/              # 数据（gitignore）
```

## 安全

- 凭据仅本地 `data/凭据.json` 或环境变量，勿提交仓库
- 库含公开帖昵称/头像，对外分发请自行评估合规
- 看板不提供发帖/点赞，只读 GET
