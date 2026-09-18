# Phase 09 交接报告：真实小流量集成验证

## 1. 当前阶段

Phase 09：真实账号小流量抓取 + API 契约核对 + 收尾审查

## 2. 本阶段目标

```text
目标：
在真实接口上验证 list/body/comment/essence/image/partial 链路可写库；
比较 sitewide.db 前后计数与 schema；
确认无 DROP、无重复 HTTP、无运行时阻断；
评估是否具备进入最终收尾条件。
禁止全量 all/resume 抓取。
```

## 3. 实际完成内容

### 执行命令（真实账号）

| 命令 | 结果摘要 |
|---|---|
| `crawl.py partial --types 2 --limit 5 --no-confirm --qps 2` | pipeline 跑通；list 因板块2=done 跳过；essence/my_posts/my_fav/body 有增量；comment 阶段在「评论数最高的 5 帖」上耗时过长（见 C） |
| `tools/phase09_api_probe.py` | posttypes/list/post/floors/floor-replys/post-replys 均 200；diagnose 各参数组合可用 |
| `crawl.py body --limit 2 --no-confirm` | PASS，补正文 1 条 |
| `crawl.py comment --ids 459704 --no-confirm --limit 1` | PASS，写入 53 楼 |
| `crawl.py list --types 2 --limit 5 --since-id <max> --no-confirm` | PASS，增量写出 4 帖并「已追平」 |
| `crawl.py image --limit 3 --no-confirm` | PASS（入口可执行）；3 张下载失败已记入 images.错误 |
| `crawl.py list --types 7 --limit 5` | 退出码 0 但未抓数：type 7 不在 posttypes 返回的 8 个板块映射内（见 C） |

### 文档 / 清理

- `_fix_user.py`：全局无代码引用（仅文档提及），**移入** `_backup/dead_scripts/_fix_user.py`（非直接粉碎删除）
- `tools/phase09_db_snapshot.py`、`tools/phase09_api_probe.py` 新增
- `offline_stage_smoke.py`：真实库检查改为「结构完整 + 计数不低于重构基线」，允许合法业务增量
- 快照：`docs/refactor/phase09_db_before.json` / `after_*.json`

## 4. 架构变化

```text
本阶段无架构重构。

验证到的真实链路：
auth → http.api_get
list(since-id) → save_posts → scrape_progress
essence → essence_ids / posts.精华
my_posts/fav → 标记列
body → update_post_body
comment → floors UPSERT + posts.评论已抓
image → images 表写失败原因
server smoke → board_api 仍只读可用

依赖：crawler/* → http + db；server → board_api + ui + http
无 crawler ↔ server 循环 import
```

## 5. 行为变化检查

```text
CLI 行为是否变化：否（命令面未改）
数据库 schema：否（列数 30/15/9/6/3/6/12 不变）
抓取 API 参数：否（diagnose 确认 floors 源码原样组合仍可用）
默认 QPS：否（显式命令传了 --qps 2/3/4）
看板 API：否（smoke PASS）
认证行为：否（凭据文件登录成功）

业务写入（合法抓取，非重构改逻辑）：
- posts +7（essence/my 列表新帖 + list 增量）
- floors +53（comment --ids）
- images +3（下载失败记录）
- cmt_ok 曾因 reset_incomplete_comments 下降后 +1（见 C）
```

## 6. 测试结果

| 项目 | 结果 |
|---|---|
| compileall | PASS |
| offline_stage_smoke | PASS |
| smoke_test | PASS（total=202607） |
| real API probe | PASS |
| list 增量写库 | PASS（+4，max_id→460273） |
| body 阶段 | PASS |
| comment --ids | PASS（53 楼） |
| image --limit 3 | 入口 PASS；0 成功 / 3 失败（CDN 文件名待核，非 NameError） |
| CLI status --brief | PASS |
| 兼容 import | PASS |

## 7. 数据安全检查

```text
DROP/重建：无
schema 变化：无
before: posts=202600 floors=212566 official=2625 pe=21 essence=198 images=0
after:  posts=202607 floors=212619 official=2625 pe=21 essence=198 images=3
body_ok: 202588 → 202591
cmt_ok: 65248 → (repair 重置后) 63518 → 63519
essence 标记 198 未丢
我的帖子 249→251，我的收藏 293→295
备份快照：docs/refactor/phase09_db_*.json
_fix_user.py：_backup/dead_scripts/
```

## 8. 删除内容

```text
_fix_user.py：移至 _backup/dead_scripts/（确认无代码引用后）
无其他生产文件删除；未恢复 favorites/crawler.py
```

## 9. 暂未处理的问题

见下方 F。

## 10. 风险判断

| 项 | 等级 |
|---|---|
| 运行时 NameError | 低（Phase8 关闭 + Phase9 真实阶段跑通） |
| comment + high-评论数 + limit 组合耗时/误重置 | **中**（行为既有，非重构引入；partial 时 repair 会标回大量帖） |
| list 对映射外 type 无提示 | 低（type7 不在 posttypes 映射） |
| image 下载失败 | 低（错误已入库；需核对 URL 规则，非阻断） |
| DB 损坏 | 低（schema 稳定，增量符合预期） |

## 11. 本阶段修改文件清单

```text
新增：
  wpy-sitewide/tools/phase09_db_snapshot.py
  wpy-sitewide/tools/phase09_api_probe.py
  docs/refactor/phase09_db_before.json / after_*.json
  docs/refactor/phase_09_real_integration.md
修改：
  wpy-sitewide/tools/offline_stage_smoke.py（真实库断言放宽为结构+下限）
移动：
  _fix_user.py → _backup/dead_scripts/_fix_user.py
未修改：
  wpy 业务算法、CLI 默认值、http/db 架构
  data 表结构
```

## 12. Git / 文件差异摘要

```text
A tools/phase09_*  docs/refactor/phase09_*  phase_09_real_integration.md
M tools/offline_stage_smoke.py
D _fix_user.py（移入 _backup/dead_scripts）
M data/sitewide.db（合法抓取增量，非 DROP）
```

## 13. 对下一阶段的建议

1. **可进入最终收尾**：以架构文档 + 本次真实小流量证据为准，不再扩大重构
2. 可选文档补充：`partial --limit` 与 comment `ORDER BY 评论数 DESC` + repair 的组合语义
3. 可选：comment CLI 增加 `--no-repair`（行为增强，非必须）
4. 可选：image 失败样本的 CDN 路径核对
5. list/comment 内剩余 SQL 是否下沉：非收尾阻断

---

## 14. 收尾复测补充（审查后）

Phase 9 报告记录的图片下载失败已定位并修复：部分历史文件名包含 `/`，原实现没有先创建目标父目录，导致 `.part` 文件写入失败。

修复：`wpy-sitewide/wpy/crawler/image.py` 在写入前创建 `os.path.dirname(path)`。

真实复测：`python crawl.py image --limit 3 --no-confirm --qps 2` → **3/3 成功**；对应 `images` 记录已为 `已有本地=1`，错误字段为空，文件实际写入 `data/img/2020_10/`、`data/img/2020_12/` 等子目录。

同时清理 `wpy/crawler/list.py` 的重复 `open_db` 导入；compileall 与 offline stage smoke 复测均 PASS。

---

=== HANDOFF ===

以下内容将交给下一位架构审查智能体。

请不要默认接受本阶段实现。

请基于：
1. 当前代码
2. 本阶段报告
3. 测试结果
4. 文件差异 / phase09_db_*.json

独立判断当前重构是否合理。

重点检查：
- 是否真正降低耦合
- 是否存在重复实现
- 是否引入循环依赖
- 是否改变业务行为
- 是否破坏数据库兼容性
- 是否存在过度工程化
- 是否仍有明显 God Module
- 是否存在临时 / 历史代码残留
- 真实抓取增量是否符合 limit / since-id / repair 语义

请输出：

A. 已确认正确的部分
B. 发现的问题
C. 必须修复的问题
D. 可以延后的问题
E. 下一阶段具体任务
F. 下一阶段执行提示词

不要输出总体评分、排名或空泛评价。

下一位智能体应当以当前真实代码为准，而不是以本报告的自我描述为准。
