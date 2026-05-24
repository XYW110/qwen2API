# 公共 API 接口文档 - 待审批账户提交

本文档详细说明了 qwen2API 提供的公共账户提交接口。该接口允许非管理员用户提交账户凭证，这些账户将进入待审批状态，需经管理员审核后方可加入正式账号池。

## 基础信息

- **Base URL**: `http://<your-host>:<port>/api`
- **认证方式**: Bearer Token (API Key)
- **数据格式**: JSON

## 接口列表

### 1. 提交待审批账户

**端点**: `POST /public/pending-accounts`

**描述**:
非管理员用户通过此接口提交新的账户信息。提交的账户不会立即生效，而是进入"待审批"状态，存储在隔离的数据文件中 (`data/pending_accounts.json`)。管理员需在管理台进行审核操作。

**请求头**:

| 参数名 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `Authorization` | string | 是 | Bearer Token，格式为 `Bearer <YOUR_API_KEY>` |
| `Content-Type` | string | 是 | 必须为 `application/json` |

**请求体**:

| 参数名 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `email` | string | 是 | 账户邮箱地址 |
| `password` | string | 是 | 账户密码 |
| `proxy` | string | 否 | 代理服务器地址，若无则传 `null` 或省略 |
| `cookies` | string | 否 | 额外的 Cookie 信息，若无则传 `null` 或省略 |

**请求示例**:

```bash
curl -X POST http://127.0.0.1:7860/api/public/pending-accounts \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key-here" \
  -d '{
    "email": "user@example.com",
    "password": "secure_password_123",
    "proxy": "http://proxy-server:8080"
  }'
```

**成功响应 (201 Created)**:

```json
{
  "ok": true,
  "message": "账户已提交，等待管理员审批",
  "pending_account_id": "uuid-string-here"
}
```

**错误响应**:

1. **401 Unauthorized** - API Key 无效或缺失
   ```json
   {
     "detail": "Invalid API key"
   }
   ```

2. **429 Too Many Requests** - 触发速率限制（每个 API Key 每小时最多 10 次提交）
   ```json
   {
     "detail": "Rate limit exceeded. Try again later."
   }
   ```

3. **409 Conflict** - 邮箱已存在（在正式账号池或待审批列表中）
   ```json
   {
     "detail": "Email already exists in pending or active accounts"
   }
   ```

4. **400 Bad Request** - 请求体格式错误或缺少必填字段
   ```json
   {
     "detail": "Validation error: email is required"
   }
   ```

## 安全与限制

1. **身份验证**: 所有请求必须携带有效的 API Key。
2. **速率限制**: 每个 API Key 每小时最多允许 10 次提交请求，超过限制将返回 429 错误。
3. **邮箱去重**: 系统会检查提交的邮箱是否已存在于正式账号池 (`data/accounts.json`) 或待审批列表 (`data/pending_accounts.json`) 中，若存在则拒绝提交。
4. **数据隔离**: 待审批账户数据独立存储，不会影响正式账号池，直到管理员明确批准。

## 管理员后续操作

账户提交后，管理员需登录管理台进行以下操作：

1. **查看待审批列表**: 访问管理台的"待审批账户"页面，或通过 API `GET /api/admin/pending-accounts` 获取列表。
2. **批准账户**: 调用 `POST /api/admin/pending-accounts/{id}/approve`，系统将自动验证账户有效性并加入正式账号池。
3. **拒绝账户**: 调用 `POST /api/admin/pending-accounts/{id}/reject`，直接删除待审批记录。

## 常见问题

**Q: 为什么我的提交被拒绝了？**
A: 可能原因包括：API Key 无效、邮箱已存在、触发速率限制或请求格式错误。请检查响应中的 `detail` 字段获取具体原因。

**Q: 提交的账户多久会被处理？**
A: 提交后账户处于待审批状态，直到管理员手动审核。建议提交后通知管理员及时处理。

**Q: 如何获取 API Key？**
A: API Key 需由管理员在管理台的"API Key 管理"页面生成并分配。
