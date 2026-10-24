---
name: phase_04_testing
description: Phase 4 测试体系 — pytest 单元测试，145 个测试用例全部通过
metadata:
  phase: 4
  title: 测试体系
  status: completed
---

# Phase 4: 测试体系

## 目标

为项目建立自动化单元测试体系，覆盖纯函数、数据转换、状态机等可独立测试的代码路径。

## 变更清单

### 发现并修复的 Bug

**1. `wpy/content_type.py` — `_CONTENT_TYPES` 键名不匹配**

`_CONTENT_TYPES` 的键带前导点（`.jpg`），但 `guess_type()` 提取扩展名时去掉了点（`"jpg"`），导致所有文件都回退到 `application/octet-stream`。

**修复：** 去掉键的前导点。

**2. `wpy/crawler/shared.py` — `FuseTripped` 未导入**

`run_workers()` 的 `_safe_fn()` 内引用了 `FuseTripped`，但模块顶部只导入了 `LIMITER, api_get`，导致 `NameError`。线程异常被静默吞掉，异常传播失效。

**修复：** 在 import 中添加 `FuseTripped`。

### 新增测试文件

| 文件 | 测试数 | 覆盖模块 |
|------|--------|----------|
| `tests/test_content_type.py` | 12 | `guess_type()` — 扩展名映射、边界情况 |
| `tests/test_config_models.py` | 26 | `ServerConfig`/`CrawlerConfig`/`AuthConfig` 验证、`validate_all()` |
| `tests/test_shared.py` | 39 | `_parse_ids`/`_parse_board_ids`/`_parse_since_date`/`_fmt_dur`/`crawlable_types`/`only_recommend`/`usable_types`/`Fuse` 状态机/`run_workers` 异常传播 |
| `tests/test_db.py` | 31 | `_int`/`is_essence`/`post_tuple`/`floor_tuple`/`iter_floors`/`official_row` |
| `tests/test_board_api.py` | 21 | `_quote` URL 编码/`_json_list` JSON 解析 |
| **合计** | **145** | |

### 测试覆盖的关键行为

- **配置验证**：端口范围、公网绑定需 token、min_qps ≤ target_qps、默认密钥警告
- **ID 解析**：`#MP123456`、`wpy://school_project/123456`、逗号/空白分隔、去重、中文逗号
- **熔断器**：连续失败计数、`ok()` 重置、`check()` 抛 `FuseTripped`、`force_stop()` 不覆盖已熔断状态
- **线程异常传播**：`run_workers` 正确收集并重新抛出 worker 异常，`FuseTripped` 除外
- **JSON 转换**：`post_tuple`/`floor_tuple`/`official_row` 完整字段映射与缺失字段默认值
- **楼中楼递归**：`iter_floors` 展平嵌套 `sub_floors`，空列表/None 安全
- **URL 编码**：`_quote` 安全字符透传、特殊字符 `%XX` 编码

## 测试结果

```
145 passed in 0.49s
```

离线冒烟测试 + 全量冒烟测试均通过，205,568 帖数据库完整保留。

## 后续建议

- 为 `board_api.BoardDB._where()` 添加查询构造测试（10+ 维度筛选）
- 为 `http.RateLimiter.penalize()`/`relax()` 添加状态测试（不涉及 sleep）
- 考虑使用 `unittest.mock` 测试依赖网络/文件系统的函数
- 长期目标：CI 中自动运行 `pytest`
