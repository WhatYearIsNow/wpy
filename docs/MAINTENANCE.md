# 开发与维护约定

WPY 保持 Python 3.10+ 标准库运行时、SQLite 和原生 HTML/CSS/JavaScript。
整理不改变公开接口、配置名、数据格式、执行顺序、错误处理、重试和时序。

## 一次准备

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
```

Windows 使用 `.venv\Scripts\python.exe`；发布切换工具仅支持本项目现有 Linux/systemd 部署。
开发依赖和间接依赖固定在 `requirements-dev.lock`，构建后端也固定版本。
升级工具时同步 `pyproject.toml` 和锁文件，通过 Python 3.10 / 3.13 CI 后合并。

## 日常入口

```bash
.venv/bin/python scripts/dev.py serve     # http://127.0.0.1:18765/
.venv/bin/python scripts/dev.py hot       # http://127.0.0.1:18766/
.venv/bin/python scripts/dev.py build
.venv/bin/python scripts/dev.py test      # pytest + 离线流水线
.venv/bin/python scripts/dev.py check     # 格式 + 语法 + 测试 + 隐私/历史检查
.venv/bin/python scripts/dev.py format    # 只修格式，不修业务、不排序导入
```

预览使用 `.local/dev/{data,build,logs,pages}`，默认空库；测试使用另一临时目录，结束即清理。
入口在导入 WPY 前移除继承的全部 `WPY_*` 配置与 `PYTHONPATH`，分别设置路径和新的临时开发口令。
不加载正式环境、SMTP、模型配置或账号，不复制正式数据库；预览只监听本机，不接入正式 Caddy。
不设置论坛凭据，不从此入口启动抓取、通知或 AI 任务。`build` 用原有流程生成空库看板与热榜。
正式热榜的部分 API 转发固定指向 8765，故开发入口为热榜额外启动独立空库后端，并仅在预览进程内
覆盖代理目标，防止预览请求进入正式看板；退出热榜预览时一并停止此后端。
端口占用时显式传 `--port`，不要停止别的项目；正式的 8765/8766 在开发入口中禁止使用。
远程预览使用 SSH 本地转发，不开放新的公网监听或修改校园网边界。

## 代码规则与例外

- UTF-8、LF、末尾换行；Python 四空格、双引号、目标行宽 100，由 Ruff 0.14.14 执行。
- 模块/函数/变量用 `snake_case`，类用 `PascalCase`，常量用 `UPPER_SNAKE_CASE`。
  不为统一命名更改公开接口、SQL 列、配置项或 JSON 字段。
- 不启用导入排序或删除导入：`config.py` 在导入时创建目录，现有导入顺序可能有副作用。
  现有独立脚本的路径引导保留；不为整理引入框架、迁移模块或提取语义不同的公共函数。
- 注释说明输入输出、单位、边界和副作用；错误处理沿用各模块已有边界，不统一吞异常。
- JS/CSS 继续使用现有两空格约定。本批不批量改写前端、内嵌 HTML 或字符串，前端资源逐字节保持不变，
  不引入 Node 工具链。今后更改前端需单独验证界面和交互。
- Caddy 沿用制表符；Markdown 允许用于换行的行尾空格。`.editorconfig` 提供编辑器规则。
- 本批开始时存在未提交的 `wpy/ai_agent.py`、`wpy/ai_agent_cli.py`、`wpy/ai_agent_mail.py`、
  `tests/test_ai_agent.py`，暂从 Ruff 中排除。维护者完成该批功能后，单独格式化、验证并移除排除项。
- 不处理运行数据、生成物、第三方代码或原始样本；生成物通过原有构建流程更新。
  `.local/`、运行数据、日志、缓存、环境文件和机器配置不提交；源码导出包含工具配置与锁文件。

机械整理单独提交，结构/业务改动另开提交。本批机械整理以完整 Python AST 对照为依据，
包括字符串、导入次序和异常结构，不只依赖测试通过。

## 分支、PR 和 CI

1. 从最新 `main` 建短分支 `feat/...`、`fix/...` 或 `chore/...`，保留已有未提交改动。
2. PR 说明问题、行为、验证和限制。机械格式化、工具/流程、业务功能分别提交。
3. Actions 在 Python 3.10 / 3.13 检查格式、语法、测试、离线流水线、隐私/历史、sdist/wheel 安装。
   CI 无正式密钥、无正式数据、无部署步骤，不发邮件、抓真实论坛或执行 AI 写入。
4. 两个矩阵检查通过后人工审阅再合并。隐私失败不能通过放宽规则、提交个人邮箱或接回旧历史解决。
   GitHub 合并身份须使用隐私邮箱；本地项目提交身份使用 `.invalid` 域名。
5. 合并不部署；发布选择已审阅的完整提交 SHA，见[发布与回滚](RELEASES.md)。

本次不在远端强设分支保护、自动合并或可见性。远端实际公开，与入口中的“私有”历史表述冲突；
提交继续按公开标准检查，可见性变更由维护者单独决定。

## 初学者阅读路线

1. `README.md` → 本文：先用空库预览和离线测试认识程序。
2. `wpy/__main__.py` → `cli.py` → `crawler/cli_app.py`：命令分发。
3. `config.py`、`config_models.py`：路径、验证和导入副作用。
4. `db.py` → `board_api.py` → `server.py`：数据库、查询与 HTTP 路由。
5. `ui.py` → `ui_templates/board.js`、`board.css`：页面壳、接口取数与样式。
6. `tests/test_server_startup.py`、`tools/offline_stage_smoke.py`：临时库与模拟网络。
7. `crawler/pipeline.py` → 各阶段 → `http.py`：阶段顺序、限流、熔断和重试。
8. 最后看通知/AI 专用文档；它们具有外部副作用，不作为学习时的试运行入口。

先在边界清楚的模块增加小测试，再改业务；不要使用真实论坛内容制作样本。
