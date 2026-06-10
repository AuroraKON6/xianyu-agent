# 实物无货源上架指南

本文档说明如何在现有虚拟商品系统基础上，使用新增的实物无货源功能。

## 一、快速开始

### 1. 评估货源利润

通过 API 或飞书发送货源信息，系统自动计算利润：

**API 方式：**
```bash
POST /sourcing/evaluate
Content-Type: application/json

{
  "title": "商用不锈钢操作台",
  "cost_price": 500,
  "resale_price": 800,
  "shipping_cost": 50,
  "category": "餐饮"
}
```

**飞书方式：**
```
成本：500 售价：800 运费：50 类别：餐饮
```

系统返回：
- 利润率、利润额
- 是否值得做（利润率 >= 30%）
- 推荐发布城市
- 上架策略（标品/非标、引流方式）
- 无痕发货话术

### 2. 生成上架策略

```bash
POST /listings/strategy
Content-Type: application/json

{
  "title": "商用不锈钢操作台",
  "category": "餐饮",
  "actual_price": 800,
  "cost_price": 500,
  "profit_margin": 0.35
}
```

返回：
- **定价策略**：引流价 400 元（实际售价 50%），用"同类产品组合法"
- **发布地址**：成都、重庆、广州
- **首图策略**：场景图（放在餐饮环境里）
- **描述重点**：材质、尺寸、功能、售后

### 3. 创建实物上架

```bash
POST /listings/physical
Content-Type: application/json

{
  "text": "商用不锈钢操作台，餐饮专用，尺寸120cm，成本500售价800",
  "publish": true
}
```

系统自动：
1. 评估货源利润
2. 生成上架策略
3. 创建优化后的上架文案（包含引流策略）
4. 提交到闲鱼（通过配置的 adapter）

## 二、原材料选品法

这是文章中的核心选品方法，跳出平台推荐局限：

```bash
POST /sourcing/material
Content-Type: application/json

{
  "material": "不锈钢"
}
```

系统会生成 15 个搜索关键词：
- 不锈钢商用、不锈钢家用、不锈钢操作台、不锈钢柜子...
- 以及 LLM 扩展的商用/工业产品

然后到拼多多/1688搜索这些关键词，发现蓝海产品。

## 三、供应商管理

### 添加供应商

```bash
POST /sourcing/suppliers
Content-Type: application/json

{
  "name": "佛山不锈钢厨具厂",
  "platform": "1688",
  "contact": "微信：xxx",
  "product_categories": ["餐饮", "厨具"],
  "is_factory_direct": true,
  "notes": "可拿出厂价，支持定制"
}
```

### 查看供应商列表

```bash
GET /sourcing/suppliers
GET /sourcing/suppliers?platform=pinduoduo
```

### 获取无痕发货话术

```bash
GET /sourcing/stealth-templates
GET /sourcing/stealth-templates?scenario=order_note
```

返回 5 套标准话术：
- `order_note`：下单备注（无痕发货）
- `buyer_pdd_logo`：买家发现拼多多 logo
- `buyer_pdd_card`：买家发现好评返现卡
- `buyer_pdd_sms`：买家收到拼多多短信
- `factory_negotiation`：与厂家谈价格

## 四、自动回复改进

系统现在在原有 price/tech/default 基础上，新增 3 种意图识别：

### 1. 讨价还价 (bargain)

买家说："便宜点"、"能少吗"、"打个折"

回复策略（按文章方法）：
- **第 1 次**："老板，少不了的啦，我们都是实价在卖呢"
- **第 2 次**："这样吧，您诚心要，收货返现 20 元，给个好评行不行"
- **底线**：不低于标价的 85%

### 2. 比价应对 (price_compare)

买家说："淘宝更便宜"、"拼多多才 xxx"

回复（按文章话术）：
> "哪里都有高价和低价的，我们主营实体店和线下批发，用料足，不走网商路子，不搞精美图片，主打的就是实实在在，您可以放心拍。"

### 3. 主动介绍 (proactive)

买家只说："在吗"、"你好"

回复策略（不问也答）：
> "在的亲！这款商用不锈钢操作台，不锈钢材质，尺寸 120cm，大家基本上都选的标准款，性价比很高的。需要可以直接拍下~"

## 五、售后处理

### 创建售后工单

```bash
POST /aftersales/cases
Content-Type: application/json

{
  "order_id": "ORDER_123",
  "buyer_id": "BUYER_456",
  "buyer_message": "东西破损了，角上凹了一块",
  "product_title": "不锈钢操作台",
  "product_price": 800,
  "product_size": "large"
}
```

系统自动分类问题并生成回复：

**小破损 (damage_minor)**：
> "非常抱歉亲，运输中造成了小破损...这边给您补偿 20 元红包作为歉意..."

**质量问题退货 (return_quality + large)**：
> "大件商品退货运费较高，我们先安排师傅上门检查维修，如果修不好再退货。"

**买错了 (wrong_purchase + large)**：
> "亲，非质量问题退货需要您承担运费哦。大件退货运费要几百块，建议您挂在闲鱼上处理，能省一点是一点。"

### 查看售后统计

```bash
GET /aftersales/stats
```

返回：总工单数、各状态分布、总赔偿金额、解决率。

## 六、飞书快捷指令

在飞书中直接发送以下消息：

| 指令 | 功能 |
|------|------|
| `成本：500 售价：800 类别：餐饮` | 评估货源利润 |
| `货源：不锈钢` | 原材料选品 |
| `上架策略` | 生成上架策略 |
| `售后：东西破损了` | 创建售后工单 |
| 原有指令仍然可用 | 虚拟商品上架等 |

## 七、双模式运行

现在系统同时支持两种模式：

**虚拟商品模式**（原有）：
- GitHub 项目发现 → 评估版权 → 自动上架 → 自动发货
- 适用于：数字资料、软件、教程

**实物无货源模式**（新增）：
- 原材料选品 → 货源评估 → 上架策略 → 自动回复 → 售后处理
- 适用于：商用厨具、机械、家具、养殖设备等

两种模式独立运行，互不干扰。你可以：
- 账号 A：专门做虚拟商品
- 账号 B：专门做实物无货源
- 账号 C：杂货铺（混合）

## 八、配置文件

在 `.env` 文件中新增以下配置（可选）：

```env
# 实物无货源配置
DEFAULT_PLATFORM_FEE_RATE=0.016
MIN_PROFIT_RATE=0.30
MIN_PROFIT_AMOUNT=20

# 选品配置
PINDUODUO_SEARCH_ENABLED=true
SOURCING_PLATFORMS=pinduoduo,1688
```

## 九、数据存储

新增数据目录结构：

```
data/
├── physical_sourcing/
│   ├── suppliers.json          # 供应商列表
│   └── sourced_products.json   # 已选品记录
├── after_sales/
│   └── cases.json              # 售后工单
└── 原有目录保持不变...
```

## 十、下一步建议

1. **接入 cn-ecommerce-search**：将 `physical_sourcing.py` 中的搜索从 mock 接入真实的拼多多/1688 搜索 API
2. **图片处理**：首图加文字标签、场景合成（需要接入图片生成服务）
3. **自动下单**：对接拼多多 API，实现自动下单+无痕发货备注
4. **价格监控**：定时检查供应商价格变化，自动预警
5. **店铺分析**：统计各 listing 的曝光→咨询→成交转化率，自动淘汰僵尸产品
