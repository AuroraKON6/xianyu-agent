# Commercial Alpha 单品资料预检包

## 1. 目的

这个工具把“一个真实账号 + 一个精确 SKU”中的商品资料部分先在离线目录中收齐并一次校验，避免服务启动后才发现标题占位、SKU 不明确、证据缺类、截图过期、图片损坏、文件含 Cookie 或路径写错。

它只解决进入候选系统前的资料质量，不替代以下事实：

- 不创建候选、证据存档、审批、草稿或 Commercial Alpha 批次。
- 不登录闲鱼，不读取账号身份，不调用 adapter，不执行平台写操作。
- 不证明截图内容真实；操作者仍要核对文件确实对应同一个供应商、同一个 SKU 和同一次市场观察。
- 不把估算费率当成真实账号条款。账号、类目、保证金、买保、退货和平台费必须在执行当天从目标账号页面记录。
- 预检通过最多投影为候选前 11 项通过；第 12 项必须在正式归档和账号条款重算后人工批准。

## 2. 为什么放在真实运营顺序的这里

Commercial Alpha 的实际顺序是：

1. 操作者选定一个可交付商品，而不是先批量铺货。
2. 锁定一个供应商的精确 SKU、规格、实付价、运费、库存和履约边界。
3. 收集五类可复核证据和至少一张有权使用的真实商品图片。
4. 离线预检资料结构、文件完整性和时效。
5. 启动服务并绑定一个真实卖家账号。
6. 将同一包资料录入候选并归档五类原始文件。
7. 读取该账号与该类目的当前商业条款，重新计算利润。
8. Agent 评审，操作者批准当前证据指纹，候选达到 `12/12`。
9. Day-0 启动预览达到 `20/20`，只创建一个批次；创建批次不会自动发布。
10. 发布仍走独立的 WorkOrder、Approval 和 ExecutionJobQueue。

离线包位于第 4 步。它减少返工，但不会越过第 5-10 步。

## 3. 创建一个新包

在项目根目录 `E:\闲鱼\xianyu` 执行：

```powershell
.\.venv\Scripts\python.exe scripts\commercial_alpha_intake.py init `
  --output "E:\闲鱼\商业资料\alpha-001"
```

目标目录必须不存在或为空。命令不会覆盖已有资料，成功后生成：

```text
alpha-001/
  manifest.json
  evidence/
  images/
```

不要把真实证据放入项目的 `data/live`，也不要提交到 Git。建议每个候选单独使用一个目录，并将目录权限限制为当前 Windows 用户。

## 4. 包内文件

### 4.1 `manifest.json`

模板只接受三个顶层字段：

| 字段 | 含义 | 规则 |
|---|---|---|
| `schema_version` | 预检包版本 | 当前只能为 `1` |
| `candidate` | 一个精确 SKU 的商品和经营事实 | 不能放多商品，也不能使用 `T/test/demo` |
| `evidence` | 五类证据文件元数据 | 每类恰好一个，路径必须在包目录内 |

不要在 JSON 中增加注释、尾逗号或重复键。未知字段会被阻断，防止拼写错误悄悄丢失事实。

### 4.2 商品事实

`candidate` 必须最终具备：

| 经营事实 | 最低要求 |
|---|---|
| 商品身份 | 真实标题、闲鱼类目、供应商 SKU、至少两个有值且可交付的规格 |
| 供应商来源 | `pdd` 或 `1688`、真实商品链接、可识别供应商简称 |
| 供应商核验 | `supplier_verified=true`，备注说明核对对象、渠道和结论 |
| 采购成本 | 精确 SKU 的实付价、运费、MOQ |
| 库存 | `in_stock` 或 `limited`，正数可发量 |
| 履约 | 无价格信息发货、1-168 小时出库、退换政策和错货/破损责任 |
| 图片 | `images/` 内至少一张 PNG/JPEG/WebP，每张不超过 10 MiB |
| 图片权利 | `owned`、`supplier_authorized` 或 `licensed`，并写明依据 |
| 市场基线 | 两个不同的当前闲鱼可比链接、展示价上下限和可复核需求信号 |
| 利润假设 | 采购价、运费、售价、暂估平台费和退款损耗；最终由真实账号条款覆盖 |

`supplier_verified`、`fulfillment_verified` 和 `neutral_shipping_confirmed` 只有在操作者实际核对后才能改为 `true`。程序不会根据截图名称替你推断结论。

### 4.3 五类证据

| `kind` | 证明内容 | 启动时效 |
|---|---|---:|
| `supplier_quote` | 精确 SKU 实付价、运费、MOQ | 24 小时内 |
| `supplier_stock` | 精确 SKU 当前可发库存 | 24 小时内 |
| `supplier_fulfillment` | 无痕发货、时效、退换和破损责任 | 必须存在 |
| `image_rights` | 自有实拍、供应商授权或许可证 | 必须存在 |
| `market_baseline` | 两个可比闲鱼商品、展示价和需求观察 | 72 小时内 |

证据支持 PNG、JPEG、WebP、PDF、HTML、UTF-8 TXT/MD/JSON，每个文件最多 10 MiB。扩展名、MIME 规则和文件魔数与正式候选证据归档完全共用；预检能通过的文件仍会在正式上传时重新计算 SHA-256。

`observed_at` 必须是带时区的 ISO 时间，例如：

```text
2026-09-05T15:30:00+08:00
```

报价、库存和市场截图应尽量在资料全部填完后最后采集，避免尚未绑定账号就先过期。

## 5. 数据清理

上传前必须裁掉或涂黑：

- Cookie、Authorization、token、API key、密码和支付凭据。
- 手机号、身份证号、收货人、详细地址和私人聊天身份信息。
- 与候选 SKU 无关的订单、账号余额和其他客户资料。

文本证据、证据备注和 URL 参数会执行自动检查，但图片中的文字不会自动 OCR。图片脱敏仍由操作者负责。不要为了通过校验修改真实价格或库存；事实不满足就停止该候选。

## 6. 运行只读预检

```powershell
.\.venv\Scripts\python.exe scripts\commercial_alpha_intake.py validate `
  --bundle "E:\闲鱼\商业资料\alpha-001"
```

命令从项目 `.env` 读取 `MIN_PROFIT_AMOUNT` 和 `MIN_PROFIT_RATE`。做固定时间审计时可显式传入：

```powershell
.\.venv\Scripts\python.exe scripts\commercial_alpha_intake.py validate `
  --bundle "E:\闲鱼\商业资料\alpha-001" `
  --checked-at "2026-09-05T16:00:00+08:00" `
  --min-profit-amount 20 `
  --min-profit-rate 0.30
```

退出码：

| 退出码 | 含义 |
|---:|---|
| `0` | 资料包可进入正式归档 |
| `2` | 资料或候选门禁阻断；按 `issues[0]` 修复后重跑 |
| `1` | 命令、目录、配置或时间参数错误 |

`validate` 只读文件并向标准输出返回 JSON，不写报告文件，也不修改包内 mtime，不访问 `data/live`。

## 7. 通过标准

只有下面条件同时成立，才把资料包交给正式候选流程：

```text
status = pass
bundle_ready = true
writes_performed = false
evidence.valid_count = 5
images.valid_count >= 1
candidate_gate_projection.evidence_ready = true
candidate_gate_projection.completion = 11/12
candidate_gate_projection.failed_gate_codes = [manual_decision_current]
commercial_ready = false
```

这里的 `11/12` 是“假设这些已验证文件随后按原哈希正式归档”的投影。报告中的 `artifact:cea_...` 只存在于内存投影，不是正式证据编号。正式上传后系统会再次验证内容，并生成真实候选绑定引用。

`bundle_fingerprint` 绑定 manifest 内容、相对路径、文件大小和 SHA-256。同一份资料在相同内容下指纹稳定；任何文件或经营事实变化都应重跑预检。

## 8. 预检通过后的正式落地

服务保持关闭，直到操作者准备进行真实登录。届时按下面顺序操作：

1. 使用正常启动脚本，核对 `/health`、Pi、数据库绝对路径、单进程和可干净停止。
2. 在控制台「账号画像」完成目标账号登录、身份预览和一次性 `BIND ...` 确认。
3. 初始化同一账号 Prompt，并完成 listing/order 双向同步与运行时新鲜度检查。
4. 在「倒卖机会验证」创建一个新候选；不要修改历史占位候选 `T` 充当真实商品。
5. 将 manifest 中的商品事实保存到「一品启动包」。
6. 在「候选证据存档」按五类逐个上传原文件；每类确认 `active`、`integrity.ok=true`、观察时间正确。
7. 重新打开启动包，核对前 11 项。若上传期间报价、库存或市场证据过期，重新采集，不要改时间。
8. 记录目标账号与该类目的当天商业条款，让系统按真实平台费重算利润。
9. 对当前修订执行 Agent 评审，再由操作者批准当前证据指纹，确认候选 `12/12`。
10. 运行 Commercial Alpha preview。只有 `20/20` 时才原样输入 `START 1`，且只创建一个不自动发布的批次。

当前版本故意不提供“一键导入并批准”。候选创建、五次不可变证据归档、账号条款和人工决策是不同责任节点；后续可以增加带预览令牌和幂等事务的导入助手，但不能把这些节点合并成一次静默写入。

## 9. 停止条件

出现以下任一情况，停止该候选，不进入正式归档：

- 精确 SKU、规格、价格或库存无法与证据一一对应。
- 供应商拒绝确认正常无价格信息发货、退换或破损责任。
- 图片权利不清楚，或只能复制第三方卖家素材。
- 两个竞品不是同规格可比商品，或只有展示价而没有任何需求信号。
- 保守利润假设低于 `MIN_PROFIT_AMOUNT` 或 `MIN_PROFIT_RATE`。
- 文件中含无法可靠移除的账号凭据或个人信息。
- 为赶进度需要虚构时间、库存、授权、条款或操作者批准。

阻断不是失败记录，而是候选淘汰或补证信号。首轮 Commercial Alpha 的目标是获得一条可信的小闭环，不是让所有候选都通过。
