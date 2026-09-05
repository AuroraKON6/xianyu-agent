# 浏览器写入失败证据 Runbook

Updated: 2026-09-02

## 1. 目的

本 Runbook 规定闲鱼浏览器适配器在平台写入失败时如何保存诊断证据，以及操作者如何使用这些证据判断下一步。

它覆盖当前 BrowserAdapter 的五类写动作：

- 发布商品 `publish_listing`
- 修改商品 `edit_listing`
- 发货 `fulfill_order`
- 发送普通或敏感回复 `send_message` / `sensitive_reply`
- 删除商品 `delete_item`

自动证据包用于回答“当时打开了什么页面、失败发生在哪个阶段、是否已经尝试平台写入”。它不是平台成功回执，也不能替代人工核验。

## 2. 核心判断

| 失败时点 | `write_attempted` | 执行状态 | 是否允许直接重试 |
|---|---:|---|---|
| 浏览器会话未建立 | `false` | `failed` | 修复会话后可按正常显式重试流程处理 |
| 页面或输入控件不存在 | `false` | `failed` | 检查页面、登录、类目或 selector 后再显式重试 |
| 已点击发布、保存、发送、确认发货或确认删除后中断 | `true` | `verification_required` | 不允许；先核对平台真实状态 |
| 发布按钮已点击但未识别到商品 ID | `true` | `verification_required` | 不允许；先检查目标账号的在售/草稿列表 |

任何 `write_attempted=true` 的未知结果都按“平台可能已经接受动作”处理。超时、页面未跳转或连接断开不能被解释为失败。

## 3. 自动采集内容

每次采集会尽力创建一张 PNG 截图和一份 JSON 清单。清单包含：

| 字段 | 含义 |
|---|---|
| `evidence_id` | 本次证据唯一编号 |
| `action` / `stage` | 写动作和失败阶段 |
| `captured_at` | UTC Unix 时间 |
| `adapter` / `adapter_status` | `browser`，以及页面是否可用 |
| `account_scope` | 账号标识的 SHA-256 截断值，不是账号原值 |
| `target_scope_sha256` | 商品、订单或买家目标的 SHA-256 |
| `write_attempted` | 是否已进入可能改变平台状态的步骤 |
| `page_url` | 删除 userinfo、查询参数和 fragment 后的页面 URL |
| `page_title` | 脱敏后的页面标题 |
| `error_summary` | 脱敏并截断的错误摘要 |
| `detected_blocker` | 识别出的登录、控件、类目或适配器阻塞 |
| `capture_status` | `complete` 或 `metadata_only` |
| `capture_error` | 截图或落盘自身的失败原因 |
| `screenshot.path` | 相对 `DATA_DIR` 的截图路径 |
| `screenshot.sha256` / `bytes` | 截图完整性哈希和字节数 |

`complete` 表示截图和清单均落盘。`metadata_only` 表示没有截图，但 JSON 清单已经保存；例如浏览器根本未建立，或页面截图接口也发生异常。

## 4. 存储位置

证据保存在：

```text
DATA_DIR/
  browser_write_evidence/
    <account_scope>/
      <UTC YYYY-MM-DD>/
        <HHMMSS>_<action>_<evidence-suffix>.png
        <HHMMSS>_<action>_<evidence-suffix>.json
```

文件夹只使用账号哈希，不使用 Cookie ID、账号昵称、买家 ID、商品 ID 或订单 ID 原文。ExecutionJob 保存相对路径，控制台的“执行任务”和 Reply Outbox 会显示该路径。

测试必须将 `DATA_DIR` 指向系统临时目录。不得让故障注入测试在 `data/live` 生成证据。

## 5. 操作者处理顺序

### 5.1 `failed` 且 `write_attempted=false`

1. 在控制台查看 `adapter_status`、`stage`、`detected_blocker` 和证据路径。
2. 打开 JSON 清单；如有截图，同时检查截图是否对应目标账号和预期页面。
3. 修复明确原因，例如重新登录、确认类目可在网页端发布、更新页面 selector 或补齐必填字段。
4. 回到当前 ExecutionJob，确认账号、目标、批准内容和时效仍然有效。
5. 只使用现有显式重试入口，不新建并行的相同动作。

### 5.2 `verification_required` 且 `write_attempted=true`

1. 立即停止相同动作的任何再次提交。
2. 打开任务绑定的准确卖家账号。
3. 按 `docs/ops/execution-recovery-runbook.md` 的动作核验表检查平台。
4. 自动截图仅用于定位失败时页面；另行保存平台当前状态、平台对象 ID、页面 URL或人工核验截图。
5. 明确选择 `confirmed_completed` 或 `confirmed_not_executed`。
6. 只有 `confirmed_not_executed` 关闭后，任务才会成为可显式重试的 `failed`。

结果仍然模糊时保留 `verification_required`，不要猜测。

## 6. 截图完整性检查

在 `DATA_DIR` 下运行，使用清单中记录的相对路径：

```powershell
$manifestPath = 'browser_write_evidence\ACCOUNT_SCOPE\DATE\EVIDENCE.json'
$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
$screenshotPath = Join-Path (Get-Location) $manifest.screenshot.path
$actual = (Get-FileHash -Algorithm SHA256 $screenshotPath).Hash.ToLowerInvariant()
$actual -eq $manifest.screenshot.sha256
```

返回 `True` 才能说明截图文件与清单中的哈希一致。它仍然只证明文件未变化，不证明平台动作成功或失败。

## 7. 数据保护

系统当前实施以下保护：

- URL 不保存 userinfo、query 或 fragment。
- Cookie、Token、Authorization、Password、Secret、Session 形式的赋值会被替换为 `[REDACTED]`。
- 已知账号标识和目标标识在文字字段中替换为 `[REDACTED_REF]`。
- 账号和目标只用哈希参与归档和绑定。
- ExecutionJob 和 Reply Outbox 只投影经过限制的证据字段。

仍需注意：浏览器截图可能包含商品内容、买家昵称、订单信息或其他经营数据。证据目录只能保存在本机受控 `DATA_DIR`，不得提交 Git、粘贴到公开 issue 或发送给无关人员。

当前没有自动清理策略。处于 `verification_required`、争议、售后或未结案实验中的证据不得删除；结案后的保留和清理应由操作者按实际审计需求执行，并保持 ExecutionJob 引用与文件同步。

## 8. 证据采集自身失败

截图失败不能覆盖原始业务失败：

- 原始动作仍按 `failed` 或 `verification_required` 进入队列状态机。
- `capture_error` 记录采集问题。
- 如 JSON 清单能落盘，状态为 `metadata_only`。
- 如果连目录或清单也无法写入，ExecutionJob 仍保存内存返回的摘要；操作者应先检查磁盘权限和 `DATA_DIR`。

不要因为截图失败而自动重跑平台动作。

## 9. 当前验收证据与边界

2026-09-02 已完成本地故障注入验收：

- 完整截图、JSON 清单、SHA-256 和字节数一致。
- URL userinfo/query/fragment、账号、目标和常见凭据赋值不会进入清单。
- 截图失败时保留 `metadata_only` 清单。
- 浏览器不可用时仍创建每账号哈希归档的元数据证据。
- 确认发货点击后断连会进入 `verification_required`，并保存 `write_attempted=true` 的证据。
- 嵌套 adapter 证据会进入 ExecutionJob、Reply Outbox 和 OPC Dashboard。
- 完整隔离测试 `966/966` 通过，耗时 `26.061s`。

尚未验证：

- 真实闲鱼页面在浏览器进程被强制终止时能够保存多少截图内容。
- 闲鱼页面结构变化后的 blocker 和 selector 识别质量。
- 真实账号下发布、回复、删除和发货的证据可读性。
- 多主机或网络共享存储。

因此本阶段结论是“本地失败证据机制 READY”，不是“真实平台写入已验证”。首次真实写入仍必须由操作者在场、只用一个账号和一个商品，并遵守审批及未知结果核验流程。
