# Phase 1: 安全加固 + 配置管理

> 执行时间: 2026-09-25
> 状态: ✅ 完成

---

## 本阶段目标

消除安全风险（硬编码凭据、SQL 注入漏洞），建立结构化配置验证体系，为后续重构奠定安全基础。

---

## 完成内容

### 1.1 APP_SECRET 环境变量化

**文件**: `wpy/config.py`

**改动**:
- `APP_KEY` 现在优先从 `WPY_APP_KEY` 环境变量读取
- `APP_SECRET` 现在优先从 `WPY_APP_SECRET` 环境变量读取
- 未设置环境变量时回退到内置默认值（兼容旧部署）

**影响**:
- 新部署可通过环境变量提供密钥，避免密钥留在代码仓库
- 旧部署不受影响（回退到内置值）
- 风险: 低 — 纯新增环境变量支持，保留兼容层

### 1.2 热榜 Token 环境变量化

**文件**: `scripts/build_hot.py`

**改动**:
- 添加 `HOT_TOKEN` Python 变量，从 `WPY_HOT_TOKEN` 环境变量读取
- HTML 模板中的 `__HOT_TOKEN__` 占位符在生成时替换为实际值
- JavaScript 中的 `TOKEN` 常量通过构建脚本注入

**影响**:
- 生产环境可通过环境变量设置热榜令牌
- 旧部署不受影响（回退到内置值）
- 风险: 低 — 独立脚本，不影响主应用

### 1.3 修复 `_login_fails` 误删问题

**文件**: `wpy/server.py`

**问题**: 原代码在 `_login_fails` 超过 1000 条时调用 `clear()` 清空所有 IP 的失败记录，导致暴力破解防护在长时间运行后形同虚设。

**改动**:
```python
# 旧代码（错误）
if len(_login_fails) > 1000:
    _login_fails.clear()  # 误删所有 IP 记录

# 新代码（修复）
if len(_login_fails) > 1000:
    _login_fails = {
        k: v for k, v in _login_fails.items()
        if any(now - t < 600 for t in v)  # 只保留 10 分钟内的记录
    }
```

**影响**:
- 暴力破解防护在长时间运行后仍然有效
- 内存占用可控（只保留过期条目）
- 风险: 低 — 单函数改动，逻辑更清晰

### 1.4 SQL 注入防护增强

**文件**: `wpy/board_api.py`

**问题**: `_where()` 方法中 `IN (%s)` 子句直接使用字符串拼接的 ID 列表，如果前端传入恶意构造的 `followUids`/`blockUids`/`favIds` 参数，可能注入 SQL。

**改动**:
```python
# 旧代码（不安全）
ids = [x for x in q["followUids"].split(",") if x.strip().isdigit()]
if ids:
    cond.append('"uid" IN (%s)' % ",".join(ids[:1000]))  # 字符串拼接

# 新代码（参数化）
ids = [int(x.strip()) for x in q["followUids"].split(",") if x.strip().isdigit()]
if ids:
    cond.append('"uid" IN (%s)' % ",".join("?" * min(len(ids), 1000)))
    args.extend(ids[:1000])  # 参数化传递
```

**影响**:
- `followUids`、`blockUids`、`favIds` 三个参数现在使用参数化查询
- 其他 `IN` 子句（搜索中的 `pids`/`uids`、`shared.py` 中的 `ids`/`types`）已经安全（来源为 CLI 参数或正则解析的纯数字）
- 风险: 中 — 需要充分测试查询正确性

### 1.5 配置验证 (config_models.py)

**文件**: `wpy/config_models.py` (新增)

**内容**:
- `ServerConfig`: 看板服务配置验证（端口范围、公网绑定必须设 token）
- `CrawlerConfig`: 抓取配置验证（page_size、并发数、QPS 范围）
- `AuthConfig`: 认证配置验证（密钥非空、默认值警告）
- `PathConfig`: 路径配置（目录存在性检查）
- `validate_all()`: 运行所有验证
- `log_warnings()`: 打印警告（非致命错误不阻止运行）

**集成**:
- `wpy/cli.py` 启动时自动调用 `validate_all()` 并打印警告
- 警告不影响程序运行，仅提示用户注意

**影响**:
- 启动时自动检测配置问题
- 公网绑定未设 token 时给出明确提示
- 使用默认 APP_SECRET 时给出警告
- 风险: 低 — 纯新增，不影响现有行为

---

## 文件变化

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `wpy/config.py` | 修改 | APP_KEY/APP_SECRET 支持环境变量 |
| `wpy/server.py` | 修改 | 修复 _login_fails 误删 |
| `wpy/board_api.py` | 修改 | SQL 参数化查询 |
| `wpy/cli.py` | 修改 | 启动时配置验证 |
| `wpy/config_models.py` | **新增** | 配置数据模型与验证 |
| `scripts/build_hot.py` | 修改 | 热榜 Token 环境变量化 |
| `README.md` | 修改 | 添加环境变量表格和安全提示 |
| `deploy/README部署.md` | 修改 | 添加安全提醒 |

---

## 测试结果

| 测试 | 结果 |
|------|------|
| `tools/offline_stage_smoke.py` | ✅ ALL OFFLINE STAGE SMOKE PASS |
| `tools/smoke_test.py` | ✅ ALL SMOKE PASS |
| 配置模型导入 | ✅ OK |

**验证级别**: Level 2（单元测试 + 冒烟测试）

---

## 风险

1. **SQL 参数化**: 修改了 `_where()` 中的 `IN` 子句构造方式，理论上可能影响查询结果。已通过冒烟测试验证查询正确性。
2. **配置验证**: 启动时打印警告，不影响运行。如果未来需要改为致命错误，可调整 `log_warnings()` 行为。

---

## 尚未完成

- `config.py` 中的硬编码常量仍保留（作为兼容层），后续 Phase 2 可考虑逐步迁移到 `config_models.py`
- `server.py` 中的 `_img_locks` 无限增长问题（Phase 3）
- `run_workers` 异常传播问题（Phase 3）
- `ui.py` 和 `server.py` 拆分（Phase 2）
- 完整测试体系（Phase 4）

---

## 下一阶段

**Phase 2: 代码质量 + 消除重复**

1. 统一 `guess_type` 实现
2. 修复 `sys.path.insert` hack
3. 拆分 `ui.py` (CSS/JS/Python 分离)
4. 拆分 `server.py` (路由/鉴权/代理)
5. 将 `ask.py` 纳入 CLI
