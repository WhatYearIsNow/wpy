# 项目状态与后续接续

记录日期：2026-10-24。这里保存稳定约定和重要决策，当前分支、需求与 CI 结果以 GitHub 为准。

## 当前项目

- 唯一项目：[WPY](https://github.com/WhatYearIsNow/wpy)，开发主线为 `main`。
- 当前保持私有，维护者未来会考虑公开；只有收到明确公开指令时才改变可见性。
- `snatch-wpy` 是前身，原仓库只有一次初始导入。脱敏历史保存在 `history/snatch-wpy`，
  也已接入 `main`。详情见[项目沿革](PROJECT_HISTORY.md)。
- 前身及发布准备仓库的远端入口已合并收尾，不再维护平行项目。未经修改的原始历史仅作本机恢复资料。
- 代码采用 MIT 许可。运行主体为 Python 标准库，前端为原生 HTML/CSS/JavaScript。

## 当前已完成

- CLI 入口统一为 `python -m wpy` / `wpy`，SQLite 支持本地看板与可选抓取更新。
- API 密钥改用环境变量；无密钥仍可浏览本地数据、查看帮助和生成页面。
- 首次看板启动支持空数据库；安装包默认把运行文件放入用户可写目录。
- 测试与离线流水线使用临时数据，发行包包括前端 CSS/JS。
- 页面真实内容不随源码分发；`pages/` 仅保留说明和空模板。
- 收尾基线为 204 项测试；CI 覆盖 Python 3.10 / 3.13、离线流水线、完整历史检查、打包与安装验证。
- 已建立 `AGENTS.md` 接续入口、Issue 模板和 PR 模板，供后续开发使用。
- 2026-10-25：看板页桌面端（≥1024px）新增左侧热榜常驻栏（Issue #1 / PR #2）。
  热榜排行查询统一到 `wpy/ranking.py`，`/api/board-ranking` 支持 `board=all` 全站榜；
  窄屏与手机 APK（独立热榜页）行为不变。部署目录尚未同步本次改动。

## 仓库可见性与历史事件

- 仓库 `WhatYearIsNow/wpy` 已由维护者改为公开；此前约定为私有，公开是维护者的明确操作。
- 2026-10-24 曾有合并（0237a43）把 snatch-wpy 完整未脱敏历史接入公开 `main`
  （提交者个人邮箱、历史版内置凭据、真实帖子内容 JSON）。2026-10-25 经维护者确认，
  已把 `main` 重置回脱敏主线（8facb6c 线）。重写前的远端 main 快照保存在本机私有归档
  （`main-unsanitized-snapshot-20261025.bundle`），未脱敏历史不得再接回任何远端分支。
- 隐私检查已允许 GitHub 服务身份（`noreply@github.com`、`users.noreply.github.com`）；
  维护者账号如仍以个人邮箱生成合并提交，会被照常拦截——建议在 GitHub 账号设置开启
  "Keep my email addresses private"。

## 每次开发先核对 GitHub

优先使用 GitHub 连接器。CLI 可用时，在仓库根目录执行：

```bash
git status --short --branch
git fetch --prune origin
git log --oneline --left-right HEAD...origin/main -n 12
gh repo view --json nameWithOwner,isPrivate,defaultBranchRef
gh issue list --state open --limit 20
gh pr list --state open --limit 20
gh run list --branch main --limit 5
```

这些检查不合并代码、不部署服务，也不改变仓库可见性。遇到认证或网络问题，说明哪些
事实没有核实，先完成与其无关的工作；不要将旧结果更新为“当前成功”。

## 用 GitHub 保存动态，用文档保存决定

| 内容 | 放置位置 |
|---|---|
| 新需求、缺陷、验收标准 | Issue |
| 实现差异、关联需求、验证结果 | PR 与提交 |
| 测试、历史隐私检查、打包结果 | Actions |
| 架构、行为、协作边界的重要变化 | 本文件及相关文档 |
| 本机部署路径、恢复位置与运行资料 | 工作区本地记忆，不提交 |

本次收尾没有预设下一项功能。后续工作以维护者的具体请求或选定 Issue 为准，不从早期聊天
自动推导长期任务。定时监控若有需要，应另行约定频率和通知条件；文档本身不会启动后台任务。

## 未来公开前

在当前仓库继续检查和发布，保留已有的干净版本沿革。不要为公开而再新建一个同名项目，
也不要把原始私有历史导入当前分支。完整流程见[开源发布说明](OPEN_SOURCE.md)。

## 维护本文件

仅在约定、架构、完成范围或重要遗留问题发生变化时更新。记录决定、验证依据和关联链接，
不用记录每次命令、短暂报错或整段聊天；不写任何真实账号信息。
