# 价格承诺与商品事实 Runbook

更新：2026-09-06。适用模块：`xianyu_agent/price_commitments.py`、`/price-commitments`、`/product-facts` 路由。

## 定位

结构化议价记忆与商品事实表（研究项 GH-05）。核心规则由确定性代码保证，LLM 不写入任何价格事实：

- `seller_lowest_committed` **只降不升**：只在平台发送确认的回复中出现更低的明确价格时下移。
- `buyer_highest_offer` **只升不降**：只记录买家的更高出价。
- 提取是固定正则（￥/¥ 前缀、元/块后缀、`50出`/`出50` 速记），超出合理区间（0.01–1,000,000）的数字直接丢弃；已知挂牌价时，出价必须落在 `[0.1×, 1.05×]` 挂牌区间内才入库。
- 每条记录按 `cookie_id + chat_id + item_id` 隔离，完整保留提取历史（含被超越的报价），同一来源消息幂等。

## 记录时机

- **买家出价**：`POST /workflows/messages/inbound` 创建 intake 时自动提取（去重分支不重复提取）。
- **卖家承诺**：`/execution-jobs/run-next|run-pending` 中 `send_message`/`sensitive_reply` 任务到达 `completed` 时，取回复文本中的**最低**明确价格入账；`verification_required` 结果**不入账**（未确认的发送不构成承诺）。
- **商品事实**：由操作者通过 `PUT /product-facts` 显式登记/修订（挂牌价、库存、发货天数、成色、备注），保留修订历史；不修改已登记事实则不产生新修订。

## 操作者命令

```powershell
# 查看议价记忆（含指标）
Invoke-RestMethod 'http://127.0.0.1:8788/price-commitments?cookie_id=<acc>&chat_id=<chat>'

# 登记/修订商品事实（影响出价可信区间与回复上下文）
Invoke-RestMethod -Method Put 'http://127.0.0.1:8788/product-facts' -Body (@{
  cookie_id = "<acc>"; item_id = "<item>"; list_price = 100.0; stock_available = 3
} | ConvertTo-Json) -ContentType 'application/json'

Invoke-RestMethod 'http://127.0.0.1:8788/product-facts?cookie_id=<acc>'
```

Reply Outbox 投影自动附带 `price_context`（当前卖方底价、买方最高出价、挂牌价），供审批时参考。

## 边界与已知限制

1. 提取只识别**绝对价格表述**；"便宜20块" 这类相对砍价不会入库（无基数时不可靠）。
2. 正则提取可能漏掉非标准表述——漏记是保守方向（记忆偏少），不会虚构价格。
3. 承诺表是操作者决策参考，不自动改价、不阻断审批；回复仍走草稿→审批→发送链。
4. 数据在 `data/live/price_commitments.json` 与 `product_facts.json`；不要手改，修正通过新事件/新修订追加。
