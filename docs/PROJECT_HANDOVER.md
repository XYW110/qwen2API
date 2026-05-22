# 项目交接文档

## 项目概览

**项目名称**: qwen2API  
**项目类型**: Python/FastAPI 后端 + React/TypeScript 前端  
**工作目录**: `D:\Work\Project\ToolProject\qwen2API`  
**任务**: 账号管理功能增强（批量导入/导出和策略配置）  
**完成日期**: 2026-05-23

## 技术栈

### 后端技术栈

- **框架**: FastAPI
- **HTTP 客户端**: `curl_cffi`（无浏览器模式）
- **数据库**: `AsyncJsonDB`
- **Python 版本**: 3.13

### 前端技术栈

- **框架**: React 18
- **语言**: TypeScript 5.6
- **状态管理**: useState, useMemo, useEffect
- **路由**: 自定义 admin 布局路由

## 核心文件路径

### 后端目录结构

```
backend/
├── api/
│   ├── admin.py              # 管理 API 端点（批量导入/导出、策略配置）
│   ├── anthropic.py          # Anthropic API 代理
│   ├── embeddings.py         # Embeddings API
│   ├── files_api.py          # 文件上传 API
│   ├── gemini.py             # Gemini API 代理
│   ├── images.py             # 图片生成 API
│   ├── responses_api.py      # 响应处理 API
│   └── v1_chat.py            # 聊天 API
├── core/
│   ├── account_pool.py       # 账号池管理
│   ├── config.py             # 配置管理（策略配置）
│   └── database.py           # 数据库操作
├── services/
│   └── auth_resolver.py      # 认证解析器
└── main.py                   # 应用入口
```

### 前端目录结构

```
frontend/src/
├── components/
│   ├── BatchExportModal.tsx  # 批量导出模态框（新建）
│   ├── BatchImportModal.tsx  # 批量导入模态框（新建）
│   └── StrategyConfig.tsx    # 策略配置模态框（新建）
├── layouts/
│   └── AdminLayout.tsx       # 管理后台布局
├── pages/
│   └── AccountsPage.tsx      # 账号管理页面
└── lib/
    ├── api.ts                # API 基础配置
    └── auth.ts               # 认证相关
```

## 已完成功能

### 1. 批量导入功能

**后端 API**: `POST /api/admin/accounts/batch-import`  
**文件**: `backend/api/admin.py` (lines 85-170)

**功能描述**:
- 解析批量导入的账号文本（格式：`email:password` 或 `email:password|proxy`）
- 验证每个账号的有效性
- 导入成功返回成功数量和失败详情

**导入格式**:
```
email:password         # 仅邮箱和密码
email:password|proxy   # 邮箱、密码和代理
```

### 2. 批量导出功能

**后端 API**: `GET /api/admin/accounts/export`  
**文件**: `backend/api/admin.py` (lines 435-466)

**功能描述**:
- 导出所有已注册的账号（排除手动注入的账号）
- 返回格式与批量导入格式兼容
- 返回账号总数

**导出格式**:
```
email:password
email:password|proxy_url
```

### 3. 策略配置功能

**后端 API**:
- `GET /api/admin/settings` - 获取当前配置
- `PUT /api/admin/settings` - 更新配置

**文件**: `backend/api/admin.py` (lines 470-502)  
**配置项**: `backend/core/config.py` (lines 27-30)

**配置参数**:

| 参数 | 环境变量 | 默认值 | 说明 |
|------|----------|--------|------|
| `ACCOUNT_SELECTION_STRATEGY` | `ACCOUNT_SELECTION_STRATEGY` | `least_loaded` | 账号选择策略 |
| `ACCOUNT_MAX_FAILURES_BEFORE_COOLDOWN` | `ACCOUNT_MAX_FAILURES_BEFORE_COOLDOWN` | `3` | 连续失败次数阈值 |
| `ACCOUNT_COOLDOWN_PERIOD_SECONDS` | `ACCOUNT_COOLDOWN_PERIOD_SECONDS` | `300` | 冷却时长（秒） |

### 4. 策略选项

| 策略 | 说明 |
|------|------|
| `least_loaded` | 选择当前并发负载最低的账号（默认策略） |
| `least_used` | 选择最久未使用的账号，均匀分配负载 |
| `round_robin` | 轮询分配，依次使用每个账号 |

### 5. 冷却机制

**功能描述**:
- 账号连续失败达到阈值后进入冷却状态
- 冷却期间账号不会被选择使用
- 冷却结束后自动恢复可用状态

**冷却显示**:
- 前端账号列表中显示"冷却中"状态
- 显示冷却剩余时间

## API 端点

| 端点 | 方法 | 功能 | 认证 |
|------|------|------|------|
| `/api/admin/accounts/batch-import` | POST | 批量导入账号 | admin |
| `/api/admin/accounts/export` | GET | 批量导出账号 | admin |
| `/api/admin/account_diagnostics` | GET | 账号诊断信息 | admin |
| `/api/admin/settings` | GET | 获取设置 | admin |
| `/api/admin/settings` | PUT | 更新设置 | admin |
| `/api/admin/accounts/{email}/chats/clear` | POST | 清除账号聊天 | admin |
| `/api/admin/accounts/{email}` | DELETE | 删除账号 | admin |

## 数据结构

### Account 类型 (Pydantic Model)

**文件**: `backend/core/account_pool.py`

```python
class Account(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    username: Optional[str] = None
    proxy: Optional[str] = None
    valid: bool = False
    inflight: int = 0
    rate_limited_until: Optional[float] = None
    activation_pending: bool = True
    status_code: Optional[str] = None
    status_text: Optional[str] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0
    cooldown_started_at: Optional[float] = None
```

### AccountItem 类型 (Frontend TypeScript)

**文件**: `frontend/src/pages/AccountsPage.tsx`

```typescript
type AccountItem = {
  email: string;
  password?: string;
  token?: string;
  username?: string;
  proxy?: string;
  valid?: boolean;
  inflight?: number;
  rate_limited_until?: number;
  activation_pending?: boolean;
  status_code?: string;
  status_text?: string;
  last_error?: string;
  consecutive_failures?: number;
  cooldown_started_at?: number;
  is_in_cooldown?: boolean;
  selection_block_reason?: string;
  cooldown_ends_at?: number;
};
```

## 新增组件

### BatchExportModal (批量导出模态框)

**文件**: `frontend/src/components/BatchExportModal.tsx`

**功能**:
- 调用后端 API 获取账号列表
- 在文本框中显示导出的账号
- 提供复制到剪贴板功能

**Props**:
```typescript
interface Props {
  onClose: () => void;
}
```

### BatchImportModal (批量导入模态框)

**文件**: `frontend/src/components/BatchImportModal.tsx`

**功能**:
- 提供文本框输入或文件上传
- 解析并验证导入数据
- 显示导入结果（成功/失败数量）

**Props**:
```typescript
interface Props {
  onClose: () => void;
  onImportComplete?: () => void;
}
```

### StrategyConfig (策略配置模态框)

**文件**: `frontend/src/components/StrategyConfig.tsx`

**功能**:
- 从后端获取当前配置
- 动态更新账号选择策略
- 配置失败阈值和冷却时长

**Props**:
```typescript
interface Props {
  onClose: () => void;
}
```

## 代码修改记录

### 后端修改

#### backend/api/admin.py

1. **新增导入** (line 6):
   - `import secrets` - 用于生成 API Key

2. **新增函数 `_is_valid_proxy_url`** (lines 12-16):
   - 验证代理 URL 格式

3. **新增函数 `_parse_account_line`** (lines 18-50):
   - 解析单行账号文本
   - 支持 `email:password` 和 `email:password|proxy` 格式

4. **新增函数 `_parse_batch_accounts_text`** (lines 52-83):
   - 批量解析账号文本
   - 分离有效账号和无效账号

5. **扩展 `get_settings` 端点** (lines 470-484):
   - 新增返回字段：`account_selection_strategy`、`account_max_failures_before_cooldown`、`account_cooldown_period_seconds`

6. **扩展 `update_settings` 端点** (lines 487-502):
   - 新增配置项更新支持

#### backend/core/config.py

1. **新增策略配置项** (lines 27-30):
   ```python
   ACCOUNT_SELECTION_STRATEGY: str = os.getenv("ACCOUNT_SELECTION_STRATEGY", "least_loaded")
   ACCOUNT_MAX_FAILURES_BEFORE_COOLDOWN: int = int(os.getenv("ACCOUNT_MAX_FAILURES_BEFORE_COOLDOWN", 3))
   ACCOUNT_COOLDOWN_PERIOD_SECONDS: int = int(os.getenv("ACCOUNT_COOLDOWN_PERIOD_SECONDS", 300))
   ```

### 前端修改

#### frontend/src/pages/AccountsPage.tsx

1. **新增导入**:
   - `Download` - 批量导出图标
   - `Settings2` - 策略配置图标
   - `BatchExportModal` - 批量导出模态框
   - `StrategyConfigModal` - 策略配置模态框

2. **新增状态**:
   - `isBatchExportOpen` - 控制批量导出模态框显示
   - `isStrategyConfigOpen` - 控制策略配置模态框显示

3. **新增按钮**:
   - "批量导出" 按钮 - 打开批量导出模态框
   - "策略配置" 按钮 - 打开策略配置模态框

#### frontend/src/layouts/AdminLayout.tsx

- 调整账号管理页面路由

## 测试

### 测试文件

**文件**: `tests/test_account_pool_diagnostics.py`

**测试内容**:
- 账号池初始化测试
- 账号添加和删除测试
- 账号选择策略测试
- 账号诊断信息测试

## 环境变量配置

### 后端环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `PORT` | `8080` | 服务端口 |
| `ADMIN_KEY` | `admin` | 管理员密钥 |
| `ACCOUNT_SELECTION_STRATEGY` | `least_loaded` | 账号选择策略 |
| `ACCOUNT_MAX_FAILURES_BEFORE_COOLDOWN` | `3` | 失败阈值 |
| `ACCOUNT_COOLDOWN_PERIOD_SECONDS` | `300` | 冷却时长（秒） |
| `MAX_INFLIGHT_PER_ACCOUNT` | `1` | 每账号最大并发数 |

## 待完成工作

1. **持久化配置**: 当前策略配置仅保存在内存中，重启服务后会恢复默认值
2. **配置导出/导入**: 尚未实现配置的导出和导入功能
3. **单元测试**: 需要补充更多单元测试

## 注意事项

1. **Token 获取方式**: 必须从 Local Storage 中获取 token 原始值，不要从 Network 请求或 Authorization 请求头中提取

2. **冷却机制**:
   - 账号连续失败达到阈值后进入冷却
   - 冷却期间账号不会被选择使用
   - 冷却结束后自动恢复

3. **服务重启**: 修改环境变量后需要重启服务才能生效

## 快速开始

### 启动后端

```bash
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

### 启动前端

```bash
cd frontend
npm run dev
```

### 运行测试

```bash
cd backend
python -m pytest tests/ -v
```

---

**文档版本**: 1.0  
**最后更新**: 2026-05-23  
**作者**: Snow AI CLI