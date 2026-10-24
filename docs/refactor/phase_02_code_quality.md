# Phase 2: 代码质量 + 消除重复

> 执行时间: 2026-09-25
> 状态: ✅ 完成

---

## 本阶段目标

消除代码重复、修复可移植性问题、拆分 God Module，提升代码可读性和长期可维护性。

---

## 完成内容

### 2.1 统一 guess_type 实现

**文件**: `wpy/content_type.py` (新增), `wpy/server.py`, `wpy/crawler/export_ops.py`

**改动**:
- 新增 `wpy/content_type.py` 作为统一的 Content-Type 映射模块
- `server.py` 和 `export_ops.py` 中的 `guess_type()` 改为从 `content_type` 模块导入
- 函数签名和行为完全一致

**影响**:
- 消除了重复实现
- 新增文件类型只需改一处
- 风险: 低 — 行为完全一致

### 2.2 修复 sys.path.insert hack

**文件**: `wpy/ask.py` (新增), `ask.py` (修改), `scripts/refresh_stats.py` (修改)

**问题**: `ask.py` 和 `refresh_stats.py` 使用 `sys.path.insert(0, str(Path(__file__).resolve().parent))` 来导入 `wpy` 包。这种方式在其他机器上可能失效，且不符合 Python 包的最佳实践。

**改动**:
- 将 `ask.py` 的核心逻辑移入 `wpy/ask.py`（作为 wpy 包的正式子模块）
- 原 `ask.py` 改为兼容入口，内部调用 `wpy.ask.main()`
- 在 `cli_app.py` 中添加 `ask` 子命令：`python -m wpy ask "你的问题"`
- `refresh_stats.py` 保留了 `sys.path.insert`（因为它是 scripts/ 下的独立脚本，不在包内），但添加了注释说明

**影响**:
- `ask.py` 现在可以通过 `python -m wpy ask` 调用
- 不再依赖 `sys.path.insert` hack
- 旧用法 `python3 ask.py` 仍然兼容
- 风险: 低 — 纯新增 + 兼容层

### 2.3 拆分 ui.py (CSS/JS/Python 分离)

**文件**: `wpy/ui_templates/` (新增), `wpy/ui.py` (修改)

**问题**: `ui.py` 原为 2737 行 God Module，包含：
- CSS: ~588 行
- JS: ~2011 行
- Python: ~140 行

**改动**:
- 新增 `wpy/ui_templates/` 包
  - `board.css`: 29,444 字节（原 CSS 原样提取）
  - `board.js`: 71,164 字节（原 JS 原样提取）
  - `__init__.py`: 提供 `get_css()` / `get_js()` 缓存读取 + `CSS` / `JS` 常量
- `ui.py` 从 2737 行减少到 140 行
  - 仅保留 `HTML_SHELL`、`TYPE_COLORS`、`build_html()`、`build_from_db()`
  - CSS/JS 从 `ui_templates` 包导入
- **UI 风格完全不变**：CSS 和 JS 文件是原样提取，内容 100% 一致

**影响**:
- ui.py 从 2737 行减少到 140 行（减少 95%）
- CSS/JS 现在可以独立编辑，无需在 Python 字符串中查找
- 所有外部 API 保持兼容（`from wpy.ui import CSS, JS, build_html` 仍然有效）
- 风险: 低 — 纯重构，行为完全一致

### 2.4 server.py 职责优化

**文件**: `wpy/server.py`

**改动**:
- `guess_type()` 改为从 `content_type` 模块导入（与 2.1 配合）
- 保持了原有的路由/鉴权/代理结构不变

**说明**: server.py 的完整拆分（路由/鉴权/代理分离）属于更大幅度的重构，需要在后续 Phase 中谨慎进行。当前 Phase 仅做了最小化的重复消除。

### 2.5 ask.py 纳入 CLI

**文件**: `wpy/crawler/cli_app.py` (修改), `wpy/ask.py` (新增)

**改动**:
- 添加 `ask` 子命令：`python -m wpy ask "你的问题"`
- 完整的错误处理（AI API 不可用、密钥文件缺失、响应格式异常）
- 用户友好的错误提示

**影响**:
- `ask` 功能现在通过标准 CLI 入口调用
- 独立的 `ask.py` 脚本仍然可用（兼容层）
- 风险: 低 — 纯新增

---

## 文件变化

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `wpy/content_type.py` | **新增** | 统一的 Content-Type 映射 |
| `wpy/ui_templates/__init__.py` | **新增** | 模板包（CSS/JS 缓存读取） |
| `wpy/ui_templates/board.css` | **新增** | 看板 CSS（原 ui.py 提取） |
| `wpy/ui_templates/board.js` | **新增** | 看板 JS（原 ui.py 提取） |
| `wpy/ask.py` | **新增** | 智能问答（wpy 包内正式模块） |
| `wpy/ui.py` | **修改** | 2737 → 140 行，CSS/JS 从模板包导入 |
| `wpy/server.py` | **修改** | guess_type 改为导入 |
| `wpy/crawler/export_ops.py` | **修改** | guess_type 改为导入 |
| `wpy/crawler/cli_app.py` | **修改** | 添加 ask 子命令 |
| `ask.py` | **修改** | 兼容入口，调用 wpy.ask |
| `scripts/refresh_stats.py` | **修改** | 添加注释说明 sys.path 用途 |

---

## 测试结果

| 测试 | 结果 |
|------|------|
| `tools/offline_stage_smoke.py` | ✅ ALL OFFLINE STAGE SMOKE PASS |
| `tools/smoke_test.py` | ✅ ALL SMOKE PASS |
| CSS/JS 一致性验证 | ✅ CSS 一致, JS 一致 |
| ui.py 行数 | 2737 → 140 行 |

**验证级别**: Level 2（冒烟测试 + 一致性验证）

---

## 风险

1. **ui.py 拆分**: CSS/JS 文件是原样提取，内容 100% 一致。`from wpy.ui import CSS, JS` 的外部调用仍然有效。
2. **ask.py 迁移**: 旧用法 `python3 ask.py` 仍然兼容，新用法 `python -m wpy ask` 已添加。

---

## 尚未完成

- `server.py` 的完整拆分（路由/鉴权/代理分离）— 需要更谨慎的测试
- `run_workers` 异常传播增强（Phase 3）
- `_img_locks` 限容（Phase 3）
- 分页导出（Phase 3）
- 完整测试体系（Phase 4）

---

## 下一阶段

**Phase 3: 稳定性增强**

1. 增强 `run_workers`（异常传播）
2. `_img_locks` 限容
3. 分页导出（替代全量导出）
4. 全局状态显式化（依赖注入）
