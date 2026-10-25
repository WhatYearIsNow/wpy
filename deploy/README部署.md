# 云服务器部署说明（给不熟悉服务器的同学）

目标：校园网内通过 HTTPS 打开看板；游客只读，用户登录自己的微北洋账号；数据定时更新。

## 架构（3 个零件）

1. **看板服务** `python -m wpy serve` —— 游客只读，登录用户以本人身份操作论坛
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

# 4) 环境文件（应用密钥与抓取账号分别配置）
sudo cp /opt/wpy/wpy-sitewide/deploy/env.board.example /opt/wpy/wpy-sitewide/deploy/env.board
sudo cp /opt/wpy/wpy-sitewide/deploy/env.update.example /opt/wpy/wpy-sitewide/deploy/env.update
sudo nano /opt/wpy/wpy-sitewide/deploy/env.board    # 写入 WPY_APP_SECRET（不写个人账号密码）
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

校园网内浏览器打开 `https://你的域名/` 即可游客浏览，右上角“登录”使用本人的微北洋账号。
校园网访问限制继续由网关/防火墙提供；Caddy 示例自身没有配置校园网网段。

## 日常

| 操作 | 命令 |
|---|---|
| 看服务 | `systemctl status wpy-board` |
| 重启看板 | `sudo systemctl restart wpy-board` |
| 立刻更新一次 | `sudo systemctl start wpy-update.service` |
| 看更新日志 | `journalctl -u wpy-update.service -n 50` |
| 更新应用密钥 | 编辑 `deploy/env.board` 后 `systemctl restart wpy-board`（已有登录会话失效） |

## 安全提醒

- `env.update` 含学号密码，权限 600，不要同步网盘/仓库
- 全站只向校园网开放，使用 HTTPS 反代；个人登录不会绕过网络边界。
- 登录必须提供有效的 `WPY_APP_SECRET`；看板不再加载 `env.update` 中的抓取账号。
- 仅有游客和用户两种身份，不设管理员。用户操作使用本人的论坛 token，不回退到抓取账号。
- 密码不落盘；论坛 token 仅在内存中保留，默认六小时到期，退出或重启也会使会话失效。
- `data/board-identity.key` 与 `data/board-users/` 是本地个人关注数据，备份时一起保留，不提交仓库。

从共享口令版本升级时，需要将有效的 `WPY_APP_SECRET` 放入 `deploy/env.board`。
旧 `WPY_BOARD_TOKEN`、`WPY_HOT_TOKEN` 和 URL `?token=` 不再授权；旧会话需要个人重新登录。
旧 `data/follows.json` 属于原共享账号，不自动转给任何用户；原文件保持不动。
同步代码后必须重建看板与热榜 HTML，再重启服务，以免旧前端继续使用旧角色与接口。
这些是部署步骤说明，不代表开发任务自动执行部署。

## 本机先自测（不上服务器）

```bash
cd wpy-sitewide
python -m wpy serve --host 127.0.0.1 --port 8765 --no-open
# 另开窗口
curl http://127.0.0.1:8765/healthz
curl "http://127.0.0.1:8765/api/init"
```
