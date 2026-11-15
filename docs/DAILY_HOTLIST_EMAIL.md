# 每日热榜摘要邮件

每日北京时间 22:30 启动发送任务。若小时抓取任务仍在运行，邮件任务等待它结束后再读取数据库；
等待超过 45 分钟或当天抓取失败时，本次不发送并由 systemd 有界重试。停机期间错过的日程不补发。

邮件默认列最近 24 小时内按浏览量排序的前 10 帖。每条含标题、发帖人、简短正文摘录、浏览/点赞/
评论/收藏数，以及直接打开帖子详情的专用链接（`/wpy/post/<帖子ID>`），不经过热榜列表。摘要是正文开头的纯文本节选，不调用模型。
HTML 版采用约 600px 单栏卡片、北洋蓝 `#2A5A90` 和邮件客户端可用的内联样式；同时提供纯文本版。
主题以「今日热榜｜日期」标识，发件人显示名为「微北洋热榜」，邮件分类头为
`X-WPY-Mail-Category: daily-hotlist`。

任务只读本机抓取数据库，不访问论坛接口。`sent_days` 状态表按北京时间日期去重；SMTP 失败不会记为
已发送，可由 systemd 重试。数据库位于 `/var/lib/wpy-hotlist`，与个人通知去重库分开。

## 配置与命令

`/etc/wpy/hotlist.env` 只需提供 `WPY_NOTIFY_CRAWLER_DB`、`WPY_SMTP_CONFIG`、
`WPY_NOTIFY_BASE_URL` 和 `WPY_HOTLIST_STATE_DIR`；不要复制论坛账号密码或 SMTP 密码到该文件。
SMTP 密钥仍由既有 root 管理的 JSON 配置提供。

```bash
python -m wpy hotlist-mail --preview /tmp/daily-hotlist-preview.html
python -m wpy hotlist-mail --send
```

预览命令使用合成数据，不连接论坛、数据库或邮箱。`--send` 是显式真实发送模式。
systemd 服务由每日 timer 调用；不启用 `Persistent` 追补错过的日程。

发送设置和模板见 `deploy/wpy-hotlist-mail.service`、`deploy/wpy-hotlist-mail.timer` 与
`deploy/env.hotlist.example`。部署时需将服务 ExecStart 指向独立发行环境，并在核对 SMTP 配置、
看板链接和数据库只读权限后启用 timer。

## 当前部署

2026-11-15 已启用独立 systemd timer，首次计划发送时间为北京时间 22:30。启用只启动 timer，未手动发送测试邮件；服务账号只读验证了抓取库、SMTP 配置和看板预览地址。部署发行版本和本机回退路径仅记录在工作区私有记忆中。
