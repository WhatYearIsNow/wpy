# 版本发布、健康检查与回滚

开发检出、正式代码和正式数据分开。开发从 `scripts/dev.py` 进入；正式服务继续由 systemd 托管，
关闭编辑器和 SSH 不影响运行。Caddy 由多个项目共用，不用仓库模板覆盖完整网关配置。

## 边界与配置

- 看板/热榜继续监听 `127.0.0.1:8765/8766`，沿用现有域名、路径和校园网访问边界。
- 工具只管理 `wpy-board`、`wpy-hot`、`wpy-update`、`wpy-refresh-stats`、`wpy-refresh-all`。
  不改定时周期、账号、抓取参数、其他项目或通知/AI 服务，不为了验证触发真实抓取。
- 通知和 AI 已有独立 wheel 环境，按各自文档维护。状态库、额度、去重、邮件发件箱不能随代码回退；
  重启可能发送邮件或恢复外部任务，不纳入站点发布演练。
- 正式密钥由既有 systemd 环境文件/凭据加载，不放发行目录。数据、图片、关注、页面和生成物保持既有路径。
- 候选只从指定提交导出，未提交改动不会被打包。本机 `release.json` 记录完整提交与源码摘要。
  正式发行目录由部署账号持有，服务账号只读；venv 含绝对路径，建立后不要移动。

复制 `deploy/runtime.example.json` 到忽略的 `deploy/runtime.local.json`，逐项核对实际部署后使用。
本机也可把配置放在项目工作区的 ops 目录，通过全局 `--config PATH` 指定；机器恢复位置写入本机记忆。
模板路径来自本项目已有部署，只记录路径/参数，不记录秘密。
特别核对 `stats_args`：当前日常统计刷新没有 `--all`，手动全量任务才有，不能照搬旧 unit 模板。

## 准备候选（不影响正式服务）

```bash
# REF 换成已经评审、CI 通过的完整提交 SHA；目标必须不存在。
.venv/bin/python scripts/release.py prepare --ref REF --output /opt/wpy/wpy-sitewide/releases/site-REF
```

新建 builder 安装候选提交的锁文件，运行格式/语法、测试、离线流水线和源码隐私检查，构建 sdist/wheel，
再在独立运行 venv 安装 wheel，核对 CLI 和前端资源。运行包仍只有标准库依赖。
失败不生成可激活的 `release.json`、不切换服务；保留现场，下次选新的目录。
CI 另查完整历史；本地候选导出无 Git 历史，因此只查候选源码。
构建可能访问软件包仓库；测试不使用真实论坛、SMTP、模型或正式库。

预览候选使用其 `source/scripts/dev.py` 和未占用的本机端口，不复制正式数据。
除 `/healthz` 外，检查空库首页、合成帖子列表与详情及权限边界；已有启动测试覆盖空库和合成帖子。

## 发布

1. 核对提交、CI、源码摘要和运行配置，先处理下文的数据兼容与备份要求。
2. 等抓取/统计自然结束，工具会拒绝正在运行的 oneshot；不要强停正在写库的任务。
   选择离下次 timer 足够远的窗口。工具不暂停/补跑 timer，兼容性要求包含切换边界的并发访问。
3. 执行：

```bash
sudo .venv/bin/python scripts/release.py activate /opt/wpy/wpy-sitewide/releases/site-REF --data-compatible
.venv/bin/python scripts/release.py health
systemctl show wpy-board wpy-hot -p ActiveState -p SubState -p WorkingDirectory
```

`--data-compatible` 表示已人工核对当前数据兼容性，不是自动迁移检查。
工具记录原覆盖配置与运行状态，只写专用 `50-site-release.conf`，保留其他覆盖配置和环境文件，
daemon-reload 后重启此前正在运行的两个 Web 服务。会有短暂连接中断；内存会话按原规则在重启后失效。
抓取/统计下次按原计划使用新代码，通知/AI 不重启。两个本机健康检查失败时自动恢复原覆盖配置和 Web 状态，
保留恢复收据。开发改源码或合并 PR 均不会自动上线。

## 健康检查和代码回滚

```bash
.venv/bin/python scripts/release.py health
# 使用该次发布输出的收据绝对路径。
sudo .venv/bin/python scripts/release.py rollback /absolute/path/switch-RECEIPT.json --data-compatible
.venv/bin/python scripts/release.py health
```

收据只记录覆盖配置，不含密钥。回滚核对当前覆盖配置等于该次发布，拒绝过期收据覆盖后续发布；
恢复原配置和 Web 状态，再查健康。发布/回滚共用本机文件锁。
失败时保留现场，检查专用覆盖配置和 systemd 状态，不清库或连续盲重试。

`/healthz` 仅证明 Web 可响应，不证明数据新鲜度、上游认证、SMTP 到达或 AI 轮次成功。
后台任务看脱敏数量和退出状态，禁止输出原始论坛/邮件正文或环境值。
外部网关和校园网边界另验，不能凭本机 200 声称端到端可用。

## 数据备份与兼容

本次维护不改 schema/数据格式，不恢复正式库。后续数据变更须记录新旧代码能否共同读写，
迁移能否逆转，恢复丢失哪段新增数据，以及是否可能重发邮件或论坛动作。

```bash
# 在线备份包含已提交 WAL 内容；目标须不存在，文件权限 0600。
.venv/bin/python scripts/release.py backup /absolute/path/source.sqlite3 /absolute/path/new-backup.sqlite3
```

仅在明确需要发布数据变更时对正式库执行，另备份图片、关注、私有页面和配置等非数据库资料。
此命令用只读源连接并校验 `integrity_check`，不打印内容；正式备份不得拿来做测试样本。
不能对活动 WAL 库只复制 `.db`，也不能清空通知/AI 状态绕过去重。

数据恢复不提供一键命令：先停该库所有写入者、保存当前一致性副本，再明确恢复的数据损失、
外部动作重复风险和文件归属。涉及丢失数据、不可逆迁移或其他服务时，说明具体影响并取得维护者确认。
代码回滚不会回滚数据，也不撤销已发送邮件或论坛互动。
