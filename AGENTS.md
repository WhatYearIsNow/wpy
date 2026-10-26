# WPY 开发约定

## 开始任务

- 先读 `docs/PROJECT_STATE.md`；按任务需要读取 `docs/DEVELOPMENT.md`、
  `docs/ARCHITECTURE.md`、`docs/PROJECT_HISTORY.md` 或 `docs/OPEN_SOURCE.md`。
- 若父目录同时有 `AGENTS.md` 与 `WORKSPACE_MEMORY.md`，且前者标明 WPY 工作区入口，
  读取本机记忆中的用户边界和目录地图；不将该本地文件复制进仓库。
- 本仓库是 WPY 最新版本的唯一主线。`history/snatch-wpy` 用于查看前身，不用于日常改动。
- 先检查 `git status --short --branch`，保留现有未提交改动。
- 有网络时执行 `git fetch --prune origin`，检查与远端的差异；不自动 pull、stash、reset
  或覆盖工作区。GitHub 的当前状态优先于文档里的历史快照。
- 优先使用已连接的 GitHub 工具核对仓库可见性、开放 Issue、开放 PR 和最新 CI。
  CLI 后备命令及维护方式见 `docs/PROJECT_STATE.md`。连接不可用时说明未核实项，继续独立工作。

## 用户要求

- 当前保持私有；仅在用户后续明确要求公开时才修改可见性。不要新建平行 WPY 仓库。
- 不提交账号、密码、访问令牌、学号、个人邮箱、手机号、固定真实用户标识或论坛正文。
- `data/`、真实 `pages/*.json`、日志、环境文件、密钥和原始历史备份都属于本地资料。
  `pages/*.example.json` 只能是空对象或空数组，不用真实数据填充示例。
- 不把未经脱敏的 Git 历史接回任何分支。使用通用项目提交身份，避免暴露维护者的个人邮箱。
- 在已有部署旁开发时，按工作区本地记忆区分开发检出和部署目录。代码变更不等于部署授权。
- 用户已经明确授权的本次任务继续完成；发布、部署或破坏性操作超出授权范围时再确认。

## 实现与验证

- 保持原生 Python 与原生前端结构；运行时依赖当前只有标准库。
- 开发、格式和阅读路线见 `docs/MAINTENANCE.md`；发布、备份和回滚见 `docs/RELEASES.md`。
  正式服务器上用 `scripts/dev.py` 隔离开发，不借整理运行真实抓取、发信或 AI 任务。
  既有未提交 AI 改动暂不纳入格式化，完成该批功能后单独验证并移除 Ruff 排除项。
- 联网登录需要环境变量配置；本地浏览、帮助和页面构建不依赖真实凭据。
- 先检查测试隔离，使用临时运行目录，不接触部署数据库或真实抓取接口。
- 按改动运行必要检查，通常为 `python -m pytest`、
  `python tools/offline_stage_smoke.py`、`python scripts/check_public_source.py --history`。
- `tools/smoke_test.py` 是依赖已有数据库的手动探针，不用于普通 CI 或干净环境验证。
- 打包与前端资源变更需验证发行包和安装行为。纯文档改动不必重复本机全量测试。

## GitHub 与交接

- 用 Issue 记录需求、缺陷和验收标准；用 PR/提交记录实现与验证；用 Actions 记录检查结果。
- 在受影响的文档中记录重要决定和行为变化；不要把整段聊天、原始日志或秘密写入记忆。
- 收尾时更新发生实质变化的 `docs/PROJECT_STATE.md`；本机路径与恢复资料只记入工作区本地记忆。
- 汇报提交/PR 链接、验证结果、剩余事项及未核实项，不把一次旧 CI 成功当作当前结果。
