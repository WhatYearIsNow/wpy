# 云服务器部署说明（给不熟悉服务器的同学）

目标：别人用域名打开看板；数据定时自动更新。

## 架构（3 个零件）

1. **看板服务** `python -m wpy serve` —— 只读，永远在跑
2. **反代 + HTTPS** Caddy —— 把域名指到上面的服务，并自动证书
3. **定时更新** systemd timer —— 每小时 `python -m wpy update` 抓新帖

## 你需要准备

- 一台 Linux 云服务器（Ubuntu 22.04/24.04 均可）
- 一个域名，A 记录指向服务器公网 IP
- 服务器 SSH 账号（root 或 sudo）
- 微北洋学号密码（仅写在服务器本地 env 文件，不要发到聊天记录外的地方）

## 一次性安装（SSH 登录服务器后）

```bash
# 1) 系统包
sudo apt update
sudo apt install -y python3 caddy

# 2) 应用目录与用户
sudo useradd -r -m -d /opt/wpy -s /usr/sbin/nologin wpy || true
sudo mkdir -p /opt/wpy/wpy-sitewide
sudo rsync -a --exclude data --exclude build --exclude logs --exclude __pycache__ \
  --exclude 'deploy/env.*' ./ wpy@localhost:/tmp/wpy-code/   # 或用 scp
# 实际常用：在本机执行
#   scp -r wpy-sitewide user@IP:/tmp/
#   ssh user@IP
# 然后：
sudo mv /tmp/wpy-sitewide /opt/wpy/wpy-sitewide
sudo chown -R wpy:wpy /opt/wpy/wpy-sitewide

# 3) 数据目录（先只放代码；sitewide.db 可从本机 scp 上去）
sudo mkdir -p /opt/wpy/wpy-sitewide/data
sudo chown -R wpy:wpy /opt/wpy/wpy-sitewide/data

# 4) 环境文件（口令与账密）
sudo cp /opt/wpy/wpy-sitewide/deploy/env.board.example /opt/wpy/wpy-sitewide/deploy/env.board
sudo cp /opt/wpy/wpy-sitewide/deploy/env.update.example /opt/wpy/wpy-sitewide/deploy/env.update
sudo nano /opt/wpy/wpy-sitewide/deploy/env.board    # 写入 WPY_BOARD_TOKEN=随机长口令
sudo nano /opt/wpy/wpy-sitewide/deploy/env.update   # 写入 WPY_APP_SECRET、学号密码
sudo chmod 600 /opt/wpy/wpy-sitewide/deploy/env.board /opt/wpy/wpy-sitewide/deploy/env.update

# 5) 上传数据库（在你的本机执行）
# scp "wpy-sitewide/data/sitewide.db" user@IP:/opt/wpy/wpy-sitewide/data/

# 6) systemd
sudo cp /opt/wpy/wpy-sitewide/deploy/wpy-board.service /etc/systemd/system/
sudo cp /opt/wpy/wpy-sitewide/deploy/wpy-update.service /etc/systemd/system/
sudo cp /opt/wpy/wpy-sitewide/deploy/wpy-update.timer /etc/systemd/system/
sudo cp /opt/wpy/wpy-sitewide/deploy/wpy-refresh-stats.service /etc/systemd/system/
sudo cp /opt/wpy/wpy-sitewide/deploy/wpy-refresh-stats.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wpy-board.service
sudo systemctl enable --now wpy-update.timer
sudo systemctl enable --now wpy-refresh-stats.timer

# 7) Caddy 域名
sudo cp /opt/wpy/wpy-sitewide/deploy/Caddyfile /etc/caddy/Caddyfile
# 编辑域名
sudo nano /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

## 验证

```bash
curl -s http://127.0.0.1:8765/healthz
# {"ok": true}

systemctl status wpy-board --no-pager
systemctl status wpy-update.timer --no-pager
journalctl -u wpy-board -n 20 --no-pager
```

浏览器打开 `https://你的域名/?token=你设置的口令`，之后刷新可不带 token（Cookie）。

## 日常

| 操作 | 命令 |
|---|---|
| 看服务 | `systemctl status wpy-board` |
| 重启看板 | `sudo systemctl restart wpy-board` |
| 立刻更新一次 | `sudo systemctl start wpy-update.service` |
| 看更新日志 | `journalctl -u wpy-update.service -n 50` |
| 改访问口令 | 编辑 `deploy/env.board` 后 `systemctl restart wpy-board` |

## 安全提醒

- `env.update` 含学号密码，权限 600，不要同步网盘/仓库
- 公网务必设置 `WPY_BOARD_TOKEN`，不要裸奔
- 联网登录必须提供有效的 `WPY_APP_SECRET`，代码不再提供内置默认值
- 库内含他人公开帖昵称/头像；是否适合挂公网请你自行评估合规风险
- 浏览是只读 GET；发帖/点赞/评论经服务端以单一账号身份代理，
  游客只能浏览，管理员口令即 `WPY_BOARD_TOKEN`

从旧版本升级时，在下次抓取或服务重启前，将有效的 `WPY_APP_SECRET`
写入本机 `deploy/env.update`。热榜现在默认不携带跳转令牌；确有需要时设置
`WPY_HOT_TOKEN`，并注意生成的 HTML 会包含该值。仅浏览已有数据库无需 API 密钥。

## 本机先自测（不上服务器）

```bash
cd wpy-sitewide
WPY_BOARD_TOKEN=test123 python -m wpy serve --host 127.0.0.1 --port 8765 --no-open
# 另开窗口
curl http://127.0.0.1:8765/healthz
curl "http://127.0.0.1:8765/api/init?token=test123"
```
