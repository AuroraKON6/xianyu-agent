# 安全检查与可融合资源清单

更新时间：2026-05-01

## 当前安全结论

- OpenClaw 全局配置 `C:\Users\轻音\.openclaw\openclaw.json` 已移除硬编码 DeepSeek 密钥；真实密钥应只放在 `.env`、系统环境变量或 OpenClaw 授权配置里。
- `E:\闲鱼\xianyu\.gitignore` 已排除 `.env`、`.venv/`、`data/`、`__pycache__/` 和 `*.pyc`。
- 平台风控守卫已开启：验证码、人机验证、滑块、访问频繁、风控提示会暂停自动化，不做绕过。
- URL 爬虫默认只提取候选链接；只有 `rights_type` 与 `rights_evidence` 证明可下载/可转售时才保存文件。
- 外部商品图片默认仅作参考，不用于发布；需要 `ALLOW_EXTERNAL_PRODUCT_IMAGES=true` 或显式素材授权才会进入上架素材。
- 虚拟产品必须先通过转售权、素材权、购买凭证门禁，才能从机会评估进入上架。

## 已接入的本地组件

运行：

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli components-check
```

覆盖这些路径：

- `E:\openclaw\openclaw-main`：OpenClaw core、gateway、`xianyu-commerce-agent` skill。
- `E:\openclaw-control-center-main\openclaw-control-center-main`：Control Center 源码。
- `E:\openclaw-mission-control-master\openclaw-mission-control-master`：approvals/task backend，可作为后续任务看板。
- `E:\OpenClaw-RL-main\OpenClaw-RL-main`：RL lab，仅用于离线评测和训练，不直接控制真实账号。
- `E:\awesome-openclaw-skills-main\awesome-openclaw-skills-main`：skills 候选池。

## GitHub 项目候选

本地雷达报告：

```text
E:\闲鱼\xianyu\data\research\2026-05-01T03-49-23-github-opportunities.json
```

候选结果：

- [langflow-ai/langflow](https://github.com/langflow-ai/langflow)：MIT，高星 agent/workflow 平台；适合学习可视化工作流、服务化部署与模板化交付。
- [vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser)：Apache-2.0，浏览器自动化 CLI；适合作为合规浏览器验证/测试层。
- [nanobrowser/nanobrowser](https://github.com/nanobrowser/nanobrowser)：Apache-2.0，Chrome extension 多 agent 自动化；可参考交互模式，不用于绕过平台风控。
- [ntegrals/openbrowser](https://github.com/ntegrals/openbrowser)：MIT，browser agent toolkit；可参考隔离浏览器/会话管理。
- [langgenius/dify](https://github.com/langgenius/dify)：许可证风险需人工复查；适合观察，不建议直接融合打包。
- [Snailclimb/JavaGuide](https://github.com/Snailclimb/JavaGuide)：Apache-2.0，资源型项目；只能做学习/培训/部署服务，不可复制资料包转卖。

## Awesome OpenClaw Skills 筛选

优先考虑：

- `clawver-digital-products`：数字商品流程思路。
- `ecommerce-price-watcher`、`shopping-price-drop-coupon-scout`：价格监控/比价框架，可接入利润评估。
- `cn-ecommerce-search`、`dupe`：相似商品/竞品搜索，只作为市场样本来源。
- `feishu-bitable-creator`、`feishu-sheets`：把机会池、授权凭证、上架记录同步到飞书表格。
- `web-search-pro`、`openclaw-free-web-search`：搜索层补强。
- `skillguard-audit`、`skill-security-reviewer`、`pipelock`、`black-box`：技能审计、HTTP 出站过滤、不可篡改日志。

明确不接入或仅隔离研究：

- CAPTCHA 代打、反检测浏览器、隐身代理、绕风控类 skills。
- 盗版下载、未授权 ebook/course 抓取类 skills。
- 任何要求复制卖家图片、文案、课程内容或绕过闲鱼验证的项目。

## 下一步门槛

- 切换真实发布前，需要闲管家授权接口、签名规则、测试账号和回滚方式。
- 飞书上线前，需要公网回调地址、事件订阅 token、自定义机器人 webhook。
- 图片发布前，需要自有图片、供应商授权图或可商用图库来源。
- 云盘保存前，需要指定网盘供应商和授权方式；当前已支持本地保存。
