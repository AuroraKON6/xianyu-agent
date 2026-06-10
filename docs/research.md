# 可融合项目与技能调研

检索时间：2026-04-30

## 已接入本项目的能力

- DeepSeek/OpenAI-compatible 调用层：`xianyu_agent/llm.py`
- 飞书事件入口：`POST /feishu/events`
- 闲鱼/闲管家适配器：`stub` 与 `xianguanjia`
- 每日擦亮：`POLISH_AT`
- 每日 GitHub 机会雷达：`GITHUB_DISCOVERY_ENABLED` + `GITHUB_DISCOVERY_AT`
- URL 下载候选爬虫：授权确认前只提取候选，不下载用于转售
- 虚拟产品比价 Agent：判断授权、回本销量、单笔净利、市场样本和红旗风险
- OpenClaw skill：`skills/xianyu-commerce-agent/SKILL.md`
- OpenClaw manifest：`openclaw/xianyu-commerce-agent.openclaw.yaml`

## 本机 GitHub 扫描结果

命令：

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli discover-github --query "ai agent" --query "agent framework" --query "workflow automation" --min-stars 5000 --limit 5 --no-deepseek
```

最新控制台结果保存到：

```text
data/research/latest-github-scan-console.json
```

| 项目 | Stars | License | 初筛 | 融合建议 |
| --- | ---: | --- | --- | --- |
| https://github.com/obra/superpowers | 172964 | MIT | good | 借鉴 skill/agent 工作法，沉淀成自己的 OpenClaw 技能包 |
| https://github.com/langflow-ai/langflow | 147510 | MIT | good | 可售卖私有化部署、工作流搭建和行业模板服务 |
| https://github.com/langchain-ai/langchain | 135380 | MIT | good | 作为 agent/tooling 基础能力参考，卖集成服务而非倒卖源码 |
| https://github.com/FoundationAgents/MetaGPT | 67555 | MIT | good | 参考多角色协作模式，改造成闲鱼运营/选品/风控分工 |
| https://github.com/TauricResearch/TradingAgents | 55481 | Apache-2.0 | good | 只借鉴多 Agent 分析框架，不用于金融承诺式销售 |
| https://github.com/apache/airflow | 45237 | Apache-2.0 | good | 如果后续任务复杂，可接入做生产级调度 |
| https://github.com/kestra-io/kestra | 26775 | Apache-2.0 | good | 可作为可视化工作流/任务编排替代方案 |
| https://github.com/n8n-io/n8n | 186116 | NOASSERTION | watch | 适合自动化集成服务；商业使用前仔细看许可证 |
| https://github.com/langgenius/dify | 139640 | NOASSERTION | watch | 适合私有化知识库/agent 平台服务；需核对许可证 |
| https://github.com/microsoft/autogen | 57579 | CC-BY-4.0 | watch | 适合参考 agent 协作模式；不能忽略署名和许可证要求 |
| https://github.com/Snailclimb/JavaGuide | 155321 | Apache-2.0 | good | 资料类项目只能做学习/导流/服务，不要原封不动售卖内容 |
| https://github.com/x1xhlol/system-prompts-and-models-of-ai-tools | 136378 | GPL-3.0 | watch | GPL 风险较高，不建议闭源打包售卖 |
| https://github.com/ComposioHQ/awesome-claude-skills | 57082 | 未识别 | reject | 无清晰许可前只做灵感参考，不进入商业化流程 |

## 闲鱼/自动发货参考项目

| 项目 | 可用点 | 风险/备注 |
| --- | --- | --- |
| https://github.com/GuDong2003/xianyu-auto-reply-fix | FastAPI、Web 管理后台、多账号、自动发货、定时擦亮、关键词/AI 回复 | README 声明仅供学习研究；生产使用需确认平台规则和授权 |
| https://github.com/shaxiu/XianyuAutoAgent | 7x24 自动值守、多专家协同、议价/技术/客服 prompt | 更偏客服与议价，不是上架流水线 |
| https://github.com/zhinianboke-new/xianyu-auto-reply | 自动回复系统参考 | 可作为消息监听思路参考，不直接搬到生产 |

## 飞书与平台接入

- 飞书官方 Python SDK：https://github.com/larksuite/oapi-sdk-python
- 飞书事件订阅 URL verification：用于把飞书应用事件打到 `POST /feishu/events`
- DeepSeek API：`https://api.deepseek.com/chat/completions`，本项目按 OpenAI-compatible chat completions 调用
- 闲管家/晓晓权益公开网关线索：`https://hy.ziyuanting.com/`

## 推荐融合顺序

1. 先用 `stub` 跑通飞书、上架、比价、URL 抓取、购买审批、授权资产保存和发货。
2. 接 DeepSeek V4 API，把 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`DEEPSEEK_JSON_MODE` 填进 `.env`。
3. 接飞书事件订阅和机器人 webhook，让审批问题回到飞书。
4. 拿到闲管家授权接口后把 `XIANYU_ADAPTER=xianguanjia`。
5. 再考虑接 Bing/SerpAPI 做图片候选和市场样本搜索。
6. GitHub 项目只做机会雷达和服务灵感，不做原仓库倒卖。

## 不建议直接做的事

- 不复制别人的商品图、宣传图、课程内容、资料包和文案上架。
- 不绕过闲鱼/闲管家的验证码、人机验证、滑块或风控。
- 不把 GitHub 仓库、agent skills、课程资料原封不动打包售卖。
- 不在没有转售授权、素材授权、购买凭证的情况下进入自动下载、自动发货或自动上架。
