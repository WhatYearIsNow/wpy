# 开源发布说明

代码采用 MIT 许可证，见根目录 `LICENSE`。开发与测试步骤见 [开发指南](DEVELOPMENT.md)。

## 当前仓库与发布源码

本地开发仓库的早期提交曾包含内置 API 密钥和固定热榜令牌。当前源码已去掉这些值，
旧提交仍保留在本地历史中。首次公开发布时，应从当前源码快照建立新的 Git 仓库，
不要直接推送现有开发仓库的历史。

```bash
python scripts/check_public_source.py
python -m pytest
python tools/offline_stage_smoke.py
python -m build
```

导出工具按源码目录白名单复制当前工作树，包含尚未提交的源码改动。它会排除 `.git/`、
数据库、图片、日志、备份、虚拟环境和真实环境文件，并在临时目录重新检查源码。
目标必须是项目目录外的新目录，不会覆盖已有目录：

```bash
python scripts/export_public_source.py --output ../wpy-public-source
```

源码检查会报告文件、行号和问题类型，不打印密钥值。它用于发现常见凭据字面量和
私有文件，不能替代对新增文件内容的审阅。`pages/` 的真实 JSON 内容可能含作者、账号、
正文和联系信息，属于本地数据；导出目录只保留 `pages/README.md`、空的
`*.example.json` 模板与部署示例，不包含真实页面内容、论坛数据库、评论或图片。

## 建立 GitHub 仓库

先检查导出目录，再在其中建立新的提交历史：

```bash
cd ../wpy-public-source
git init -b main
git add .
git diff --cached --stat
git commit -m "Initial open-source release"
```

在 GitHub 创建空仓库后，将其远程地址配置到这份新仓库，再推送 `main`。
以上步骤面向首次公开发布；本地开发仓库与其旧历史保持原样。

## 现有部署升级

源码不再提供默认 API 密钥。下次抓取或服务重启前，需要把有效的 `WPY_APP_SECRET`
写入本机 `deploy/env.update`，该文件不应提交。浏览已有数据库不需要此密钥。

热榜默认不附加跳转令牌。只有需要预授权链接时才设置 `WPY_HOT_TOKEN`，该值会嵌入
生成的 HTML；发布前应确认这符合该页面的访问方式。

这里的准备流程不会更改运行中的 systemd 服务、生产数据库或旧 Git 历史。
