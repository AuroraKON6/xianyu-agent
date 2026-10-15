# GLM 第七轮报告：真实公开数据全链演示（含扩充）+ 进程治理

日期：2026-10-12。全量隔离回归 **1587/1587**，exit 0；live 20 文件前后一致（演示使用独立目录，不写 live）。

## 扩充批次（同日第二、三轮累计）

三轮检索补采品类（货架/沥水架/水池/水槽柜/调料台 + 补齐 304 工作台零售），来源档案累计 **W1-W7 批发 + M1-M11 零售 = 18 条观察**（全部带 URL 溯源与原样摘录；区间价取保守上限并注明；无展示价的条目只记存在性不入价目）。`run_expanded.py` 全链运行：7 批发快照 + 11 零售观察 → **7 张商机卡**（此前 2 个 awaiting SKU 已补齐）；审计 0 冲突/0 孤儿。配对结论与假价差排除实录见 [expanded_findings.md](../../research/real-source-demo-2026-10-12/expanded_findings.md)：假价差排除**六度**真实生效（跨材质×2、跨规格×2、缺规格不升级、负价差×2 如实呈现）；**零可行动机会**（利润全 unknown，无一默认成本）；唯一持续为正的同级配对（双星水池 ~17%）成本未知不可行动。控制台以该真实数据渲染的决策卡截图：`console-real-data-decision-card.png` / `console-real-data-full.png`。此为 A61"20 组真实商品对"的公网预研底稿（18 观察/7 配对）。

## 真实公开数据演示（G2C 预演，用户授权"自行找例子"）

**输入档案**：[research/real-source-demo-2026-10-12/sources.md](../../research/real-source-demo-2026-10-12/sources.md)——2026-10-12 经 web_search 对公开网页的逐条原样摘录：批发侧 1688 美星双层 201 ¥360 / 304 双层 ¥473.7；零售展示侧淘宝镜像 ¥131.88 / 加厚款 ¥761。只记录页面实际展示的字段，**未观察到的尺寸/材质/运费一律留空**。

**执行**：`run_demo.py` 在独立目录跑全链（导入→观察→radar→匹配→商机→决策卡）。每道门的表现：

| 环节 | 结果 | 诚实性 |
|---|---|---|
| 供应商导入 | 2 快照（含溯源 URL/观察时间/有效期） | 运费/库存/MOQ 未观察到 → 快照保持 unknown，批次标注"公开网页批发观察（未经确认）" |
| 市场观察 | 2 条（provider=`market:public_web_observation`，attributes 带来源 URL） | 展示价≠成交价（系统语义）；知乎媒体陈述**不入账** |
| 匹配 | **COMPARABLE**（非 EXACT） | 市场侧未展示尺寸/材质 → 硬约束不可对齐，系统拒绝判同款——假价差排除的真数据演示 |
| 商机卡 | researching；保守利润 **unknown** 并列明四项缺失（运费/包装/费率/退货损失） | 无一项用默认值补 |
| 材料用途 | 唯一阻断原因 = `no_active_confirmation` | 溯源（URL/时间/有效期）全过——**唯一缺的就是您的确认**，与设计完全一致 |
| 304 SKU | awaiting_input + 去重提醒（无对应市场观察） | 缺口如实呈现 |
| 审计 | 0 唯一键冲突 / 0 孤儿 / 7 类账本行齐全 | `pipeline_result.json` 存档 |

**验收定位（重要）**：这是**真实公开网页数据**驱动的一次完整流水线运行，不是 R 层验收——R 层需要闲鱼平台可比链接读取与经确认的供应关系（清单见 [gap-checklist](../ops/glm-gap-checklist-2026-10-12.md)）。真实层状态如实记为：市场价=真实公开展示价（非闲鱼），供应商=真实公开报价（未经关系确认）。

## 产品修正（1 处）

`radar_dispatch._run_match` 市场查找从仅认 `market:xianyu_observation` 改为任意 `market:` 前缀 provider（取该 SKU 最新）——公开网页观察是合法市场来源；radar 专项 9/9 回归通过。

## 进程治理（本轮全量回归的一段插曲）

首次全量出现 3 处失败，全部是子进程超时形状。定位：**我此前 G2B 调试遗留的两个 uvicorn 残留进程**（端口 18999，当时的 kill 未生效）拖慢磁盘——server 导入从 ~17s 劣化到 89s（user CPU 仅 0.15s，纯 I/O 等待）。清理后导入恢复 17s，全量重跑 **1587/1587 通过**。教训已记：后台 `&` 启动的服务必须验证端口释放（用户的 wb-restore 进程非我所起，未动）。

## 命令与退出码

```text
python research/real-source-demo-2026-10-12/run_demo.py   # 全链演示（隔离目录）
python scripts/audit_commerce_ledgers.py --data-dir research/real-source-demo-2026-10-12/data  # exit 0
unittest discover -p test_radar_dispatch.py                # 9/9 OK（产品修正回归）
powershell scripts/run_tests_isolated.ps1                 # 1587/1587, exit 0（清理后）
```

零真实模型调用、零平台读写、未写 live、未新增自启动；残留进程已清理。

## 回退方案

删除 research/real-source-demo-2026-10-12/ 即撤销演示；radar 的 provider 放宽为行为兼容扩展（旧 provider 仍在 `market:` 前缀内）。
