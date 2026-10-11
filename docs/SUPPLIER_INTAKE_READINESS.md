# 供应商报价准入：只读评估与人工核验

此功能承接对账面板和隔离Pi运行器之后的真实供应商资料准入。它读取现有导入批次和不可变报价快照，不另建供应商、订单或审批状态机。

**评估完整不等于供应商获批。** 输出最高状态是`ready_for_human_review`：仅表示受检结构和声明齐全，仍需人工核验。`provider_verified`、`external_llm_authorized`、`platform_action_authorized`始终为false。

## 1. 正确运行顺序

1. 从有来源和使用权限的渠道获取原始报价文件。保留原始字节、取得时间、供应商身份核验和使用权限凭据；不要让AI补写来源或改标demo。
2. 通过现有`/commerce/supplier-imports/preview`预览，按原协议确认导入；保存返回的batch ID及每个snapshot ID。原始文件按共享来源合同提供`source_kind`、`source_reference`、`source_observed_at`、`valid_until`，导入后成为`attributes.quote_provenance`，原观察时间与`fetched_at`导入时间分开。导入确认只证明操作者提交过该文件，不代表供应商事实已被验证。
3. 调用只读缺口接口，取得待补字段。已有批次被撤销、导入部分失败或快照缺失时先停止。
4. 复制[声明模板](templates/supplier-intake-declaration.example.json)，按独立核对的原始材料填写。JSON里不得放模型密钥、Cookie、完整个人信息或私人联系方式。
5. 同时提供原始报价字节和声明进行只读评估。哈希不匹配时重新确认文件来源，不能直接改账本里的hash。
6. 人工核实每项证据与对应snapshot、SKU、规格、日期和条款一致，再决定是否进入下一阶段的只读商业分析。此报告没有持久化审批能力，不可当作审批令牌使用。

没有真实资料时允许用明确标注`source=demo`的夹具验证代码；不得据此宣称真实供应商验收通过。

## 2. 声明中的关键字段

| 字段 | 如何取得 / 如何核对 |
| --- | --- |
| `batch_id`、`content_sha256`、`supplier` | 必须与确认导入返回的同一批次一致；文件哈希由服务对原始字节重算 |
| `source_kind` | `unknown` / `demo` / `supplier_original`；最后一项是待核查声明，不是系统自动证明 |
| `origin_ref` | 原始取得记录的脱敏索引，必须与快照`quote_provenance.source_reference`相同；系统不访问URL或读取该字符串指向的路径 |
| `supplier_identity_ref` | 身份核验材料索引，不是随便填一个供应商名称 |
| `use_permission_ref` | 来源使用范围凭据；不能用文件在本机就推定允许提供给外部模型 |
| `local_review_permitted` | null代表未知，false代表未允许；true仍须操作者独立核验 |
| `external_llm_permitted` | 仅记录声明，不授予模型权限；报告的`external_llm_authorized`始终false |
| `issued_at` | 供应商真实报价出具时间，不能填今天的导入时间替代 |
| `received_at` | 取得报价的时间，不得晚于快照`fetched_at`导入时间；可晚于供应商原观察时间 |
| `valid_until` | 报价有效截止；必须有时区且严格晚于评估时刻，不得延长已存快照的截止时间 |
| `offers` | 以同批次snapshot ID为键，不按相似名称合并，不可引用其他批次/供应商快照 |
| 每条`currency`、`price_unit`、`quantity_unit` | 明确CNY及统一计价/库存/MOQ单位；箱价转件价不代表库存单位可以猜测 |
| 每条`freight_basis`、`freight_destination` | 当前评估只接受明确分摊到计价单位的运费和目的地；整单费、到付或偏远地区附加费未明确时保持缺口 |
| 每条`stock_observed_at`、`stock_valid_until` | 库存证据时间及期限。库存必须是明确整数并覆盖至少一个MOQ，不接受“有货”替代数量 |
| 每条`tax_terms`、`delivery_terms`、`after_sales_terms` | 保存真实条款和适用范围，不由模板默认包税、几天到货或无条件退货 |
| 每条`evidence_ref` | 本快照对应证据条目索引，不是跨SKU复用的一句“已核验” |

所有时间用带时区ISO格式，例如`2026-09-23T10:00:00+08:00`。当前接口不接受调用方自定义评估时刻，避免回拨时间掩盖过期。复用`quote_provenance.assess_quote_provenance`而非另设来源资格口径；旧快照缺少来源元数据不能靠补充声明升级为有效原件。沿用项目现有报价/库存24小时最大年龄规则，供应商声明更早到期则按更早期限；不是对现实市场有效期的保证。

## 3. API

### 只读查询缺口

`GET /commerce/supplier-imports/{batch_id}/readiness`

无原文件和声明时应返回缺口，不会自动推定来源、有效期或许可。找不到批次404，批次歧义或损坏409。

### 带证据声明评估

`POST /commerce/supplier-imports/{batch_id}/readiness`

JSON体含`declaration`（声明对象）和`content_b64`（原报价文件的base64，原文件最多2MiB）。没有文件路径参数，不会代调用方下载URL。字段严格校验，禁止额外operator字段及把字符串`"true"`转成授权布尔值。

POST是计算型只读操作，不保存原文件、声明或新业务记录。现有job会话门禁不因该功能扩权；未授权任务会话仍被403拒绝。该接口沿用现有应用访问边界，没有新增SSO或独立认证系统，不能裸露在公网。

返回包括：

- `policy_version`、`assessed_at`、`batch_id`、`input_sha256`；hash绑定受检批次、快照、声明与实际原文件摘要，**不证明原始内容真实**。
- `checks`与`gaps`：每项有code、`pass/unknown/fail`、snapshot ID及补充要求。
- `status`：`blocked`为已发现矛盾/失效；`needs_evidence`为资料未知；`ready_for_human_review`仍须人工复核。
- 三个不越权边界：`provider_verified=false`、`external_llm_authorized=false`、`platform_action_authorized=false`。

除结构检查外，评估还拒绝：demo改标real、跨供应商引用、批次撤销/部分失败、丢失/重复快照、原字节哈希变化、无时区/未来/过期时间、旧报价新导入、非明确币种/单位、未知运费、NaN/负价格、布尔/小数MOQ、库存不足、缺失售后等。

## 4. CLI：不启动服务、不写业务根

在项目目录运行，路径替换为自己的**隔离**资料目录：

```powershell
.\.venv\Scripts\python.exe scripts/check_supplier_intake.py --data-dir 'C:\operator\isolated-data' --batch-id 'impb_REPLACE'
.\.venv\Scripts\python.exe scripts/check_supplier_intake.py --data-dir 'C:\operator\isolated-data' --batch-id 'impb_REPLACE' --quote-file 'C:\operator\original.csv' --declaration 'C:\operator\declaration.json'
```

CLI只读取`commerce/import_batches.json`、`commerce/offers.json`及明确提供的两个文件；不存在的数据目录不会被创建。账本文件各最多16MiB，报价和声明各最多2MiB，每批最多1000条快照，超限应拆批或另行设计导出器，不自动扩容。CLI对两份业务文件前后重读比对；发现评估期间变化即拒绝输出ready结论。输出JSON到stdout，不自动保存报告或调用模型。

| 退出码 | 含义 |
| --- | --- |
| 0 | 结构齐全，待人工复核；不是供应商、模型或交易批准 |
| 2 | blocked / needs_evidence，见逐项gaps |
| 1 | 输入不存在、格式不符、过大或读取期间变化；未写业务数据 |

需要归档时由操作者将stdout保存到独立报告目录，不要覆盖输入或现有业务文件。

## 5. 验证命令

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_supplier_intake.py -v
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests_isolated.ps1
```

测试使用临时文件、临时仓库与人工构造的声明；不访问供应商、不支付模型费用、不读取或改变日常供应商业务数据。完整声明夹具仅用于验证结构判定，不是伪造业务事实。

## 6. 本阶段刻意不做的事

- 不自动验证引用材料是否真的来自供应商，不认证提交声明者，不扫描本机寻找私人材料，不把客户声明当成审批。
- 不重算每个原始单元格与所有历史归一化字段的语义等价；原始字节hash绑定和人工逐项核验是两回事。
- 本批不修改Pi任务收集器/机会计算/发布链路；复用并行实现的共享来源门禁，但本报告中额外的身份、权限、售后等核验尚不是所有旧入口的强制门禁。不能把本批报告当成整条链路已完成真实供应商授权的证明。
- 不把税费说明直接折算利润，不把运费范围/图片使用权/市场基线等更后阶段前置条件标完成。
- 不将JSON单进程状态宣称为跨文件数据库事务或断电一致性；报告是评估时刻的诊断，使用前须重算，不能永久缓存ready状态。

下一步复用现有证据归档与审批能力，将真实材料、人工身份及批准范围绑定到批次/快照版本，再把该结果呈现在导入面板并接入只读任务输入。未完成这些门槛之前不新增真实模型或平台自动写入权限。
