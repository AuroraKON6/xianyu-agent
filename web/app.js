const state = {
  health: null,
  guard: null,
  memory: null,
  collaboration: { messages: [], discussions: [] },
  workOrders: [],
  executionJobs: [],
  messageIntake: { metrics: {}, health: {}, recent: [], runbooks: [], scheduler: {} },
  replyOutbox: { items: [], summary: {} },
  accountProfiles: [],
  browserAccountImportPreview: null,
  unassignedWorkReview: null,
  commercialLaunchPreview: null,
  commercialCandidates: [],
  resaleCandidates: [],
  discoveryInbox: [],
  lastResult: {},
  evidenceCandidateId: null,
  candidateActivationPacket: null,
  candidateEvidenceArtifacts: [],
  commercialTermsContext: null,
};

const CANDIDATE_EVIDENCE_KIND_LABELS = {
  supplier_quote: "供应商报价",
  supplier_stock: "供应商库存",
  supplier_fulfillment: "供应商履约",
  image_rights: "图片使用权",
  market_baseline: "竞品基线",
};

const SCHEDULER_PRESETS = {
  high_touch: {
    enabled: true,
    interval_seconds: 60,
    max_conversations: 30,
    messages_per_conversation: 30,
  },
  observe: {
    enabled: true,
    interval_seconds: 600,
    max_conversations: 10,
    messages_per_conversation: 10,
  },
  paused: {
    enabled: false,
    interval_seconds: 300,
    max_conversations: 5,
    messages_per_conversation: 10,
  },
};

const $ = (selector) => document.querySelector(selector);

function pretty(data) {
  return JSON.stringify(data, null, 2);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = {};
  }
  if (!response.ok) {
    const detail = data && typeof data.detail === "object" ? data.detail : (data.detail || response.statusText);
    const message = typeof detail === "string"
      ? detail
      : (detail?.message || detail?.error || "请求被拒绝");
    const error = new Error(message);
    error.detail = detail;
    error.status = response.status;
    throw error;
  }
  return data;
}

function renderResult(data) {
  state.lastResult = data;
  $("#result-json").textContent = pretty(data);
  $("#summary").innerHTML = summarize(data);
}

function summarize(data) {
  if (!data || !data.type) {
    return "等待指令";
  }
  const result = data.result || {};
  if (data.type === "error") {
    return [
      `<strong>操作未完成</strong>`,
      escapeHtml(result.message || "请求被拒绝"),
    ].join("<br>");
  }
  if (data.type === "pi_agent") {
    const toolCalls = Array.isArray(result.tool_calls) ? result.tool_calls : [];
    const toolText = toolCalls.length
      ? toolCalls.map((item) => `${item.name || "tool"}:${item.ok === false ? "失败" : "完成"}`).join(" / ")
      : "未调用工具";
    return [
      `<strong>Pi · ${escapeHtml(result.model || "-")}</strong>`,
      escapeHtml(result.message || "Pi 已完成，但未返回文本").replaceAll("\n", "<br>"),
      `工具：${escapeHtml(toolText)} ｜ 外部动作：${result.external_action ? "是" : "否"}`,
    ].join("<br>");
  }
  if (data.type === "memory") {
    return "记忆已保存。后续生成文案、比价和授权判断会带上这些偏好。";
  }
  if (data.type === "listing") {
    const draft = result.draft || {};
    const publish = result.publish_result || {};
    const lines = [
      `<strong>${escapeHtml(draft.title || "已生成商品草稿")}</strong>`,
      `价格：${draft.price ?? "-"} ｜ 库存：${draft.stock ?? "-"}`,
      `交付：${draft.delivery_type || "-"}`,
      `发布：${publish.message ? escapeHtml(publish.message) : "未发布"}`,
    ];
    if (result.collaboration) {
      const discussion = result.collaboration.discussion || {};
      lines.push(`协作：${Object.keys(result.collaboration).join(" / ")}`);
      if (discussion.summary) {
        lines.push(`圆桌讨论：${escapeHtml(discussion.summary).slice(0, 160)}`);
      }
    }
    return lines.join("<br>");
  }
  if (data.type === "agent_discussion") {
    const transcript = result.transcript || [];
    return [
      `<strong>圆桌讨论</strong>`,
      `主题：${escapeHtml(result.topic || "-")}`,
      `参与：${(result.participants || []).map(escapeHtml).join(" / ") || "-"}`,
      `发言：${transcript.length} 轮 ｜ 状态：${transcript.every((turn) => turn.status === "completed") ? "完成" : "有异常"}`,
      result.summary ? `摘要：${escapeHtml(result.summary).slice(0, 220)}` : "",
    ].filter(Boolean).join("<br>");
  }
  if (data.type === "opportunity_evaluation") {
    const economics = result.economics || {};
    const judgement = result.judgement || {};
    return [
      `<strong>机会 ${escapeHtml(result.id || "-")}</strong>`,
      `结论：${escapeHtml(judgement.verdict || "-")} ｜ 授权风险：${escapeHtml(judgement.rights_risk || "-")}`,
      `单笔净利：${economics.net_per_sale ?? "-"} ｜ 回本销量：${economics.break_even_units ?? "-"}`,
    ].join("<br>");
  }
  if (data.type === "url_crawl_opportunity") {
    const crawl = result.crawl || {};
    const opportunity = result.opportunity || {};
    return [
      `<strong>URL 已评估</strong>`,
      `标题：${escapeHtml(crawl.title || "-")}`,
      `下载候选：${(crawl.download_candidates || []).length} ｜ 机会ID：${escapeHtml(opportunity.id || "-")}`,
    ].join("<br>");
  }
  if (data.type === "github_discovery") {
    return `GitHub 机会雷达完成：${result.count || 0} 个候选，报告已保存到 data/research。`;
  }
  if (data.type === "sourcing_evaluation") {
    const economics = result.economics || {};
    return [
      `<strong>货源评估</strong>`,
      `利润：¥${economics.profit}（利润率 ${(economics.profit_margin * 100).toFixed(1)}%）`,
      `类型：${result.is_standard_product ? "标品" : "非标品"}`,
      `城市：${(result.recommended_cities || []).slice(0, 3).join("、")}`,
    ].join("<br>");
  }
  if (data.type === "listing_strategy") {
    const ps = result.price_strategy || {};
    return [
      `<strong>上架策略</strong>`,
      `引流价：¥${ps.bait_price}（${(ps.bait_price_ratio * 100).toFixed(0)}%）`,
      `城市：${result.location_strategy?.primary_city || "-"}`,
      `类型：${result.is_standard ? "标品" : "非标品"}`,
    ].join("<br>");
  }
  if (data.type === "after_sales") {
    return [
      `<strong>售后工单 ${escapeHtml(result.id || "-")}</strong>`,
      `问题：${escapeHtml(result.issue_type || "-")}`,
      `补偿：¥${result.compensation_amount || 0}`,
      `状态：${escapeHtml(result.status || "-")}`,
    ].join("<br>");
  }
  if (data.type === "approval_execution") {
    if (result.ok) {
      return [
        `<strong>已加入执行队列</strong>`,
        `动作：${escapeHtml(result.action || "-")}`,
        `任务：${escapeHtml(result.job_id || "-")}`,
        `状态：${escapeHtml(result.status || "-")}`,
      ].filter(Boolean).join("<br>");
    }
    return `<strong style="color:var(--danger)">入队失败</strong>：${escapeHtml(result.error || "未知错误")}`;
  }
  if (data.type === "approval_decision") {
    const isApproved = result.decision === "approved";
    return [
      `<strong>${isApproved ? "已批准" : "已拒绝"}</strong>`,
      `动作：${escapeHtml(result.action || "-")}`,
      result.decision_note ? `备注：${escapeHtml(result.decision_note)}` : "",
    ].filter(Boolean).join("<br>");
  }
  if (data.type === "execution_job_retry") {
    return `<strong>已重试</strong>：任务 ${escapeHtml(result.job_id || "-")} 已重新加入队列`;
  }
  if (data.type === "execution_job_cancel") {
    return `<strong>已取消</strong>：任务 ${escapeHtml(result.job_id || "-")}`;
  }
  if (data.type === "execution_run_next") {
    const count = result.executed || 0;
    return `<strong>执行队列处理</strong>：${count} 个任务已执行`;
  }
  if (data.type === "account_sync") {
    const orders = result.orders || {};
    const listings = result.listings || {};
    return [
      `<strong>账号同步完成</strong>`,
      `账号：${escapeHtml(result.cookie_id || "-")}`,
      `订单：${orders.persisted || 0}/${orders.fetched || 0}`,
      `商品：${listings.persisted || 0}/${listings.fetched || 0}`,
    ].join("<br>");
  }
  if (data.type === "account_prompt_profile") {
    const missing = result.missing_required_keys || [];
    const unknown = result.unknown_keys || [];
    return [
      `<strong>Prompt 配置${result.ready ? "已就绪" : "需补齐"}</strong>`,
      `账号：${escapeHtml(result.cookie_id || "-")}`,
      `已配置：${escapeHtml((result.configured_keys || []).join(", ") || "none")}`,
      `缺失：${escapeHtml(missing.join(", ") || "none")}`,
      unknown.length ? `未知项：${escapeHtml(unknown.join(", "))}` : "",
    ].filter(Boolean).join("<br>");
  }
  if (data.type === "account_prompt_bootstrap") {
    const missing = result.missing_required_keys || [];
    return [
      `<strong>Prompt 初始化${result.ready ? "完成" : "仍需补齐"}</strong>`,
      `账号：${escapeHtml(result.cookie_id || "-")}`,
      `必需项：${escapeHtml((result.configured_required_keys || []).join(", ") || "none")}`,
      `缺失：${escapeHtml(missing.join(", ") || "none")}`,
      `下一步：跑账号同步，补商品/订单上下文。`,
    ].join("<br>");
  }
  if (data.type === "account_bootstrap") {
    return [
      `<strong>账号已导入</strong>`,
      `账号：${escapeHtml(result.cookie_id || "-")}`,
      `下一步：刷新首轮实盘试跑 gates，并补齐 Prompt / 同步商品订单。`,
    ].join("<br>");
  }
  if (data.type === "browser_login_browser") {
    return [
      `<strong>闲鱼登录浏览器已打开</strong>`,
      `会话：${result.browser_started ? "新启动" : "已连接"}`,
      `状态：${result.ok === false ? "启动失败" : "等待登录"}`,
    ].join("<br>");
  }
  if (data.type === "browser_account_import") {
    return [
      `<strong>浏览器账号${result.operation === "refresh" ? "已刷新" : "已建档"}</strong>`,
      `账号：${escapeHtml(result.cookie_id || "-")}`,
      `会话凭据：${Number(result.cookie_count || 0)} 项 ｜ 状态：${result.enabled ? "enabled" : "disabled"}`,
      `下一步：初始化 Prompt、同步订单商品、记录商业条款。`,
    ].join("<br>");
  }
  if (data.type === "account_conversation_summary") {
    const risks = result.risk_flags || [];
    const recent = result.recent_messages || [];
    const intents = result.intent_counts || {};
    const intentText = Object.entries(intents).map(([key, value]) => `${key}:${value}`).join(", ");
    const recentText = recent.slice(-3)
      .map((message) => `${message.role || "-"}:${message.intent || "-"}:${message.content || ""}`)
      .join(" / ");
    return [
      `<strong>会话摘要</strong>`,
      `账号：${escapeHtml(result.cookie_id || "-")}`,
      `轮次：${Number(result.turns || 0)} ｜ 最近意图：${escapeHtml(result.last_intent || "-")}`,
      `砍价轮次：${Number(result.max_bargain_count || 0)} ｜ 下一步：${escapeHtml(result.next_operator_action || "-")}`,
      `意图分布：${escapeHtml(intentText || "none")}`,
      risks.length ? `风险：${escapeHtml(risks.join(", "))}` : "",
      recentText ? `最近消息：${escapeHtml(recentText)}` : "",
    ].filter(Boolean).join("<br>");
  }
  if (data.type === "message_intake_retry") {
    return result.approval_required
      ? `<strong>重试草稿已创建</strong><br>审批：${escapeHtml((result.intake_record || {}).approval_id || "-")}`
      : `<strong>重试仍需人工处理</strong><br>${escapeHtml(result.generation_error || "没有生成可用草稿")}`;
  }
  if (data.type === "message_intake_retention") {
    return `<strong>消息入站留存已裁剪</strong><br>移除 ${result.removed || 0} 条，保留 ${result.kept || 0} 条`;
  }
  if (data.type === "message_intake_incident_bundle") {
    const alerts = (result.alerts || []).map((alert) => alert.code).filter(Boolean).join(", ") || "none";
    return `<strong>事件包已生成</strong><br>记录 ${((result.recent_intakes || []).length)} 条，告警：${escapeHtml(alerts)}`;
  }
  if (data.type === "message_intake_health_check") {
    const previews = result.notification_previews || [];
    const okText = (result.health || {}).ok ? "healthy" : "alerts";
    return `<strong>Dry-run健康检查完成</strong><br>状态：${escapeHtml(okText)}，通知预览 ${previews.length} 条`;
  }
  if (data.type === "browser_message_import") {
    return [
      `<strong>浏览器消息导入完成</strong>`,
      `created ${result.created || 0} / duplicate ${result.duplicates || 0} / outbound ${result.skipped_outbound || 0} / invalid ${result.skipped_invalid || 0} / failed ${result.failed || 0}`,
      `buyer: ${escapeHtml(result.buyer_id || "-")} · account: ${escapeHtml(result.cookie_id || "-")}`,
    ].join("<br>");
  }
  if (data.type === "browser_conversation_import") {
    return [
      `<strong>未读会话导入完成</strong>`,
      `conversations ${result.conversations_selected || 0}/${result.conversations_total || 0} · skipped read ${result.conversations_skipped_read || 0}`,
      `created ${result.created || 0} / duplicate ${result.duplicates || 0} / outbound ${result.skipped_outbound || 0} / invalid ${result.skipped_invalid || 0} / failed ${result.failed || 0}`,
      `account: ${escapeHtml(result.cookie_id || "-")}`,
    ].join("<br>");
  }
  if (data.type === "message_import_scheduler_run") {
    const importResult = result.import_result || {};
    const scheduler = result.scheduler || {};
    return result.ok
      ? [
          `<strong>消息导入轮询完成</strong>`,
          `created ${importResult.created || 0} / duplicate ${importResult.duplicates || 0} / failed ${importResult.failed || 0}`,
          `run count: ${scheduler.run_count || 0}`,
      ].join("<br>")
      : `<strong style="color:var(--danger)">消息导入轮询失败</strong><br>${escapeHtml(result.error || "unknown error")}`;
  }
  if (data.type === "message_import_scheduler_config") {
    return [
      `<strong>消息导入轮询已${result.enabled ? "启用" : "暂停"}</strong>`,
      `account: ${escapeHtml(result.cookie_id || "-")} · interval: ${result.interval_seconds || 0}s`,
      `max conversations: ${result.max_conversations || 0} · messages: ${result.messages_per_conversation || 0}`,
    ].join("<br>");
  }
  if (data.type === "message_import_scheduler_bootstrap") {
    return [
      `<strong>Scheduler profiles bootstrapped</strong>`,
      `preset: ${escapeHtml(result.preset || "-")} · created ${result.created || 0} · updated ${result.updated || 0} · skipped ${result.skipped || 0}`,
      result.dry_run ? "dry-run only" : "saved to scheduler config",
    ].join("<br>");
  }
  if (data.type === "discovery_inbox_promote") {
    const candidate = result.candidate || {};
    const event = result.event || {};
    return [
      `<strong>线索已提升为候选</strong>`,
      `线索: ${escapeHtml(event.title || event.event_id || "-")}`,
      `候选: ${escapeHtml(candidate.candidate_id || "-")} ｜ ${escapeHtml(candidate.title || "-")}`,
      `供应价: ¥${candidate.supplier_price ?? "-"} → 目标售价: ¥${candidate.target_sale_price ?? "-"}`,
    ].join("<br>");
  }
  if (data.type === "resale_candidate") {
    const c = result.candidate || result;
    return [
      `<strong>候选已录入: ${escapeHtml(c.title || c.candidate_id || "-")}</strong>`,
      `来源: ${escapeHtml(c.source_platform || "-")} → ${escapeHtml(c.sales_platform || "-")}`,
      `供应价: ¥${c.supplier_price ?? "-"} → 目标售价: ¥${c.target_sale_price ?? "-"}`,
      `毛利: ¥${(c.economics || {}).gross_profit ?? "-"} ｜ 风险: ${escapeHtml(c.risk_level || "-")}`,
      c.status === "blocked" ? `<span style="color:var(--danger)">已阻断: ${escapeHtml(c.blocked_reason || "")}</span>` : "",
    ].filter(Boolean).join("<br>");
  }
  if (data.type === "commercial_terms") {
    const assessment = result.assessment || {};
    const candidate = result.candidate || {};
    const economics = candidate.economics || {};
    return [
      `<strong>账号商业条款已保存并重算利润</strong>`,
      `状态: ${assessment.ready ? "通过" : "仍有待补证据"}`,
      `平台费用: ¥${economics.platform_fee ?? "-"} ｜ 预计毛利: ¥${economics.gross_profit ?? "-"}`,
      `待补: ${(assessment.failed_gate_codes || []).map(escapeHtml).join(", ") || "无"}`,
    ].join("<br>");
  }
  if (data.type === "resale_plan") {
    const plan = result.plan || result;
    return [
      `<strong>倒卖计划已创建</strong>`,
      `计划ID: ${escapeHtml(plan.plan_id || "-")}`,
      `状态: ${escapeHtml(plan.status || "-")}`,
      `下一步: 生成采购草稿审批`,
    ].join("<br>");
  }
  if (data.type === "resale_purchase_draft") {
    return [
      `<strong>采购草稿已创建，需人工审批</strong>`,
      result.approval_required ? "⚠ 此操作需要审批后才能继续" : "",
      "已创建人工采购任务，请手动下单",
    ].filter(Boolean).join("<br>");
  }
  if (data.type === "resale_verification") {
    return `<strong>验证结果已记录</strong><br>供应商已验证: ${result.supplier_verified ? "是" : "否"}`;
  }
  if (data.type === "resale_decision") {
    return `<strong>人工决策已更新: ${escapeHtml(result.manual_decision || "-")}</strong>`;
  }
  if (data.type === "resale_listing_draft") {
    const draft = result.listing_draft || {};
    const wo = result.work_order || {};
    return [
      `<strong>上架草稿已创建，需审批</strong>`,
      `草稿ID: ${escapeHtml(draft.draft_id || "-")}`,
      `不会自动发布`,
    ].join("<br>");
  }
  if (data.type === "resale_preflight") {
    return summarizePreflight(result);
  }
  if (data.type === "resale_preflight_blocked") {
    const p = result || {};
    const lines = [`<strong>发布被阻止（409）</strong>`];
    if (p.message) {
      lines.push(`原因: ${escapeHtml(p.message)}`);
    }
    const gates = p.gates || [];
    const failed = gates.filter((g) => g.status === "fail");
    lines.push(`前置检查未通过: ${failed.map((g) => escapeHtml(g.code)).join(", ") || "请先运行发布前检查"}`);
    lines.push(renderPreflightGateList(failed.length ? failed : gates));
    if (p.next_step && p.next_step.length) {
      lines.push(`下一步: ${p.next_step.map((n) => `${escapeHtml(n.code)} — ${escapeHtml(n.message)}`).join("；")}`);
    }
    return lines.join("<br>");
  }
  if (data.type === "resale_agent_review") {
    const d = result.discussion || {};
    const agg = result.aggregate || {};
    const parts = [
      `<strong>Agent 评审完成</strong>`,
      `参与者: ${escapeHtml((d.participants || []).join(", "))}`,
    ];
    if (agg.agent_review_score != null) {
      parts.push(`评分: ${agg.agent_review_score}/100`);
      parts.push(`建议: ${escapeHtml(agg.agent_review_recommendation || "-")}`);
    }
    if (agg.blocking_flags && agg.blocking_flags.length) {
      parts.push(`<span style="color:var(--danger)">阻断: ${escapeHtml(agg.blocking_flags.join(", "))}</span>`);
    }
    parts.push(`摘要: ${escapeHtml(d.summary || "无")}`);
    return parts.join("<br>");
  }
  return escapeHtml(data.type);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatTime(value) {
  if (!value) {
    return "";
  }
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}

function renderCollaborationFeed(messages = [], discussions = []) {
  const feed = $("#collaboration-feed");
  if (!feed) {
    return;
  }
  const items = [];

  discussions.slice(-5).reverse().forEach((discussion) => {
    const participants = (discussion.participants || []).join(" / ");
    const summary = escapeHtml(discussion.summary || "暂无摘要").replaceAll("\n", "<br>");
    items.push(`
      <div class="collab-item">
        <div class="collab-title">圆桌讨论 · ${escapeHtml(discussion.topic || "-")}</div>
        <div class="collab-meta">${escapeHtml(participants || "-")} · ${formatTime(discussion.created_at)}</div>
        <div class="collab-body">${summary}</div>
      </div>
    `);
  });

  messages.slice(-8).reverse().forEach((message) => {
    const route = `${message.from_agent || "-"} → ${message.to_agent || "-"}`;
    const detail = [
      message.action ? `动作：${message.action}` : "",
      message.reason ? `原因：${message.reason}` : "",
      message.status ? `状态：${message.status}` : "",
    ].filter(Boolean).join(" · ");
    items.push(`
      <div class="collab-item">
        <div class="collab-title">Agent 通信 · ${escapeHtml(route)}</div>
        <div class="collab-meta">${escapeHtml(detail || "-")} · ${formatTime(message.created_at)}</div>
      </div>
    `);
  });

  feed.innerHTML = items.length ? items.join("") : `<div class="empty-state">暂无协作记录</div>`;
}

async function refreshCollaboration() {
  const [messages, discussions] = await Promise.all([
    api("/agents/messages?limit=8"),
    api("/agents/discussions?limit=5"),
  ]);
  state.collaboration = { messages, discussions };
  renderCollaborationFeed(messages, discussions);
}

function setSelectOptions(selector, values, allLabel) {
  const select = $(selector);
  if (!select) {
    return;
  }
  const current = select.value;
  const options = [`<option value="">${escapeHtml(allLabel)}</option>`].concat(
    values
      .filter(Boolean)
      .sort()
      .map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`),
  );
  select.innerHTML = options.join("");
  if (values.includes(current)) {
    select.value = current;
  }
}

function syncWorkOrderFilterOptions(workOrders = []) {
  setSelectOptions("#work-order-filter-status", [...new Set(workOrders.map((order) => order.status || ""))], "全部状态");
  setSelectOptions("#work-order-filter-kind", [...new Set(workOrders.map((order) => order.kind || ""))], "全部类型");
  setSelectOptions("#work-order-filter-owner", [...new Set(workOrders.map((order) => order.owner || ""))], "全部负责人");
  setSelectOptions("#work-order-filter-risk", [...new Set(workOrders.map((order) => order.risk_level || ""))], "全部风险");
}

function applyWorkOrderFilters(workOrders = []) {
  const status = $("#work-order-filter-status")?.value || "";
  const kind = $("#work-order-filter-kind")?.value || "";
  const owner = $("#work-order-filter-owner")?.value || "";
  const risk = $("#work-order-filter-risk")?.value || "";
  return workOrders.filter((order) => {
    if (status && order.status !== status) return false;
    if (kind && order.kind !== kind) return false;
    if (owner && order.owner !== owner) return false;
    if (risk && order.risk_level !== risk) return false;
    return true;
  });
}

function renderApprovalCounts(workOrders = []) {
  const target = $("#approval-count-badges");
  if (!target) {
    return;
  }
  const approvals = workOrders.flatMap((order) => order.approvals || []);
  const pending = approvals.filter((item) => item.status === "pending").length;
  const approved = approvals.filter((item) => item.status === "approved").length;
  const failed = approvals.filter((item) => item.execution_status === "failed").length;
  const queued = state.executionJobs.filter((job) => job.status === "queued").length;
  const verificationRequired = state.executionJobs.filter((job) => job.status === "verification_required").length;
  target.innerHTML = [
    `<span class="ops-count ops-count--pending">待审批 ${pending}</span>`,
    `<span class="ops-count ops-count--approved">已批准 ${approved}</span>`,
    `<span class="ops-count ops-count--queued">待执行 ${queued}</span>`,
    verificationRequired ? `<span class="ops-count ops-count--verify">待核验 ${verificationRequired}</span>` : "",
    failed ? `<span class="ops-count ops-count--failed">执行失败 ${failed}</span>` : "",
  ].filter(Boolean).join("");
}

function renderWorkOrderTimeline(order) {
  const events = (order.events || []).slice(-5).reverse();
  if (!events.length) {
    return "";
  }
  return `
    <ol class="work-order-timeline">
      ${events.map((event) => {
        const detail = event.approval_id || event.plan_id || event.action || event.note || event.message || "";
        return `<li><time>${formatTime(event.created_at)}</time><span>${escapeHtml(event.type || "-")}${detail ? ` · ${escapeHtml(detail)}` : ""}</span></li>`;
      }).join("")}
    </ol>
  `;
}

function renderWorkOrderFeed(workOrders = []) {
  const feed = $("#work-order-feed");
  if (!feed) {
    return;
  }
  syncWorkOrderFilterOptions(workOrders);
  renderApprovalCounts(workOrders);
  const filtered = applyWorkOrderFilters(workOrders);
  const items = filtered.slice(-12).reverse().map((order) => {
    const approvals = order.approvals || [];
    const pending = approvals.filter((a) => a.status === "pending");
    const plan = order.action_plan || {};
    const actions = plan.actions || [];
    const meta = [
      `owner: ${order.owner || "-"}`,
      `kind: ${order.kind || "-"}`,
      `status: ${order.status || "-"}`,
      `risk: ${order.risk_level || "-"}`,
      pending.length ? `待审批: ${pending.length}` : "",
      actions.length ? `actions: ${actions.length}` : "",
    ].filter(Boolean).join(" · ");

    const approvalCards = approvals.map(renderApprovalCard).join("");
    const timeline = renderWorkOrderTimeline(order);

    return `
      <div class="work-order-item">
        <div class="collab-title">${escapeHtml(order.title || order.work_order_id || "-")}</div>
        <div class="collab-meta">${escapeHtml(meta)} · ${formatTime(order.updated_at || order.created_at)}</div>
        <div class="collab-body">${escapeHtml(plan.summary || order.kind || "").slice(0, 160)}</div>
        ${approvalCards ? `<div class="approval-list">${approvalCards}</div>` : ""}
        ${timeline}
      </div>
    `;
  });
  feed.innerHTML = items.length ? items.join("") : `<div class="empty-state">暂无匹配任务单</div>`;
}

function renderApprovalCard(approval) {
  const snapshot = approval.draft_snapshot || {};
  const scope = approval.scope_envelope || {};
  const execStatus = approval.execution_status;
  const execResult = approval.execution_result || {};

  // Listing snapshot (title/price/stock)
  const listingSnapshot = snapshot.title
    ? `<div class="approval-snapshot">
        <span class="snapshot-title">${escapeHtml(snapshot.title)}</span>
        ${snapshot.price != null ? `<span class="snapshot-field">¥${snapshot.price}</span>` : ""}
        ${snapshot.stock != null ? `<span class="snapshot-field">库存${snapshot.stock}</span>` : ""}
        ${snapshot.description ? `<span class="snapshot-desc">${escapeHtml(snapshot.description).slice(0, 60)}</span>` : ""}
        ${snapshot.delivery_type ? `<span class="snapshot-field">${escapeHtml(snapshot.delivery_type)}</span>` : ""}
       </div>`
    : "";

  // Delete item snapshot (item_id/reason_summary)
  const deleteItemSnapshot = approval.action === "delete_item" && snapshot.item_id
    ? `<div class="approval-snapshot" data-action="delete_item">
        <span class="snapshot-title">删除商品 ${escapeHtml(snapshot.item_id)}</span>
        ${snapshot.item_title ? `<span class="snapshot-field">${escapeHtml(snapshot.item_title)}</span>` : ""}
        ${snapshot.reason_summary ? `<span class="snapshot-desc">${escapeHtml(snapshot.reason_summary)}</span>` : ""}
        ${snapshot.cookie_id ? `<span class="snapshot-field">账号: ${escapeHtml(snapshot.cookie_id)}</span>` : ""}
        ${snapshot.approval_note ? `<span class="snapshot-field">${escapeHtml(snapshot.approval_note)}</span>` : ""}
       </div>`
    : "";

  // Refund snapshot (order_id/amount_cny/reason_summary)
  const refundSnapshot = approval.action === "refund_order" && snapshot.order_id && snapshot.amount_cny !== undefined
    ? `<div class="approval-snapshot" data-action="refund_order">
        <span class="snapshot-title">退款订单 ${escapeHtml(snapshot.order_id)}</span>
        ${snapshot.amount_cny != null ? `<span class="snapshot-field">金额: ¥${snapshot.amount_cny}</span>` : ""}
        ${snapshot.buyer_id ? `<span class="snapshot-field">买家: ${escapeHtml(snapshot.buyer_id)}</span>` : ""}
        ${snapshot.cookie_id ? `<span class="snapshot-field">账号: ${escapeHtml(snapshot.cookie_id)}</span>` : ""}
        ${snapshot.reason_summary ? `<span class="snapshot-desc">${escapeHtml(snapshot.reason_summary)}</span>` : ""}
        ${snapshot.approval_note ? `<span class="snapshot-field">${escapeHtml(snapshot.approval_note)}</span>` : ""}
       </div>`
    : "";

  // Dispute reply snapshot (dispute_id/message_summary/risk_flags)
  const disputeReplySnapshot = approval.action === "sensitive_reply" && snapshot.dispute_id
    ? `<div class="approval-snapshot" data-action="sensitive_reply">
        <span class="snapshot-title">纠纷 ${escapeHtml(snapshot.dispute_id)}</span>
        ${snapshot.order_id ? `<span class="snapshot-field">订单: ${escapeHtml(snapshot.order_id)}</span>` : ""}
        ${snapshot.buyer_id ? `<span class="snapshot-field">买家: ${escapeHtml(snapshot.buyer_id)}</span>` : ""}
        ${snapshot.cookie_id ? `<span class="snapshot-field">账号: ${escapeHtml(snapshot.cookie_id)}</span>` : ""}
        ${snapshot.message_summary ? `<span class="snapshot-desc">${escapeHtml(snapshot.message_summary)}</span>` : ""}
        ${(snapshot.risk_flags || []).length ? `<span class="snapshot-field" style="color:var(--danger)">风险标记: ${snapshot.risk_flags.length}</span>` : ""}
        ${snapshot.approval_note ? `<span class="snapshot-field">${escapeHtml(snapshot.approval_note)}</span>` : ""}
       </div>`
    : "";

  // Fulfillment snapshot (order_id/delivery_type/cookie_id)
  const fulfillSnapshot = approval.action === "fulfill_order" && snapshot.order_id && !snapshot.title && snapshot.amount_cny === undefined && !snapshot.dispute_id
    ? `<div class="approval-snapshot" data-action="fulfill_order">
        <span class="snapshot-title">订单 ${escapeHtml(snapshot.order_id)}</span>
        ${snapshot.delivery_type ? `<span class="snapshot-field">${escapeHtml(snapshot.delivery_type)}</span>` : ""}
        ${snapshot.delivery_payload_summary ? `<span class="snapshot-field">payload: ${escapeHtml(snapshot.delivery_payload_summary)}</span>` : ""}
        ${snapshot.cookie_id ? `<span class="snapshot-field">账号: ${escapeHtml(snapshot.cookie_id)}</span>` : ""}
       </div>`
    : "";

  // Inbound message snapshot (incoming_message_summary/reply_summary/risk_flags)
  const inboundMessageSnapshot = approval.action === "send_message" && snapshot.buyer_id && snapshot.incoming_message_summary !== undefined && snapshot.reply_summary !== undefined
    ? `<div class="approval-snapshot" data-action="inbound_message">
        <span class="snapshot-title">Buyer ${escapeHtml(snapshot.buyer_id)}</span>
        ${snapshot.buyer_name ? `<span class="snapshot-field">${escapeHtml(snapshot.buyer_name)}</span>` : ""}
        ${snapshot.chat_id ? `<span class="snapshot-field">Chat: ${escapeHtml(snapshot.chat_id)}</span>` : ""}
        ${snapshot.item_id ? `<span class="snapshot-field">Item: ${escapeHtml(snapshot.item_id)}</span>` : ""}
        ${snapshot.cookie_id ? `<span class="snapshot-field">Account: ${escapeHtml(snapshot.cookie_id)}</span>` : ""}
        <span class="snapshot-desc">Incoming: ${escapeHtml(snapshot.incoming_message_summary)}</span>
        <span class="snapshot-desc">Draft: ${escapeHtml(snapshot.reply_summary)}</span>
        ${snapshot.conversation_turns ? `<span class="snapshot-field">Context turns: ${snapshot.conversation_turns}</span>` : ""}
        ${(snapshot.risk_flags || []).length ? `<span class="snapshot-field" style="color:var(--danger)">风险标记: ${snapshot.risk_flags.length}</span>` : ""}
        ${snapshot.approval_note ? `<span class="snapshot-field">${escapeHtml(snapshot.approval_note)}</span>` : ""}
       </div>`
    : "";

  // Send message snapshot (buyer_id/message_summary/risk_flags)
  const sendMessageSnapshot = approval.action === "send_message" && snapshot.buyer_id && snapshot.message_summary !== undefined && !snapshot.order_id && !snapshot.title && !snapshot.dispute_id
    ? `<div class="approval-snapshot" data-action="send_message">
        <span class="snapshot-title">买家 ${escapeHtml(snapshot.buyer_id)}</span>
        ${snapshot.chat_id ? `<span class="snapshot-field">会话: ${escapeHtml(snapshot.chat_id)}</span>` : ""}
        ${snapshot.cookie_id ? `<span class="snapshot-field">账号: ${escapeHtml(snapshot.cookie_id)}</span>` : ""}
        <span class="snapshot-desc">${escapeHtml(snapshot.message_summary)}</span>
        ${(snapshot.risk_flags || []).length ? `<span class="snapshot-field" style="color:var(--danger)">风险标记: ${snapshot.risk_flags.length}</span>` : ""}
       </div>`
    : "";

  // Purchase draft snapshot (supplier_url/specs/quantity/profit_review/risk_warnings)
  const purchaseDraftSnapshot = snapshot.supplier_url && !snapshot.order_id && !snapshot.title && !snapshot.buyer_id
    ? `<div class="approval-snapshot" data-action="prepare_purchase_draft">
        <span class="snapshot-title">采购草稿</span>
        ${snapshot.quantity ? `<span class="snapshot-field">数量: ${snapshot.quantity}</span>` : ""}
        ${snapshot.total_cost != null ? `<span class="snapshot-field">应付: ¥${snapshot.total_cost}</span>` : ""}
        ${snapshot.supplier_url ? `<a class="snapshot-supplier-url" href="${escapeHtml(snapshot.supplier_url)}" target="_blank" rel="noopener">供应商链接</a>` : ""}
        ${snapshot.profit_review ? `<span class="snapshot-profit">毛利: ¥${snapshot.profit_review.unit_gross_profit ?? "-"} (${((snapshot.profit_review.unit_gross_margin || 0) * 100).toFixed(1)}%)</span>` : ""}
        ${(snapshot.risk_warnings || []).length ? `<span class="snapshot-warnings">⚠ ${snapshot.risk_warnings.slice(0, 3).map(escapeHtml).join("; ")}</span>` : ""}
        ${snapshot.product_specs && Object.keys(snapshot.product_specs).length ? `<span class="snapshot-field">规格: ${escapeHtml(JSON.stringify(snapshot.product_specs))}</span>` : ""}
       </div>`
    : "";

  const snapshotInfo = listingSnapshot || deleteItemSnapshot || refundSnapshot || disputeReplySnapshot || fulfillSnapshot || inboundMessageSnapshot || sendMessageSnapshot || purchaseDraftSnapshot;

  const scopeExpired = Number(scope.valid_until || 0) > 0 && Number(scope.valid_until) < Date.now() / 1000;
  const scopeSigned = Boolean(
    approval.approved_action_digest
    && scope.action_digest
    && approval.approved_action_digest === scope.action_digest
  );
  const scopeTargets = Object.entries(scope.target_refs || {})
    .slice(0, 3)
    .map(([key, value]) => `${key}=${value}`)
    .join(" · ");
  const scopeInfo = scope.action_digest
    ? `<div class="approval-scope ${scopeExpired ? "approval-scope--expired" : ""}">
        <span>${scopeExpired ? "批准作用域已过期" : (scopeSigned ? "已绑定批准内容" : "待批准绑定")}</span>
        <span>account=${escapeHtml(scope.cookie_id || "default")}</span>
        ${scopeTargets ? `<span>${escapeHtml(scopeTargets)}</span>` : ""}
        <span>rev=${escapeHtml(String(scope.expected_revision || "").slice(0, 12))}</span>
        <span>digest=${escapeHtml(String(scope.action_digest || "").slice(0, 12))}</span>
        <span>corr=${escapeHtml(String(scope.correlation_id || "").slice(0, 18))}</span>
       </div>`
    : `<div class="approval-scope approval-scope--expired">旧工单无作用域凭证，请重建行动计划</div>`;

  const statusBadge = `<span class="approval-badge approval-badge--${approval.status}">${escapeHtml(approval.status)}</span>`;
  const execBadge = execStatus
    ? `<span class="approval-badge approval-badge--exec-${execStatus}">${escapeHtml(execStatus)}</span>`
    : "";

  let actions = "";
  if (approval.status === "pending") {
    actions = `
      <div class="approval-actions">
        <button class="approve-btn" onclick="decideApproval('${approval.approval_id}', 'approved')">批准</button>
        <button class="reject-btn" onclick="decideApproval('${approval.approval_id}', 'rejected')">拒绝</button>
      </div>`;
  } else if (approval.status === "approved" && !execStatus) {
    actions = `
      <div class="approval-actions">
        <button class="execute-btn" onclick="executeApproval('${approval.approval_id}')">加入执行队列</button>
      </div>`;
  } else if (execStatus === "completed") {
    const extId = execResult.external_id || "";
    actions = `<div class="approval-result approval-result--ok">执行完成${extId ? " · ID: " + escapeHtml(extId) : ""}</div>`;
  } else if (execStatus === "failed") {
    actions = `<div class="approval-result approval-result--fail">执行失败: ${escapeHtml(execResult.error || "未知")}</div>`;
  } else if (execStatus === "running") {
    actions = `<div class="approval-result approval-result--running">执行中...</div>`;
  }

  return `
    <div class="approval-card">
      <div class="approval-header">
        <span class="approval-action">${escapeHtml(approval.action)}</span>
        ${statusBadge}${execBadge}
      </div>
      <div class="approval-meta">agent: ${escapeHtml(approval.agent || "-")} · ${escapeHtml(approval.reason || "")}</div>
      ${scopeInfo}
      ${snapshotInfo}
      ${actions}
    </div>
  `;
}

async function refreshWorkOrders() {
  const workOrders = await api("/work-orders?limit=50");
  state.workOrders = workOrders;
  renderWorkOrderFeed(workOrders);
}

function applyExecutionJobFilters(jobs = []) {
  const status = $("#execution-job-filter-status")?.value || "";
  return jobs.filter((job) => !status || job.status === status);
}

function renderExecutionJobFeed(jobs = []) {
  const feed = $("#execution-job-feed");
  if (!feed) {
    return;
  }
  const filtered = applyExecutionJobFilters(jobs);
  const items = filtered.slice(-16).reverse().map((job) => {
    const error = job.error ? `<div class="execution-job-error">${escapeHtml(job.error)}</div>` : "";
    const result = job.result && job.result.error ? `<div class="execution-job-error">${escapeHtml(job.result.error)}</div>` : "";
    const evidence = job.evidence || {};
    const actions = [
      job.status === "failed" ? `<button class="text-btn" onclick="retryJob('${job.job_id}')">重试</button>` : "",
      job.status === "queued" ? `<button class="text-btn" onclick="cancelJob('${job.job_id}')">取消</button>` : "",
      job.status === "verification_required" ? `<button class="text-btn" onclick="resolveJobVerification('${job.job_id}', 'confirmed_completed')">确认已执行</button>` : "",
      job.status === "verification_required" ? `<button class="text-btn" onclick="resolveJobVerification('${job.job_id}', 'confirmed_not_executed')">确认未执行</button>` : "",
    ].filter(Boolean).join("");
    const verification = job.verification || {};
    const verificationInfo = job.status === "verification_required"
      ? `<div class="execution-job-verification">核验原因：${escapeHtml(verification.reason || "external_result_unknown")} · 禁止直接重试</div>`
      : "";
    const leaseInfo = job.fencing_token
      ? ` · fence: ${Number(job.fencing_token || 0)}`
      : "";
    const evidenceParts = [
      evidence.adapter_status ? `adapter=${evidence.adapter || "browser"}/${evidence.adapter_status}` : "",
      evidence.action_stage ? `stage=${evidence.action_stage}` : "",
      evidence.after_screenshot ? `截图 ${evidence.after_screenshot}` : "",
      evidence.evidence_manifest ? `清单 ${evidence.evidence_manifest}` : "",
      evidence.capture_error ? `证据采集异常 ${evidence.capture_error}` : "",
    ].filter(Boolean);
    const evidenceInfo = evidenceParts.length
      ? `<div class="execution-job-evidence">${escapeHtml(evidenceParts.join(" · "))}</div>`
      : "";
    const jobScope = job.scope_envelope || {};
    const scopeInfo = jobScope.action_digest
      ? `<div class="execution-job-evidence">scope ${escapeHtml(String(jobScope.action_digest).slice(0, 12))} · corr ${escapeHtml(String(jobScope.correlation_id || "").slice(0, 18))}</div>`
      : "";
    return `
      <div class="execution-job-item execution-job-item--${escapeHtml(job.status || "unknown")}">
        <div class="execution-job-title">
          <span>${escapeHtml(job.action || "-")}</span>
          <span class="approval-badge approval-badge--exec-${escapeHtml(job.status || "unknown")}">${escapeHtml(job.status || "-")}</span>
        </div>
        <div class="collab-meta">
          job: ${escapeHtml(job.job_id || "-")} · approval: ${escapeHtml(job.approval_id || "-")} · attempts: ${job.attempts ?? 0}/${job.max_attempts ?? "-"}${leaseInfo}
        </div>
        ${error || result}
        ${scopeInfo}
        ${evidenceInfo}
        ${verificationInfo}
        ${actions ? `<div class="execution-job-actions">${actions}</div>` : ""}
      </div>
    `;
  });
  feed.innerHTML = items.length ? items.join("") : `<div class="empty-state">暂无匹配执行任务</div>`;
  renderApprovalCounts(state.workOrders);
}

async function refreshExecutionJobs() {
  const jobs = await api("/execution-jobs?limit=50");
  state.executionJobs = jobs;
  renderExecutionJobFeed(jobs);
}

function renderMessageImportScheduler(scheduler = {}) {
  const target = $("#message-import-scheduler");
  if (!target) {
    return;
  }
  const last = scheduler.last_result || {};
  const profiles = scheduler.profiles || {};
  const dueProfiles = scheduler.due_profiles || [];
  const status = scheduler.enabled ? (scheduler.due ? "due" : "active") : "paused";
  target.innerHTML = [
    `<span class="snapshot-field">scheduler: ${escapeHtml(status)}</span>`,
    `<span class="snapshot-field">account: ${escapeHtml(scheduler.cookie_id || "-")}</span>`,
    `<span class="snapshot-field">profiles: ${scheduler.total_profiles || Object.keys(profiles).length || 0}</span>`,
    `<span class="snapshot-field">due profiles: ${dueProfiles.length}</span>`,
    `<span class="snapshot-field">interval: ${scheduler.interval_seconds || 0}s</span>`,
    `<span class="snapshot-field">runs: ${scheduler.run_count || 0}</span>`,
    `<span class="snapshot-field">failures: ${scheduler.consecutive_failures || 0}</span>`,
    scheduler.backoff_seconds ? `<span class="snapshot-field">backoff: ${scheduler.backoff_seconds}s</span>` : "",
    scheduler.last_run_at ? `<span class="snapshot-field">last: ${escapeHtml(scheduler.last_run_at)}</span>` : "",
    Object.keys(last).length ? `<span class="snapshot-field">last created: ${last.created || 0}</span>` : "",
    scheduler.last_error ? `<span class="snapshot-field">error: ${escapeHtml(scheduler.last_error)}</span>` : "",
  ].filter(Boolean).join("");
  target.className = `message-intake-health ${scheduler.last_error ? "is-warn" : ""}`;
  renderMessageImportSchedulerProfiles(scheduler);
}

function renderMessageImportSchedulerProfiles(scheduler = {}) {
  const target = $("#message-import-scheduler-profiles");
  if (!target) {
    return;
  }
  const profiles = scheduler.profiles || {};
  const rows = Object.entries(profiles).map(([cookieId, profile]) => {
    const last = profile.last_result || {};
    const status = profile.enabled ? (profile.due ? "due" : "active") : "paused";
    const next = profile.backoff_seconds
      ? `backoff ${Number(profile.backoff_seconds || 0)}s`
      : `interval ${Number(profile.interval_seconds || 0)}s`;
    return `
      <div class="scheduler-profile-row" data-scheduler-cookie-id="${escapeHtml(cookieId)}">
        <div class="scheduler-profile-main">
          <strong>${escapeHtml(profile.cookie_id || cookieId)}</strong>
          <span>${escapeHtml(status)} / ${escapeHtml(next)}</span>
        </div>
        <div class="scheduler-profile-stats">
          <span>runs ${Number(profile.run_count || 0)}</span>
          <span>created ${Number(last.created || 0)}</span>
          <span>failed ${Number(last.failed || 0)}</span>
          <span>failures ${Number(profile.consecutive_failures || 0)}</span>
        </div>
        <div class="scheduler-profile-actions">
          <button type="button" class="text-btn scheduler-profile-run" data-scheduler-cookie-id="${escapeHtml(cookieId)}">run</button>
          <button type="button" class="text-btn scheduler-profile-toggle-action" data-scheduler-cookie-id="${escapeHtml(cookieId)}" data-scheduler-enabled="${profile.enabled ? "false" : "true"}">${profile.enabled ? "pause" : "enable"}</button>
        </div>
      </div>
    `;
  });
  target.innerHTML = rows.length ? rows.join("") : `<div class="empty-state">no scheduler profiles</div>`;
}

function renderMessageIntakeFeed(records = [], metrics = {}, health = {}, runbooks = [], scheduler = {}) {
  renderMessageImportScheduler(scheduler);

  const metricTarget = $("#message-intake-metrics");
  if (metricTarget) {
    metricTarget.innerHTML = [
      `<span class="ops-count ops-count--pending">created ${metrics.created_intakes || 0}</span>`,
      `<span class="ops-count ops-count--approved">pending ${metrics.pending_approvals || 0}</span>`,
      `<span class="ops-count ops-count--queued">duplicates ${metrics.duplicate_intakes || 0}</span>`,
      (metrics.retry_created_intakes || 0)
        ? `<span class="ops-count ops-count--approved">retries ${metrics.retry_created_intakes}</span>`
        : "",
      (metrics.manual_reply_fallbacks || 0)
        ? `<span class="ops-count ops-count--failed">manual ${metrics.manual_reply_fallbacks}</span>`
        : "",
      (metrics.generation_failures || 0)
        ? `<span class="ops-count ops-count--failed">failed ${metrics.generation_failures}</span>`
        : "",
    ].filter(Boolean).join("");
  }

  const healthTarget = $("#message-intake-health");
  if (healthTarget) {
    const status = health.bridge_status || "empty";
    healthTarget.innerHTML = [
      `<span class="snapshot-field">bridge: ${escapeHtml(status)}</span>`,
      `<span class="snapshot-field">last: ${health.last_event_age_seconds == null ? "-" : `${health.last_event_age_seconds}s ago`}</span>`,
      `<span class="snapshot-field">recent: ${health.recent_events || 0}</span>`,
      `<span class="snapshot-field">dup rate: ${Math.round((health.duplicate_rate || 0) * 100)}%</span>`,
    ].join("");
    healthTarget.className = `message-intake-health ${health.ok === false ? "is-warn" : ""}`;
  }

  const alertTarget = $("#message-intake-alerts");
  if (alertTarget) {
    const alerts = health.alerts || [];
    alertTarget.innerHTML = alerts.length
      ? alerts.map((alert) => `
          <div class="message-intake-alert">
            <strong>${escapeHtml(alert.code || "alert")}</strong>
            <span>${escapeHtml(alert.message || "")}</span>
          </div>
        `).join("")
      : `<div class="empty-state">No intake alerts</div>`;
  }

  const runbookTarget = $("#message-intake-runbooks");
  if (runbookTarget) {
    const activeCodes = new Set((health.alerts || []).map((alert) => alert.code));
    const visibleRunbooks = (runbooks || []).filter((runbook) => {
      return activeCodes.size === 0 || activeCodes.has(runbook.code) || (runbook.code === "model_recovery" && activeCodes.has("generation_failures"));
    }).slice(0, 4);
    runbookTarget.innerHTML = visibleRunbooks.length
      ? visibleRunbooks.map((runbook) => `
          <div class="message-intake-runbook">
            <strong>${escapeHtml(runbook.code || "runbook")}</strong>
            <span>${escapeHtml(runbook.title || "")}</span>
            <small>${escapeHtml(((runbook.recovery_steps || [])[0]) || "")}</small>
          </div>
        `).join("")
      : "";
  }

  const feed = $("#message-intake-feed");
  if (!feed) {
    return;
  }
  if (!records.length) {
    feed.innerHTML = `<div class="empty-state">No inbound messages</div>`;
    return;
  }
  feed.innerHTML = records.map((record) => {
    const risk = (record.risk_flags || []).length;
    const meta = [
      record.source || "-",
      record.status || "-",
      record.buyer_id ? `buyer: ${record.buyer_id}` : "",
      record.chat_id ? `chat: ${record.chat_id}` : "",
      risk ? `risk: ${risk}` : "",
    ].filter(Boolean).join(" · ");
    const body = record.status === "duplicate"
      ? `Duplicate of ${escapeHtml(record.duplicate_of || "-")}`
      : [
          record.incoming_message_summary ? `In: ${escapeHtml(record.incoming_message_summary)}` : "",
          record.reply_summary ? `Draft: ${escapeHtml(record.reply_summary)}` : "",
          record.generation_error ? `Error: ${escapeHtml(record.generation_error)}` : "",
        ].filter(Boolean).join("<br>");
    const canRetry = ["manual_reply", "generation_failed", "retry_manual_reply", "retry_generation_failed"].includes(record.status);
    const actions = canRetry
      ? `<div class="message-intake-actions">
          <button class="text-btn" onclick="retryMessageIntake('${escapeHtml(record.intake_id)}')">重试草稿</button>
        </div>`
      : "";
    return `
      <div class="collab-item message-intake-item">
        <div class="collab-title">${escapeHtml(record.work_order_id || record.intake_id || "-")}</div>
        <div class="collab-meta">${escapeHtml(meta)} · ${formatTime(record.created_at)}</div>
        <div class="collab-body">${body || "-"}</div>
        ${actions}
      </div>
    `;
  }).join("");
}

const REPLY_OUTBOX_STATUS_LABELS = {
  draft: "草稿",
  pending_approval: "待审批",
  ready_to_enqueue: "待入队",
  queued: "已排队",
  running: "发送中",
  verification_required: "待核验",
  failed: "失败",
  sent: "已发送",
  rejected: "已拒绝",
  cancelled: "已取消",
  completed_without_receipt: "缺少回执",
};

function renderReplyOutbox(data = {}) {
  const items = Array.isArray(data.items) ? data.items : [];
  const summary = data.summary || {};
  state.replyOutbox = { items, summary };
  const metricTarget = $("#reply-outbox-metrics");
  if (metricTarget) {
    const counts = summary.counts || {};
    metricTarget.innerHTML = [
      `<span class="ops-count ops-count--pending">待审批 ${counts.pending_approval || 0}</span>`,
      `<span class="ops-count ops-count--approved">待入队 ${counts.ready_to_enqueue || 0}</span>`,
      `<span class="ops-count ops-count--queued">排队/发送 ${(counts.queued || 0) + (counts.running || 0)}</span>`,
      (counts.verification_required || 0) ? `<span class="ops-count ops-count--verify">待核验 ${counts.verification_required}</span>` : "",
      (counts.failed || 0) ? `<span class="ops-count ops-count--failed">失败 ${counts.failed}</span>` : "",
      `<span class="ops-count ops-count--approved">已发送 ${counts.sent || 0}</span>`,
    ].filter(Boolean).join("");
  }

  const feed = $("#reply-outbox-feed");
  if (!feed) return;
  const statusFilter = $("#reply-outbox-filter-status")?.value || "";
  const filtered = items.filter((item) => !statusFilter || item.status === statusFilter);
  if (!filtered.length) {
    feed.innerHTML = `<div class="empty-state">暂无匹配回复</div>`;
    return;
  }
  feed.innerHTML = filtered.slice(0, 30).map((item) => {
    const status = item.status || "draft";
    const label = REPLY_OUTBOX_STATUS_LABELS[status] || status;
    const receipt = item.receipt || {};
    const verification = item.verification || {};
    const executionEvidence = item.execution_evidence || {};
    const actions = [
      status === "pending_approval" ? `<button class="text-btn" onclick="decideApproval('${item.approval_id}', 'approved')">批准</button>` : "",
      status === "pending_approval" ? `<button class="text-btn" onclick="decideApproval('${item.approval_id}', 'rejected')">拒绝</button>` : "",
      status === "ready_to_enqueue" ? `<button class="text-btn" onclick="enqueueApproval('${item.approval_id}')">入队</button>` : "",
      status === "queued" ? `<button class="text-btn" onclick="cancelJob('${item.execution_job_id}')">取消</button>` : "",
      status === "failed" ? `<button class="text-btn" onclick="retryJob('${item.execution_job_id}')">重试</button>` : "",
      status === "verification_required" ? `<button class="text-btn" onclick="resolveJobVerification('${item.execution_job_id}', 'confirmed_completed')">确认已发送</button>` : "",
      status === "verification_required" ? `<button class="text-btn" onclick="resolveJobVerification('${item.execution_job_id}', 'confirmed_not_executed')">确认未发送</button>` : "",
    ].filter(Boolean).join("");
    const receiptLine = receipt.external_message_id || receipt.page_url || receipt.manual_confirmation
      ? `<div class="collab-meta">回执：${escapeHtml(receipt.external_message_id || receipt.page_url || "人工核验")}</div>`
      : "";
    const verificationLine = status === "verification_required"
      ? `<div class="execution-job-verification">${escapeHtml(verification.reason || "external_result_unknown")} · 先核对闲鱼聊天记录</div>`
      : "";
    const evidenceLine = executionEvidence.after_screenshot || executionEvidence.evidence_manifest
      ? `<div class="execution-job-evidence">${escapeHtml(executionEvidence.after_screenshot || executionEvidence.evidence_manifest)}</div>`
      : "";
    const binding = [
      item.cookie_id ? `account=${item.cookie_id}` : "",
      item.buyer_id ? `buyer=${item.buyer_id}` : "",
      item.chat_id ? `chat=${item.chat_id}` : "",
      item.item_id ? `item=${item.item_id}` : "",
      item.reply_revision ? `rev=${item.reply_revision}` : "",
      item.action_digest ? `digest=${item.action_digest.slice(0, 12)}` : "",
    ].filter(Boolean).join(" · ");
    return `
      <div class="collab-item reply-outbox-item">
        <div class="execution-job-title">
          <span>${escapeHtml(item.message_summary || "-")}</span>
          <span class="approval-badge approval-badge--exec-${escapeHtml(status)}">${escapeHtml(label)}</span>
        </div>
        <div class="collab-meta">${escapeHtml(item.action || "send_message")} · ${formatTime(item.created_at)} · attempts ${Number(item.attempts || 0)}</div>
        <div class="reply-outbox-binding">${escapeHtml(binding || item.idempotency_key || "-")}</div>
        ${receiptLine}
        ${evidenceLine}
        ${verificationLine}
        ${item.error ? `<div class="execution-job-error">${escapeHtml(item.error)}</div>` : ""}
        ${actions ? `<div class="execution-job-actions">${actions}</div>` : ""}
      </div>
    `;
  }).join("");
}

async function refreshMessageIntake() {
  const [metrics, health, recent, runbookData, scheduler, replyOutbox] = await Promise.all([
    api("/message-intake/metrics"),
    api("/message-intake/health"),
    api("/message-intake/recent?limit=12"),
    api("/message-intake/runbooks"),
    api("/message-intake/import-scheduler"),
    api("/reply-outbox?limit=100"),
  ]);
  const runbooks = runbookData.runbooks || [];
  state.messageIntake = { metrics, health, recent, runbooks, scheduler };
  renderMessageIntakeFeed(recent, metrics, health, runbooks, scheduler);
  renderReplyOutbox(replyOutbox);
}

function renderAccountProfile(profile) {
  const counts = profile.counts || {};
  const caps = profile.capabilities || {};
  const ai = profile.ai || {};
  const actions = (profile.recommended_next_actions || []).slice(0, 3);
  const statusClass = profile.enabled ? "approval-badge--approved" : "approval-badge--rejected";
  const statusText = profile.enabled ? "enabled" : "disabled";
  const capabilityBadges = [
    caps.seller_auto_reply ? "seller reply" : "",
    caps.keyword_reply ? "keywords" : "",
    caps.ai_reply ? "AI" : "",
    caps.order_fulfillment_tracking ? "orders" : "",
    caps.listing_inventory_tracking ? "items" : "",
  ].filter(Boolean);
  const cookieId = profile.cookie_id || "";
  const promptTools = cookieId ? `
    <div class="account-profile-tools">
      <button type="button" data-cookie-id="${escapeHtml(cookieId)}" onclick="checkAccountPromptProfile(this.dataset.cookieId)">检查 Prompt</button>
      <button type="button" data-cookie-id="${escapeHtml(cookieId)}" onclick="bootstrapAccountPromptProfile(this.dataset.cookieId)">初始化 Prompt</button>
      <button type="button" data-cookie-id="${escapeHtml(cookieId)}" onclick="checkAccountConversationSummary(this.dataset.cookieId)">会话摘要</button>
    </div>
  ` : "";
  return `
    <div class="account-profile-card">
      <div class="account-profile-title">
        <strong>${escapeHtml(profile.cookie_id || "-")}</strong>
        <span class="approval-badge ${statusClass}">${statusText}</span>
      </div>
      <div class="collab-meta">${escapeHtml(profile.remark || "no remark")}</div>
      <div class="account-profile-stats">
        <span class="snapshot-field">rules ${counts.keyword_rules || 0}</span>
        <span class="snapshot-field">items ${counts.items || 0}</span>
        <span class="snapshot-field">orders ${counts.orders_total || 0}</span>
        <span class="snapshot-field">pause ${profile.pause_duration || 0}m</span>
      </div>
      <div class="account-profile-stats">
        <span class="snapshot-field">AI ${ai.enabled ? escapeHtml(ai.model_name || "on") : "off"}</span>
        <span class="snapshot-field">prompts ${(ai.custom_prompt_keys || []).length}</span>
        <span class="snapshot-field">auto-confirm ${profile.auto_confirm ? "on" : "off"}</span>
      </div>
      <div class="account-profile-tags">
        ${capabilityBadges.map((badge) => `<span class="risk-flag">${escapeHtml(badge)}</span>`).join("") || '<span class="snapshot-field">not ready</span>'}
      </div>
      ${promptTools}
      <ul class="account-profile-actions">
        ${actions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </div>
  `;
}

function renderAccountProfiles(profiles = []) {
  const target = $("#account-profile-list");
  if (!target) {
    return;
  }
  target.innerHTML = profiles.length
    ? profiles.map(renderAccountProfile).join("")
    : `<div class="empty-state">暂无账号画像</div>`;
}

async function loadAccountProfiles() {
  const profiles = await api("/accounts/profiles");
  state.accountProfiles = profiles;
  renderAccountProfiles(profiles);
}

const refreshAccountProfiles = loadAccountProfiles;

async function checkAccountPromptProfile(cookieId) {
  if (!cookieId) return;
  try {
    const result = await api(`/accounts/${encodeURIComponent(cookieId)}/prompt-profile`);
    renderResult({ type: "account_prompt_profile", result });
  } catch (error) {
    alert(`Prompt 检查失败：${error.message}`);
  }
}

async function bootstrapAccountPromptProfile(cookieId) {
  if (!cookieId) return;
  if (!confirm(`为账号 ${cookieId} 初始化必需 Prompt？已有自定义 Prompt 会保留。`)) {
    return;
  }
  try {
    const result = await api(`/accounts/${encodeURIComponent(cookieId)}/prompt-profile/bootstrap`, {
      method: "POST",
    });
    renderResult({ type: "account_prompt_bootstrap", result });
    await loadAccountProfiles();
    await refreshOPCDashboard();
  } catch (error) {
    alert(`Prompt 初始化失败：${error.message}`);
  }
}

async function checkAccountConversationSummary(cookieId) {
  if (!cookieId) return;
  const chatId = prompt("chat_id", "");
  if (chatId === null) return;
  const trimmed = chatId.trim();
  if (!trimmed) return;
  try {
    const result = await api(`/accounts/${encodeURIComponent(cookieId)}/conversation-summary?chat_id=${encodeURIComponent(trimmed)}`);
    renderResult({ type: "account_conversation_summary", result });
  } catch (error) {
    alert(`会话摘要失败：${error.message}`);
  }
}

async function submitAccountBootstrapForm(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const fd = new FormData(form);
  const payload = {
    cookie_id: String(fd.get("cookie_id") || "").trim(),
    cookie_value: String(fd.get("cookie_value") || "").trim(),
    remark: String(fd.get("remark") || "").trim(),
  };
  if (!payload.cookie_id || !payload.cookie_value) {
    alert("账号ID和Cookie不能为空");
    return;
  }
  try {
    const result = await api("/accounts", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderResult({ type: "account_bootstrap", result });
    form.reset();
    await loadAccountProfiles();
    await refreshOPCDashboard();
  } catch (error) {
    alert(`账号导入失败：${error.message}`);
  }
}

function renderBrowserAccountImportPreview(preview = null) {
  const target = $("#browser-account-import-preview");
  if (!target) return;
  if (!preview?.preview_token) {
    target.hidden = true;
    target.innerHTML = "";
    return;
  }
  const operation = preview.operation === "refresh" ? "刷新已有账号" : "新建账号";
  const expiresAt = preview.expires_at
    ? new Date(Number(preview.expires_at) * 1000).toLocaleTimeString("zh-CN", { hour12: false })
    : "-";
  target.hidden = false;
  target.innerHTML = `
    <div class="browser-account-preview-head">
      <strong>检测结果</strong>
      <span class="approval-badge ${preview.existing_account ? "approval-badge--approved" : "approval-badge--pending"}">${operation}</span>
    </div>
    <div class="browser-account-preview-grid">
      <span><small>账号</small><strong>${escapeHtml(preview.account_id || "-")}</strong></span>
      <span><small>名称</small><strong>${escapeHtml(preview.display_name || "未读取")}</strong></span>
      <span><small>会话</small><strong>${Number(preview.cookie_count || 0)} 项 · ${escapeHtml(preview.session_ref || "-")}</strong></span>
      <span><small>有效至</small><strong>${escapeHtml(expiresAt)}</strong></span>
    </div>
    <div class="browser-account-preview-confirm">
      <code>${escapeHtml(preview.confirmation_phrase || "")}</code>
      <button id="commit-browser-account" type="button" class="primary" onclick="commitBrowserAccountImport()">确认绑定</button>
    </div>
  `;
}

async function openXianyuLoginBrowser() {
  const button = $("#open-xianyu-login-browser");
  if (button) button.disabled = true;
  try {
    const result = await api("/accounts/browser-import/open", { method: "POST" });
    renderResult({ type: "browser_login_browser", result });
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  } finally {
    if (button) button.disabled = false;
  }
}

function browserAccountImportErrorMessage(error) {
  const code = error?.detail?.error || "";
  const messages = {
    browser_adapter_inactive: "当前未启用浏览器适配器，账号未写入。",
    browser_cdp_unavailable: "请先点击“打开登录浏览器”，完成闲鱼登录后再检测。",
    browser_login_cookies_missing: "当前浏览器尚未完成闲鱼登录，请登录后重新检测。",
    browser_login_not_verified: "闲鱼未确认当前登录状态，请在登录浏览器中重新登录。",
    browser_login_probe_failed: "闲鱼登录状态检测暂时失败，请检查页面后重试。",
    account_import_preview_expired: "账号检测结果已过期，请重新检测后再绑定。",
    browser_account_changed: "浏览器中的账号已变化，请重新检测并核对账号。",
    browser_session_changed: "浏览器登录会话已刷新，请重新检测后再绑定。",
    account_import_commit_in_progress: "这个账号正在绑定，请稍后刷新账号画像。",
    account_import_confirmation_mismatch: "确认语句不匹配，账号未写入。",
  };
  return messages[code] || error?.message || "账号接入失败";
}

async function previewBrowserAccountImport() {
  const button = $("#preview-browser-account");
  if (button) button.disabled = true;
  state.browserAccountImportPreview = null;
  renderBrowserAccountImportPreview();
  try {
    const preview = await api("/accounts/browser-import/preview", { method: "POST" });
    state.browserAccountImportPreview = preview;
    renderBrowserAccountImportPreview(preview);
  } catch (error) {
    renderResult({
      type: "error",
      result: { message: browserAccountImportErrorMessage(error), detail: error.detail },
    });
  } finally {
    if (button) button.disabled = false;
  }
}

async function commitBrowserAccountImport() {
  const preview = state.browserAccountImportPreview;
  if (!preview?.preview_token) return;
  const expected = preview.confirmation_phrase || `BIND ${preview.preview_token.slice(0, 8).toUpperCase()}`;
  const confirmation = prompt(`输入 ${expected} 确认绑定账号 ${preview.account_id}`, "");
  if (confirmation === null) return;
  if (confirmation.trim() !== expected) {
    alert("确认语句不匹配，账号未写入。");
    return;
  }
  const button = $("#commit-browser-account");
  if (button) button.disabled = true;
  const remark = String($("#account-bootstrap-form")?.elements?.remark?.value || "").trim();
  try {
    const result = await api("/accounts/browser-import/commit", {
      method: "POST",
      body: JSON.stringify({
        preview_token: preview.preview_token,
        confirmation,
        remark,
      }),
    });
    state.browserAccountImportPreview = null;
    renderBrowserAccountImportPreview();
    renderResult({ type: "browser_account_import", result });
    await loadAccountProfiles();
    await refreshOPCDashboard();
    await refreshCommercialTrials();
  } catch (error) {
    renderResult({
      type: "error",
      result: { message: browserAccountImportErrorMessage(error), detail: error.detail },
    });
    if (button) button.disabled = false;
  }
}

async function retryMessageIntake(intakeId) {
  if (!confirm("确认重新生成草稿？只会创建待审批回复，不会直接发送。")) {
    return;
  }
  try {
    const result = await api(`/message-intake/${intakeId}/retry`, { method: "POST" });
    renderResult({ type: "message_intake_retry", result });
    await refreshStatus();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function pruneMessageIntake() {
  const days = prompt("保留最近多少天的消息入站账本？", "14");
  if (days === null) return;
  const maxRecords = prompt("最多保留多少条记录？", "5000");
  if (maxRecords === null) return;
  try {
    const result = await api("/message-intake/retention/prune", {
      method: "POST",
      body: JSON.stringify({
        retention_days: Number(days) || 14,
        max_records: Number(maxRecords) || 5000,
      }),
    });
    renderResult({ type: "message_intake_retention", result });
    await refreshMessageIntake();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function exportMessageIntakeBundle() {
  try {
    const result = await api("/message-intake/incident-bundle?limit=20");
    renderResult({ type: "message_intake_incident_bundle", result });
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function runMessageIntakeDryRun() {
  try {
    const result = await api("/message-intake/health-check/dry-run", {
      method: "POST",
      body: JSON.stringify({
        window_minutes: 60,
        stale_after_minutes: 30,
        notification_hooks: ["console", "local_file"],
      }),
    });
    renderResult({ type: "message_intake_health_check", result });
    await refreshMessageIntake();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function importBrowserMessages() {
  const cookieId = prompt("cookie_id", "default");
  if (cookieId === null) return;
  const buyerId = prompt("buyer_id (required)", "");
  if (buyerId === null) return;
  if (!buyerId.trim()) {
    alert("buyer_id is required");
    return;
  }
  const buyerName = prompt("buyer_name (optional)", "") || "";
  const chatId = prompt("chat_id (optional)", "") || "";
  const itemId = prompt("item_id (optional)", "") || "";
  try {
    const result = await api("/message-intake/import-browser", {
      method: "POST",
      body: JSON.stringify({
        cookie_id: cookieId.trim() || "default",
        buyer_id: buyerId.trim(),
        buyer_name: buyerName.trim(),
        chat_id: chatId.trim(),
        item_id: itemId.trim(),
      }),
    });
    renderResult({ type: "browser_message_import", result });
    await refreshMessageIntake();
    await refreshWorkOrders();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function importBrowserConversations() {
  const cookieId = prompt("cookie_id", "default");
  if (cookieId === null) return;
  const maxConversations = prompt("max unread conversations", "20");
  if (maxConversations === null) return;
  const messagesPerConversation = prompt("messages per conversation", "20");
  if (messagesPerConversation === null) return;
  const includeRead = confirm("Include read conversations too? Cancel keeps unread-only mode.");
  try {
    const result = await api("/message-intake/import-browser-conversations", {
      method: "POST",
      body: JSON.stringify({
        cookie_id: cookieId.trim() || "default",
        include_read: includeRead,
        max_conversations: Number(maxConversations) || 20,
        messages_per_conversation: Number(messagesPerConversation) || 20,
      }),
    });
    renderResult({ type: "browser_conversation_import", result });
    await refreshMessageIntake();
    await refreshWorkOrders();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function configureMessageImportScheduler() {
  const current = state.messageIntake.scheduler || {};
  const enabled = confirm("Enable browser message import polling? Cancel disables it.");
  const cookieId = prompt("cookie_id", current.cookie_id || "default");
  if (cookieId === null) return;
  const interval = prompt("interval seconds", String(current.interval_seconds || 300));
  if (interval === null) return;
  const maxConversations = prompt("max unread conversations per run", String(current.max_conversations || 20));
  if (maxConversations === null) return;
  const messagesPerConversation = prompt("messages per conversation", String(current.messages_per_conversation || 20));
  if (messagesPerConversation === null) return;
  const includeRead = confirm("Include read conversations too? Cancel keeps unread-only mode.");
  try {
    const result = await api("/message-intake/import-scheduler", {
      method: "POST",
      body: JSON.stringify({
        enabled,
        cookie_id: cookieId.trim() || "default",
        interval_seconds: Number(interval) || 300,
        include_read: includeRead,
        max_conversations: Number(maxConversations) || 20,
        messages_per_conversation: Number(messagesPerConversation) || 20,
      }),
    });
    renderResult({ type: "message_import_scheduler_config", result });
    state.messageIntake.scheduler = result;
    renderMessageImportScheduler(result);
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function runMessageImportSchedulerNow() {
  try {
    const result = await api("/message-intake/import-scheduler/run-now", { method: "POST" });
    renderResult({ type: "message_import_scheduler_run", result });
    await refreshMessageIntake();
    await refreshWorkOrders();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function submitMessageImportSchedulerProfileForm(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const cookieId = String(data.get("scheduler_cookie_id") || "").trim();
  if (!cookieId) {
    alert("cookie_id is required");
    return;
  }
  try {
    const result = await api("/message-intake/import-scheduler", {
      method: "POST",
      body: JSON.stringify({
        enabled: data.get("scheduler_enabled") === "on",
        cookie_id: cookieId,
        interval_seconds: Number(data.get("scheduler_interval_seconds")) || 300,
        max_conversations: Number(data.get("scheduler_max_conversations")) || 20,
        messages_per_conversation: Number(data.get("scheduler_messages_per_conversation")) || 20,
      }),
    });
    renderResult({ type: "message_import_scheduler_config", result });
    state.messageIntake.scheduler = result;
    renderMessageImportScheduler(result);
    await refreshMessageIntake();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

function applyMessageImportSchedulerPreset(presetName) {
  const form = $("#message-import-scheduler-profile-form");
  const preset = SCHEDULER_PRESETS[presetName] || SCHEDULER_PRESETS.high_touch;
  if (!form) {
    return;
  }
  form.elements.scheduler_preset.value = presetName in SCHEDULER_PRESETS ? presetName : "high_touch";
  form.elements.scheduler_interval_seconds.value = String(preset.interval_seconds);
  form.elements.scheduler_max_conversations.value = String(preset.max_conversations);
  form.elements.scheduler_messages_per_conversation.value = String(preset.messages_per_conversation);
  form.elements.scheduler_enabled.checked = Boolean(preset.enabled);
}

async function bootstrapMessageImportSchedulerProfiles() {
  const form = $("#message-import-scheduler-profile-form");
  const preset = form?.elements.scheduler_preset?.value || "observe";
  const overwrite = Boolean(form?.elements.scheduler_overwrite?.checked);
  const message = overwrite
    ? `Bootstrap scheduler profiles from accounts with ${preset} preset and overwrite existing profiles?`
    : `Bootstrap scheduler profiles from accounts with ${preset} preset? Existing profiles will be skipped.`;
  if (!confirm(message)) {
    return;
  }
  try {
    const result = await api("/message-intake/import-scheduler/bootstrap-accounts", {
      method: "POST",
      body: JSON.stringify({ preset, overwrite }),
    });
    renderResult({ type: "message_import_scheduler_bootstrap", result });
    state.messageIntake.scheduler = result.scheduler || {};
    renderMessageImportScheduler(state.messageIntake.scheduler);
    await refreshMessageIntake();
    await refreshOPCDashboard();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function runMessageImportSchedulerProfile(cookieId) {
  const targetCookieId = String(cookieId || "").trim();
  if (!targetCookieId) {
    return;
  }
  try {
    const result = await api(`/message-intake/import-scheduler/run-now?cookie_id=${encodeURIComponent(targetCookieId)}`, {
      method: "POST",
    });
    renderResult({ type: "message_import_scheduler_run", result });
    await refreshMessageIntake();
    await refreshWorkOrders();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function toggleMessageImportSchedulerProfile(cookieId, enabled) {
  const targetCookieId = String(cookieId || "").trim();
  if (!targetCookieId) {
    return;
  }
  const scheduler = state.messageIntake.scheduler || {};
  const profile = (scheduler.profiles || {})[targetCookieId] || {};
  try {
    const result = await api("/message-intake/import-scheduler", {
      method: "POST",
      body: JSON.stringify({
        enabled: Boolean(enabled),
        cookie_id: targetCookieId,
        interval_seconds: Number(profile.interval_seconds || scheduler.interval_seconds || 300),
        include_read: Boolean(profile.include_read),
        max_conversations: Number(profile.max_conversations || 20),
        messages_per_conversation: Number(profile.messages_per_conversation || 20),
        item_id: profile.item_id || "",
      }),
    });
    renderResult({ type: "message_import_scheduler_config", result });
    state.messageIntake.scheduler = result;
    renderMessageImportScheduler(result);
    await refreshMessageIntake();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function refreshStatus() {
  const [health, guard, memory] = await Promise.all([
    api("/health"),
    api("/guard/status"),
    api("/memory"),
  ]);
  state.health = health;
  state.guard = guard;
  state.memory = memory;
  $("#status-ok").textContent = health.ok ? "在线" : "异常";
  $("#status-model").textContent = health.model || "-";
  $("#status-adapter").textContent = health.adapter || "-";
  $("#status-memory").textContent = memory.updated_at || "-";
  $("#guard-line").textContent = guard.paused
    ? `已暂停：${guard.pause_reason || "需要人工处理"}`
    : "风控守卫正常，遇到验证会暂停";
  $("#guard-line").className = guard.paused ? "is-warn" : "";
  renderMemory(memory);
  await refreshCollaboration();
  await refreshExecutionJobs();
  await refreshWorkOrders();
  await refreshMessageIntake();
  await refreshAccountProfiles();
}

function renderMemory(memory) {
  const notes = memory.notes || [];
  const list = $("#memory-list");
  if (!notes.length) {
    list.innerHTML = `<div class="memory-note">还没有额外偏好。你可以写：记忆：以后标题更短一点。</div>`;
    return;
  }
  list.innerHTML = notes
    .slice(-12)
    .reverse()
    .map(
      (note) => `<div class="memory-note">${escapeHtml(note.text || "")}<time>${escapeHtml(note.created_at || "")}</time></div>`,
    )
    .join("");
}

async function sendCommand() {
  const text = $("#command-input").value.trim();
  if (!text) {
    renderResult({ type: "error", result: { message: "请输入指令" } });
    return;
  }
  $("#send-btn").disabled = true;
  $("#send-btn").textContent = "处理中";
  try {
    const data = await api("/agent/message", {
      method: "POST",
      body: JSON.stringify({ text, publish: $("#publish-toggle").checked }),
    });
    renderResult(data);
    await refreshStatus();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  } finally {
    $("#send-btn").disabled = false;
    $("#send-btn").textContent = "发送给Pi";
  }
}

async function saveMemory() {
  const text = $("#memory-input").value.trim();
  if (!text) {
    return;
  }
  const memory = await api("/memory", {
    method: "POST",
    body: JSON.stringify({ text, source: "console" }),
  });
  $("#memory-input").value = "";
  renderResult({ type: "memory", result: memory });
  await refreshStatus();
}

async function polish() {
  renderResult({ type: "polish", result: await api("/tasks/polish", { method: "POST", body: "{}" }) });
  await refreshStatus();
}

async function resumeGuard() {
  renderResult({
    type: "guard_resume",
    result: await api("/guard/resume", {
      method: "POST",
      body: JSON.stringify({ note: "console resume" }),
    }),
  });
  await refreshStatus();
}

async function executeApproval(approvalId) {
  if (!confirm("确认将此审批加入执行队列？")) {
    return;
  }
  try {
    const result = await api(`/approvals/${approvalId}/execute`, { method: "POST" });
    renderResult({ type: "approval_execution", result });
    await refreshStatus();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

const enqueueApproval = executeApproval;

async function decideApproval(approvalId, decision) {
  const label = decision === "approved" ? "批准" : "拒绝";
  if (!confirm(`确认${label}此审批项？`)) {
    return;
  }
  try {
    const result = await api(`/approvals/${approvalId}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision, note: `console ${label}` }),
    });
    renderResult({ type: "approval_decision", result: { ...result, decision } });
    await refreshStatus();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function retryJob(jobId) {
  if (!confirm("确认重试此执行任务？")) {
    return;
  }
  try {
    const result = await api(`/execution-jobs/${jobId}/retry`, { method: "POST" });
    renderResult({ type: "execution_job_retry", result });
    await refreshStatus();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function cancelJob(jobId) {
  if (!confirm("确认取消此执行任务？")) {
    return;
  }
  try {
    const result = await api(`/execution-jobs/${jobId}/cancel`, { method: "POST" });
    renderResult({ type: "execution_job_cancel", result });
    await refreshStatus();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function resolveJobVerification(jobId, resolution) {
  const completed = resolution === "confirmed_completed";
  const note = prompt(completed ? "记录你在闲鱼核对到的已执行结果" : "记录你在闲鱼核对到的未执行结果");
  if (!note || !note.trim()) {
    return;
  }
  let reference = "";
  if (completed) {
    reference = prompt("填写平台商品/消息/订单编号，或页面 URL / 截图路径") || "";
    if (!reference.trim()) {
      return;
    }
  }
  const evidence = { external_id: "", page_url: "", screenshot: "" };
  if (/^https?:\/\//i.test(reference.trim())) {
    evidence.page_url = reference.trim();
  } else if (/[/\\]|\.(png|jpg|jpeg|webp)$/i.test(reference.trim())) {
    evidence.screenshot = reference.trim();
  } else {
    evidence.external_id = reference.trim();
  }
  if (!confirm(completed ? "确认平台动作已经执行？" : "确认平台动作没有执行？确认后该任务才允许进入失败重试流程。")) {
    return;
  }
  try {
    const result = await api(`/execution-jobs/${jobId}/verification`, {
      method: "POST",
      body: JSON.stringify({
        resolution,
        note: note.trim(),
        ...evidence,
        checked_by: "console_operator",
      }),
    });
    renderResult({ type: "execution_verification_resolved", result });
    await refreshStatus();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function runNextJobs() {
  if (!confirm("确认执行下一批已排队任务？")) {
    return;
  }
  try {
    const result = await api("/execution-jobs/run-next", { method: "POST" });
    renderResult({ type: "execution_run_next", result });
    await refreshStatus();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function runPendingJobs() {
  if (!confirm("确认执行所有当前可执行任务？")) {
    return;
  }
  try {
    const result = await api("/execution-jobs/run-pending", { method: "POST" });
    renderResult({ type: "execution_run_next", result });
    await refreshStatus();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    $("#command-input").value = button.dataset.template || "";
  });
});

$("#send-btn").addEventListener("click", sendCommand);
$("#save-memory").addEventListener("click", saveMemory);
$("#refresh-btn").addEventListener("click", refreshStatus);
$("#refresh-collaboration").addEventListener("click", refreshCollaboration);
$("#refresh-work-orders").addEventListener("click", refreshWorkOrders);
$("#refresh-execution-jobs")?.addEventListener("click", refreshExecutionJobs);
$("#refresh-message-intake")?.addEventListener("click", refreshMessageIntake);
$("#reply-outbox-filter-status")?.addEventListener("change", () => renderReplyOutbox(state.replyOutbox));
$("#refresh-account-profiles")?.addEventListener("click", refreshAccountProfiles);
$("#open-xianyu-login-browser")?.addEventListener("click", openXianyuLoginBrowser);
$("#preview-browser-account")?.addEventListener("click", previewBrowserAccountImport);
$("#account-bootstrap-form")?.addEventListener("submit", submitAccountBootstrapForm);
$("#prune-message-intake")?.addEventListener("click", pruneMessageIntake);
$("#export-message-intake-bundle")?.addEventListener("click", exportMessageIntakeBundle);
$("#run-message-intake-dry-run")?.addEventListener("click", runMessageIntakeDryRun);
$("#import-browser-messages")?.addEventListener("click", importBrowserMessages);
$("#import-browser-conversations")?.addEventListener("click", importBrowserConversations);
$("#configure-message-import-scheduler")?.addEventListener("click", configureMessageImportScheduler);
$("#run-message-import-scheduler")?.addEventListener("click", runMessageImportSchedulerNow);
$("#message-import-scheduler-profile-form")?.addEventListener("submit", submitMessageImportSchedulerProfileForm);
$("#apply-message-import-scheduler-preset")?.addEventListener("click", () => {
  const preset = $(".scheduler-preset-control")?.value || "high_touch";
  applyMessageImportSchedulerPreset(preset);
});
$("#bootstrap-message-import-scheduler-profiles")?.addEventListener("click", bootstrapMessageImportSchedulerProfiles);
$("#message-import-scheduler-preset")?.addEventListener("change", (event) => {
  applyMessageImportSchedulerPreset(event.target.value);
});
$("#message-import-scheduler-profiles")?.addEventListener("click", (event) => {
  const runButton = event.target.closest(".scheduler-profile-run");
  if (runButton) {
    runMessageImportSchedulerProfile(runButton.dataset.schedulerCookieId);
    return;
  }
  const toggleButton = event.target.closest(".scheduler-profile-toggle-action");
  if (toggleButton) {
    toggleMessageImportSchedulerProfile(
      toggleButton.dataset.schedulerCookieId,
      toggleButton.dataset.schedulerEnabled === "true",
    );
  }
});
$("#run-next-jobs")?.addEventListener("click", runNextJobs);
$("#run-pending-jobs")?.addEventListener("click", runPendingJobs);
$("#work-order-filter-status")?.addEventListener("change", () => renderWorkOrderFeed(state.workOrders));
$("#work-order-filter-kind")?.addEventListener("change", () => renderWorkOrderFeed(state.workOrders));
$("#work-order-filter-owner")?.addEventListener("change", () => renderWorkOrderFeed(state.workOrders));
$("#work-order-filter-risk")?.addEventListener("change", () => renderWorkOrderFeed(state.workOrders));
$("#execution-job-filter-status")?.addEventListener("change", () => renderExecutionJobFeed(state.executionJobs));
$("#polish-btn").addEventListener("click", polish);
$("#resume-btn").addEventListener("click", resumeGuard);
$("#copy-result").addEventListener("click", async () => {
  await navigator.clipboard.writeText(pretty(state.lastResult));
});

refreshStatus().catch((error) => {
  $("#status-ok").textContent = "离线";
  $("#guard-line").textContent = error.message;
  $("#guard-line").className = "is-error";
});

// ============================================================
// Resale Panel
// ============================================================

function decisionLabel(d) {
  return { watch: "观察中", approve: "已通过", reject: "已拒绝" }[d] || d;
}

function renderPreflightGateList(gates = []) {
  if (!gates.length) {
    return "";
  }
  return gates.map((g) => {
    const css = g.status === "pass" ? "preflight-gate preflight-gate--pass" : "preflight-gate preflight-gate--fail";
    return `<div class="${css}"><span class="resale-badge resale-badge--${g.status === "pass" ? "verified" : "unverified"}">${escapeHtml(g.status)}</span>${escapeHtml(g.code)}: ${escapeHtml(g.message)}<span class="snapshot-field">${escapeHtml(g.evidence || "")}</span></div>`;
  }).join("");
}

function evidenceAgeHours(value) {
  if (!value) {
    return null;
  }
  const ts = Date.parse(String(value).replace(" ", "T"));
  if (Number.isNaN(ts)) {
    return null;
  }
  return (Date.now() - ts) / 3600000;
}

function renderEvidenceSummary(c) {
  const parts = [];
  const add = (label, ok, extraClass = "") => {
    const cls = ok ? "" : "resale-evidence-item--missing";
    parts.push(`<span class="resale-evidence-item ${cls} ${extraClass}">${escapeHtml(label)}</span>`);
  };
  add(c.supplier_verified ? "供应商已核验" : "供应商未核验", Boolean(c.supplier_verified));
  const quoteAge = evidenceAgeHours(c.supplier_quote_checked_at);
  if (quoteAge == null) {
    add("报价未核验", false);
  } else if (quoteAge < 0) {
    add("报价 未来", false, "resale-evidence-item--warn");
  } else {
    const within = quoteAge <= 24;
    add(`报价 ${within ? Math.round(quoteAge) + "h前" : "超24h"}`, within, within ? "" : "resale-evidence-item--warn");
  }
  const stockAge = evidenceAgeHours(c.stock_checked_at);
  if (stockAge == null) {
    add("库存未核验", false);
  } else if (stockAge < 0) {
    add("库存 未来", false, "resale-evidence-item--warn");
  } else {
    const within = stockAge <= 24;
    add(`库存 ${within ? Math.round(stockAge) + "h前" : "超24h"}`, within, within ? "" : "resale-evidence-item--warn");
  }
  add(c.fulfillment_verified ? "履约已核验" : "履约未核验", Boolean(c.fulfillment_verified));
  const imageCount = (c.images || []).filter((img) => String(img || "").trim()).length;
  add(`图片 ${imageCount}`, imageCount >= 1);
  add(`授权 ${c.image_rights_type || "未选"}`, Boolean(c.image_rights_type));
  add(`授权证据 ${c.image_rights_evidence ? "有" : "无"}`, Boolean(c.image_rights_evidence));
  const activation = c.activation_summary || {};
  const completion = activation.completion || {};
  add(`启动包 ${completion.passed || 0}/${completion.total || 12}`, Boolean(activation.ready));
  return `<div class="resale-evidence-summary">${parts.join("")}</div>`;
}

function summarizePreflight(p) {
  const lines = [
    `<strong>发布前检查: ${p.ready ? "通过" : "未通过"}</strong>`,
    `状态: ${escapeHtml(p.status || "-")} ｜ 账号: ${escapeHtml(p.cookie_id || "-")}`,
    `检查时间: ${escapeHtml(p.checked_at || "-")}`,
  ];
  const gates = p.gates || [];
  const failed = gates.filter((g) => g.status === "fail");
  if (failed.length) {
    lines.push(`失败门禁: ${failed.map((g) => escapeHtml(g.code)).join(", ")}`);
    lines.push(renderPreflightGateList(failed));
  } else {
    lines.push("全部门禁通过，可生成上架草稿。");
  }
  if (p.next_step && p.next_step.length) {
    lines.push(`下一步: ${p.next_step.map((n) => `${escapeHtml(n.code)} — ${escapeHtml(n.message)}`).join("；")}`);
  }
  return lines.join("<br>");
}

function renderResaleFeed(candidates = []) {
  const feed = $("#resale-feed");
  if (!feed) {
    return;
  }
  if (!candidates.length) {
    feed.innerHTML = `<div class="empty-state">暂无候选</div>`;
    return;
  }
  feed.innerHTML = candidates.map((c) => {
    const isBlocked = c.status === "blocked";
    const econ = c.economics || {};
    const riskClass = c.risk_level || "low";
    const flags = (c.risk_flags || []).map(
      (f) => `<span class="risk-flag${isBlocked ? " is-blocked" : ""}">${escapeHtml(f)}</span>`
    ).join("");

    const verifyHtml = c.supplier_verified
      ? `<div class="resale-verification"><span class="resale-badge resale-badge--verified">已验证供应商</span>${c.supplier_verification_note ? `<span class="snapshot-field">${escapeHtml(c.supplier_verification_note)}</span>` : ""}</div>`
      : `<div class="resale-verification"><span class="resale-badge resale-badge--unverified">未验证供应商</span></div>`;

    const competitorHtml = c.competitor_price_min != null
      ? `<div class="resale-candidate-econ"><span class="snapshot-field">竞品价: ¥${c.competitor_price_min} - ¥${c.competitor_price_max ?? "?"}</span></div>`
      : "";

    const decisionHtml = c.manual_decision
      ? `<div class="resale-decision"><span class="resale-badge resale-badge--decision-${c.manual_decision}">${decisionLabel(c.manual_decision)}</span>${c.manual_decision_note ? `<span class="snapshot-field">${escapeHtml(c.manual_decision_note)}</span>` : ""}</div>`
      : "";

    const activation = c.activation_summary || {};
    const activationCompletion = activation.completion || {};
    const activationNext = activation.next_step || {};
    const activationHtml = `<div class="candidate-activation-card-summary ${activation.ready ? "is-ready" : "is-blocked"}">
      <strong>一品启动包 ${activationCompletion.passed || 0}/${activationCompletion.total || 12}</strong>
      <span>${activation.ready ? "当前证据修订可进入账号发布预检" : `下一项: ${escapeHtml(activationNext.title || activationNext.code || "补齐真实商品证据")}`}</span>
      <span class="snapshot-field">修订 ${escapeHtml(activation.evidence_revision || c.evidence_revision || 1)}</span>
    </div>`;
    const canListing = !isBlocked && activation.ready && !c.listing_draft_id;
    const missingReasons = [];
    if (isBlocked) missingReasons.push("已阻断");
    if (!activation.ready) missingReasons.push(activationNext.title || activationNext.code || "启动包未完成");
    if (c.listing_draft_id) missingReasons.push("已生成草稿");

    const draftStatusHtml = c.listing_draft_id
      ? `<div class="resale-draft-status"><span class="resale-badge resale-badge--draft">草稿: ${escapeHtml(c.listing_draft_id)}</span><span class="snapshot-field">需审批后发布</span></div>`
      : "";

    const actions = isBlocked
      ? `<div class="resale-actions"><button class="resale-btn resale-btn--secondary" onclick="openVerifyDialog('${c.candidate_id}')">一品启动包</button><span style="color:var(--danger);font-size:11px">已阻断，无法创建计划；修正事实后重新评估</span></div>`
      : `<div class="resale-actions">
          <button class="resale-btn" onclick="createResalePlan('${c.candidate_id}')">创建倒卖计划</button>
          <button class="resale-btn resale-btn--secondary" onclick="checkPublishPreflight('${c.candidate_id}')">发布前检查</button>
          ${canListing
            ? `<button class="resale-btn" onclick="createListingDraft('${c.candidate_id}')">生成上架草稿</button>`
            : `<span class="resale-missing" style="color:var(--muted);font-size:11px">上架条件不足: ${escapeHtml(missingReasons.join(", "))}</span>`}
          <button class="resale-btn resale-btn--secondary" onclick="openVerifyDialog('${c.candidate_id}')">一品启动包</button>
          <button class="resale-btn resale-btn--secondary" onclick="openDecisionDialog('${c.candidate_id}')">人工决策</button>
          <button class="resale-btn resale-btn--secondary" onclick="agentReviewCandidate('${c.candidate_id}')">Agent 评审</button>
        </div>`;

    const agentReviewHtml = c.agent_review_score != null
      ? `<div class="agent-review-summary">
           <strong>Agent 评分:</strong> ${c.agent_review_score}/100
           <span class="resale-badge resale-badge--rec-${c.agent_review_recommendation || "unknown"}">${c.agent_review_recommendation || "-"}</span>
           ${(c.blocking_flags || []).length ? `<div style="color:var(--danger);margin-top:3px">阻断: ${escapeHtml(c.blocking_flags.join(", "))}</div>` : ""}
           ${c.manual_decision_override_reason ? `<div style="margin-top:3px">覆盖理由: ${escapeHtml(c.manual_decision_override_reason)}</div>` : ""}
         </div>`
      : c.agent_reviews
        ? `<div class="agent-review-summary"><strong>Agent 建议:</strong> ${escapeHtml(c.agent_reviews.summary || "无")}</div>`
        : "";

    return `
      <div class="resale-candidate${isBlocked ? " is-blocked" : ""}">
        <div class="resale-candidate-header">
          <span class="resale-candidate-title">${escapeHtml(c.title || c.candidate_id)}</span>
          <span class="resale-badge resale-badge--${c.status}">${escapeHtml(c.status)}</span>
        </div>
        <div class="resale-candidate-meta">
          <span class="snapshot-field">${escapeHtml(c.source_platform)}</span>
          <span class="snapshot-field">${escapeHtml(c.sales_platform)}</span>
          <span class="snapshot-field">${escapeHtml(c.market_scope)}</span>
          <span class="resale-badge resale-badge--${riskClass}">风险: ${escapeHtml(riskClass)}</span>
        </div>
        <div class="resale-candidate-econ">
          <span class="snapshot-field">供应价: ¥${c.supplier_price}</span>
          <span class="snapshot-field">运费: ¥${c.supplier_shipping}</span>
          <span class="snapshot-field">目标售价: ¥${c.target_sale_price}</span>
          <span class="snapshot-field">毛利: ¥${econ.gross_profit ?? "-"}</span>
          <span class="snapshot-field">毛利率: ${econ.gross_margin != null ? (econ.gross_margin * 100).toFixed(1) + "%" : "-"}</span>
        </div>
        ${competitorHtml}
        ${verifyHtml}
        ${renderEvidenceSummary(c)}
        ${activationHtml}
        ${decisionHtml}
        ${draftStatusHtml}
        ${agentReviewHtml}
        ${flags ? `<div class="resale-candidate-risk">${flags}</div>` : ""}
        ${c.blocked_reason ? `<div class="resale-candidate-risk" style="color:var(--danger)">原因: ${escapeHtml(c.blocked_reason)}</div>` : ""}
        ${actions}
      </div>
    `;
  }).join("");
}

function renderDiscoveryInbox(events = []) {
  const feed = $("#discovery-inbox-feed");
  if (!feed) {
    return;
  }
  if (!events.length) {
    feed.innerHTML = `<div class="empty-state">暂无线索</div>`;
    return;
  }
  feed.innerHTML = events.map((event) => {
    const priceHint = Number(event.price_hint || 0);
    const meta = [
      event.source ? `source: ${event.source}` : "",
      event.category_hint ? `category: ${event.category_hint}` : "",
      event.supplier_hint ? `supplier: ${event.supplier_hint}` : "",
      priceHint ? `price: ¥${priceHint}` : "",
      event.status ? `status: ${event.status}` : "",
    ].filter(Boolean).join(" ｜ ");
    return `
      <div class="discovery-inbox-item">
        <div class="collab-title">${escapeHtml(event.title || event.event_id || "-")}</div>
        <div class="collab-meta">${escapeHtml(meta || "-")} ｜ ${formatTime(event.created_at)}</div>
        <div class="collab-body">${escapeHtml(event.summary || "暂无摘要").slice(0, 180)}</div>
        <div class="discovery-inbox-actions">
          <button type="button" class="resale-btn" data-event-id="${escapeHtml(event.event_id)}" onclick="promoteDiscoveryEvent(this.dataset.eventId)">提升为候选</button>
          <button type="button" class="resale-btn resale-btn--secondary" data-event-id="${escapeHtml(event.event_id)}" onclick="markDiscoveryEventRead(this.dataset.eventId)">标记已读</button>
        </div>
      </div>
    `;
  }).join("");
}

async function refreshDiscoveryInbox() {
  try {
    const data = await api("/discovery/inbox?status=unread");
    state.discoveryInbox = data.events || [];
    renderDiscoveryInbox(state.discoveryInbox);
  } catch {
    state.discoveryInbox = [];
    renderDiscoveryInbox([]);
  }
}

async function markDiscoveryEventRead(eventId) {
  if (!eventId) {
    return;
  }
  try {
    await api(`/discovery/inbox/${encodeURIComponent(eventId)}/mark-read`, { method: "POST" });
    await refreshDiscoveryInbox();
  } catch (error) {
    alert(`标记失败：${error.message}`);
  }
}

async function promoteDiscoveryEvent(eventId) {
  if (!eventId) {
    return;
  }
  const event = state.discoveryInbox.find((item) => item.event_id === eventId) || {};
  const title = event.title || eventId;
  if (!confirm(`将线索「${title}」提升为候选商品？`)) {
    return;
  }
  try {
    const data = await api(`/discovery/inbox/${encodeURIComponent(eventId)}/promote-candidate`, {
      method: "POST",
      body: "{}",
    });
    renderResult({ type: "discovery_inbox_promote", result: data });
    await refreshDiscoveryInbox();
    await refreshResaleCandidates();
    await loadDashboard();
  } catch (error) {
    alert(`提升候选失败：${error.message}`);
  }
}

async function refreshResaleCandidates() {
  const scope = $("#resale-filter-scope")?.value || "";
  const source = $("#resale-filter-source")?.value || "";
  const status = $("#resale-filter-status")?.value || "";
  const params = new URLSearchParams();
  if (scope) params.set("market_scope", scope);
  if (source) params.set("source_platform", source);
  if (status) params.set("status", status);
  const qs = params.toString();
  try {
    const candidates = await api(`/resale/candidates${qs ? "?" + qs : ""}`);
    state.resaleCandidates = candidates;
    renderResaleFeed(candidates);
  } catch (error) {
    renderResaleFeed([]);
  }
}

function splitListField(raw, separator = /[\n,]+/) {
  return String(raw || "")
    .split(separator)
    .map((item) => item.trim())
    .filter(Boolean);
}

function numberOrDefault(raw, fallback = 0) {
  const value = Number.parseFloat(String(raw ?? ""));
  return Number.isFinite(value) ? value : fallback;
}

function toDatetimeLocal(value) {
  if (!value) {
    return "";
  }
  const text = String(value).trim();
  if (/(?:Z|[+-]\d{2}:\d{2})$/i.test(text)) {
    return toLocalDatetimeInput(text);
  }
  // Stored format is "YYYY-MM-DDTHH:MM:SS"; datetime-local drops seconds.
  const match = text.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}):\d{2}$/);
  return match ? match[1] : text;
}

function toServerTimestamp(value) {
  const text = String(value || "").trim();
  if (!text) {
    return "";
  }
  // datetime-local is "YYYY-MM-DDTHH:MM"; backend expects seconds.
  const match = text.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})$/);
  return match ? `${match[1]}:00` : text;
}

function toLocalDatetimeInput(value = null) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return "";
  const pad = (part) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function toUtcTimestamp(value) {
  const date = new Date(String(value || ""));
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

async function submitResaleForm(event) {
  event.preventDefault();
  const form = $("#resale-form");
  const fd = new FormData(form);
  const specsRaw = fd.get("specifications") || "";
  let specifications = {};
  if (specsRaw.trim()) {
    try {
      specifications = JSON.parse(specsRaw);
    } catch {
      alert("规格 JSON 格式错误，请检查");
      return;
    }
  }
  const payload = {
    title: fd.get("title"),
    category: fd.get("category") || "",
    source_platform: fd.get("source_platform"),
    sales_platform: fd.get("sales_platform") || "xianyu",
    market_scope: fd.get("market_scope") || "domestic",
    source_url: fd.get("source_url") || "",
    supplier_price: numberOrDefault(fd.get("supplier_price"), 0),
    supplier_shipping: numberOrDefault(fd.get("supplier_shipping"), 0),
    target_sale_price: numberOrDefault(fd.get("target_sale_price"), 0),
    platform_fee_rate: numberOrDefault(fd.get("platform_fee_rate"), 0.08),
    platform_fixed_fee: numberOrDefault(fd.get("platform_fixed_fee"), 0),
    expected_refund_rate: numberOrDefault(fd.get("expected_refund_rate"), 0.02),
    specifications,
    images: splitListField(fd.get("images")),
  };
  try {
    const result = await api("/resale/candidates", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderResult({ type: "resale_candidate", result });
    form.reset();
    await refreshResaleCandidates();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function createResalePlan(candidateId) {
  if (!confirm("确认为此候选创建倒卖计划？")) {
    return;
  }
  try {
    const result = await api(`/resale/candidates/${candidateId}/plan`, {
      method: "POST",
      body: "{}",
    });
    renderResult({ type: "resale_plan", result });
    await refreshResaleCandidates();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function createPurchaseDraft(planId) {
  if (!confirm("确认生成采购草稿？将创建审批单，需人工批准后才能继续。")) {
    return;
  }
  try {
    const result = await api(`/resale/plans/${planId}/purchase-draft`, {
      method: "POST",
      body: JSON.stringify({ quantity: 1 }),
    });
    renderResult({ type: "resale_purchase_draft", result });
    await refreshResaleCandidates();
    await refreshWorkOrders();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function openVerifyDialog(candidateId) {
  const candidate = state.resaleCandidates.find((item) => item.candidate_id === candidateId) || null;
  state.evidenceCandidateId = candidateId;
  populateEvidenceModal(candidate);
  const modal = $("#evidence-modal");
  if (modal) {
    modal.hidden = false;
  }
  const summary = $("#candidate-activation-summary");
  if (summary) {
    summary.innerHTML = "正在按商业顺序核验当前证据...";
  }
  try {
    const encodedCandidateId = encodeURIComponent(candidateId);
    const [packet, archive] = await Promise.all([
      api(`/resale/candidates/${encodedCandidateId}/activation-packet`),
      api(`/resale/candidates/${encodedCandidateId}/evidence-artifacts`),
    ]);
    state.candidateActivationPacket = packet;
    state.candidateEvidenceArtifacts = Array.isArray(archive?.artifacts) ? archive.artifacts : [];
    renderCandidateActivationPacket(packet);
    renderCandidateEvidenceArtifacts(state.candidateEvidenceArtifacts);
  } catch (error) {
    showEvidenceFormError(error.message);
  }
}

function populateEvidenceModal(candidate) {
  const c = candidate || {};
  const form = $("#evidence-form");
  if (!form) {
    return;
  }
  const set = (name, value) => {
    const control = form.elements[name];
    if (!control) {
      return;
    }
    if (control.type === "checkbox") {
      control.checked = Boolean(value);
      return;
    }
    control.value = value == null ? "" : value;
  };
  set("title", c.title || "");
  set("category", c.category || "");
  set("source_platform", c.source_platform || "pdd");
  set("source_url", c.source_url || "");
  set("supplier_name", c.supplier_name || "");
  set("supplier_sku", c.supplier_sku || "");
  set("specifications", JSON.stringify(c.specifications || {}, null, 2));
  set("supplier_price", c.supplier_price == null ? "" : c.supplier_price);
  set("supplier_shipping", c.supplier_shipping == null ? "" : c.supplier_shipping);
  set("target_sale_price", c.target_sale_price == null ? "" : c.target_sale_price);
  set("supplier_verified", c.supplier_verified);
  set("supplier_verification_note", c.supplier_verification_note || "");
  set("supplier_quote_checked_at", toDatetimeLocal(c.supplier_quote_checked_at));
  set("supplier_quote_evidence_ref", c.supplier_quote_evidence_ref || "");
  set("minimum_order_quantity", c.minimum_order_quantity == null ? "" : c.minimum_order_quantity);
  set("stock_status", c.stock_status || "");
  set("stock_quantity", c.stock_quantity == null ? "" : c.stock_quantity);
  set("stock_checked_at", toDatetimeLocal(c.stock_checked_at));
  set("stock_evidence_ref", c.stock_evidence_ref || "");
  set("fulfillment_verified", c.fulfillment_verified);
  set("fulfillment_verification_note", c.fulfillment_verification_note || "");
  set("fulfillment_evidence_ref", c.fulfillment_evidence_ref || "");
  set("neutral_shipping_confirmed", c.neutral_shipping_confirmed);
  set("dispatch_time_hours", c.dispatch_time_hours == null ? "" : c.dispatch_time_hours);
  set("supplier_return_policy", c.supplier_return_policy || "");
  set("damage_liability_note", c.damage_liability_note || "");
  set("images", (c.images || []).join("\n"));
  set("image_rights_type", c.image_rights_type || "");
  set("image_rights_evidence", c.image_rights_evidence || "");
  set("image_rights_evidence_ref", c.image_rights_evidence_ref || "");
  set("competitor_links", (c.competitor_links || []).join(", "));
  set("competitor_price_min", c.competitor_price_min == null ? "" : c.competitor_price_min);
  set("competitor_price_max", c.competitor_price_max == null ? "" : c.competitor_price_max);
  set("competitor_checked_at", toDatetimeLocal(c.competitor_checked_at));
  set("competitor_evidence_ref", c.competitor_evidence_ref || "");
  set("estimated_demand_signal", c.estimated_demand_signal || "");
  const observedAt = $("#candidate-evidence-observed-at");
  if (observedAt) observedAt.value = toLocalDatetimeInput();
  const artifactFile = $("#candidate-evidence-file");
  if (artifactFile) artifactFile.value = "";
  const artifactNote = $("#candidate-evidence-note");
  if (artifactNote) artifactNote.value = "";
  const artifactSourceUrl = $("#candidate-evidence-source-url");
  if (artifactSourceUrl) artifactSourceUrl.value = "";
  state.candidateEvidenceArtifacts = [];
  renderCandidateEvidenceArtifacts([]);
  const errorBox = $("#evidence-form-error");
  if (errorBox) {
    errorBox.hidden = true;
    errorBox.textContent = "";
  }
}

function renderCandidateActivationPacket(packet, notice = "") {
  const summary = $("#candidate-activation-summary");
  if (!summary) {
    return;
  }
  const completion = packet?.completion || {};
  const gates = Array.isArray(packet?.gates) ? packet.gates : [];
  const gateHtml = gates.map((gate) => `
    <div class="candidate-activation-gate ${gate.status === "pass" ? "is-pass" : "is-blocked"}">
      <span class="resale-badge resale-badge--${gate.status === "pass" ? "verified" : "unverified"}">${gate.status === "pass" ? "通过" : "阻断"}</span>
      <div><strong>${escapeHtml(gate.title || gate.code)}</strong><span>${escapeHtml(gate.action || "")}</span><small>${escapeHtml(gate.evidence || "")}</small></div>
    </div>
  `).join("");
  const next = packet?.next_step;
  summary.innerHTML = `
    ${notice ? `<div class="candidate-activation-notice">${escapeHtml(notice)}</div>` : ""}
    <div class="candidate-activation-summary-head">
      <strong>${packet?.ready ? "一品启动包已就绪" : "一品启动包仍被阻断"}</strong>
      <span>${completion.passed || 0}/${completion.total || gates.length} 通过</span>
      <span>证据修订 ${escapeHtml(packet?.evidence_revision || 1)}</span>
    </div>
    ${next ? `<div class="candidate-activation-next"><strong>当前第一项</strong><span>${escapeHtml(next.title || next.code)}</span><small>${escapeHtml(next.action || "")}</small></div>` : ""}
    <div class="candidate-activation-gates">${gateHtml}</div>
  `;
}

function formatArtifactBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes < 0) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function renderCandidateEvidenceArtifacts(artifacts = []) {
  const list = $("#candidate-evidence-artifact-list");
  const count = $("#candidate-evidence-archive-count");
  const items = Array.isArray(artifacts) ? artifacts : [];
  if (count) count.textContent = `${items.length} 份`;
  if (!list) return;
  if (!items.length) {
    list.innerHTML = '<span class="snapshot-field">暂无存档；五类证据均需上传真实文件</span>';
    return;
  }
  const candidateId = encodeURIComponent(state.evidenceCandidateId || "");
  list.innerHTML = items.map((artifact) => {
    const artifactId = encodeURIComponent(artifact.artifact_id || "");
    const active = artifact.status === "active";
    const integrityOk = artifact.integrity?.ok === true;
    const stateClass = active && integrityOk ? "is-active" : "is-inactive";
    const stateText = active
      ? (integrityOk ? "当前有效" : "完整性失败")
      : (artifact.status === "superseded" ? "已被替代" : escapeHtml(artifact.status || "未知"));
    const digest = String(artifact.sha256 || "");
    return `
      <div class="candidate-evidence-artifact ${stateClass}">
        <span class="resale-badge resale-badge--${active && integrityOk ? "verified" : "unverified"}">${stateText}</span>
        <div>
          <strong>${escapeHtml(CANDIDATE_EVIDENCE_KIND_LABELS[artifact.kind] || artifact.kind || "未知证据")}</strong>
          <span>${escapeHtml(artifact.original_filename || "未命名")} · ${formatArtifactBytes(artifact.byte_size)}</span>
          <small>观察 ${escapeHtml(artifact.observed_at || "-")} · SHA-256 ${escapeHtml(digest.slice(0, 12) || "-")} · ${escapeHtml(artifact.integrity?.status || "not_checked")}</small>
        </div>
        <a class="resale-btn resale-btn--secondary candidate-evidence-download" href="/resale/candidates/${candidateId}/evidence-artifacts/${artifactId}/file" download>下载</a>
      </div>
    `;
  }).join("");
}

async function uploadCandidateEvidenceArtifact() {
  const candidateId = state.evidenceCandidateId;
  const kind = String($("#candidate-evidence-kind")?.value || "").trim();
  const observedAtInput = String($("#candidate-evidence-observed-at")?.value || "").trim();
  const fileInput = $("#candidate-evidence-file");
  const file = fileInput?.files?.[0];
  if (!candidateId || !kind || !observedAtInput || !file) {
    showEvidenceFormError("请选择证据类型、实际观察时间和证据文件");
    return;
  }
  if (file.size <= 0 || file.size > 10 * 1024 * 1024) {
    showEvidenceFormError("证据文件必须大于 0 且不超过 10 MiB");
    return;
  }
  const observedAt = toServerTimestamp(observedAtInput);
  if (new Date(observedAtInput).getTime() > Date.now() + 60000) {
    showEvidenceFormError("实际观察时间不能晚于当前时间");
    return;
  }
  const query = new URLSearchParams({
    kind,
    observed_at: observedAt,
    filename: file.name,
  });
  const note = String($("#candidate-evidence-note")?.value || "").trim();
  const sourceUrl = String($("#candidate-evidence-source-url")?.value || "").trim();
  if (note) query.set("note", note);
  if (sourceUrl) query.set("source_url", sourceUrl);
  const button = $("#upload-candidate-evidence");
  if (button) {
    button.disabled = true;
    button.textContent = "上传中";
  }
  showEvidenceFormError("");
  try {
    const response = await fetch(
      `/resale/candidates/${encodeURIComponent(candidateId)}/evidence-artifacts?${query}`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": file.type || "application/octet-stream",
        },
        body: file,
      },
    );
    const text = await response.text();
    let result = {};
    try {
      result = text ? JSON.parse(text) : {};
    } catch {
      result = {};
    }
    if (!response.ok) {
      const detail = result?.detail;
      throw new Error(typeof detail === "string" ? detail : (detail?.message || "证据上传被拒绝"));
    }
    state.candidateActivationPacket = result.activation_packet;
    const candidate = result.candidate || {};
    const form = $("#evidence-form");
    const refBindings = {
      supplier_quote: "supplier_quote_evidence_ref",
      supplier_stock: "stock_evidence_ref",
      supplier_fulfillment: "fulfillment_evidence_ref",
      image_rights: "image_rights_evidence_ref",
      market_baseline: "competitor_evidence_ref",
    };
    const timestampBindings = {
      supplier_quote: "supplier_quote_checked_at",
      supplier_stock: "stock_checked_at",
      market_baseline: "competitor_checked_at",
    };
    const refField = refBindings[kind];
    if (form?.elements?.[refField]) form.elements[refField].value = candidate[refField] || "";
    const timestampField = timestampBindings[kind];
    if (timestampField && form?.elements?.[timestampField]) {
      form.elements[timestampField].value = toDatetimeLocal(candidate[timestampField]);
    }
    const archive = await api(
      `/resale/candidates/${encodeURIComponent(candidateId)}/evidence-artifacts`,
    );
    state.candidateEvidenceArtifacts = Array.isArray(archive?.artifacts) ? archive.artifacts : [];
    renderCandidateEvidenceArtifacts(state.candidateEvidenceArtifacts);
    const notice = result.manual_decision_invalidated
      ? "新证据已存档并绑定，旧人工决策已失效；完成全部证据后重新决策。"
      : "证据已存档并绑定到当前候选。";
    renderCandidateActivationPacket(result.activation_packet, notice);
    renderResult({ type: "candidate_evidence_artifact", result });
    if (fileInput) fileInput.value = "";
    const noteInput = $("#candidate-evidence-note");
    if (noteInput) noteInput.value = "";
    await refreshResaleCandidates();
  } catch (error) {
    showEvidenceFormError(error.message);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "上传并绑定";
    }
  }
}

function closeEvidenceModal() {
  const modal = $("#evidence-modal");
  if (modal) {
    modal.hidden = true;
  }
  state.evidenceCandidateId = null;
  state.candidateActivationPacket = null;
  state.candidateEvidenceArtifacts = [];
}

function validateEvidenceForm(form) {
  const priceMin = form.elements.competitor_price_min?.value;
  const priceMax = form.elements.competitor_price_max?.value;
  if (priceMin !== "" && priceMax !== "" && Number(priceMax) < Number(priceMin)) {
    return "竞品最高价不能低于竞品最低价";
  }
  const specsRaw = (form.elements.specifications?.value || "").trim();
  if (specsRaw) {
    try {
      const specs = JSON.parse(specsRaw);
      if (!specs || Array.isArray(specs) || typeof specs !== "object") {
        return "规格 JSON 必须是对象";
      }
    } catch {
      return "规格 JSON 格式错误";
    }
  }
  for (const field of ["supplier_quote_checked_at", "stock_checked_at", "competitor_checked_at"]) {
    const value = form.elements[field]?.value;
    if (value && new Date(value).getTime() > Date.now() + 60000) {
      return `${field} 不能填写未来时间`;
    }
  }
  return "";
}

function optionalNumber(control) {
  const raw = String(control?.value || "").trim();
  if (!raw) {
    return null;
  }
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

async function saveEvidenceModal() {
  const form = $("#evidence-form");
  if (!form) {
    return;
  }
  const validationError = validateEvidenceForm(form);
  if (validationError) {
    showEvidenceFormError(validationError);
    return;
  }
  const candidateId = state.evidenceCandidateId;
  if (!candidateId) {
    return;
  }
  let specifications = {};
  try {
    specifications = JSON.parse((form.elements.specifications?.value || "{}").trim() || "{}");
  } catch {
    showEvidenceFormError("规格 JSON 格式错误");
    return;
  }
  const payload = {
    title: (form.elements.title?.value || "").trim(),
    category: (form.elements.category?.value || "").trim(),
    source_platform: (form.elements.source_platform?.value || "").trim(),
    source_url: (form.elements.source_url?.value || "").trim(),
    supplier_name: (form.elements.supplier_name?.value || "").trim(),
    supplier_sku: (form.elements.supplier_sku?.value || "").trim(),
    specifications,
    supplier_price: optionalNumber(form.elements.supplier_price),
    supplier_shipping: optionalNumber(form.elements.supplier_shipping),
    target_sale_price: optionalNumber(form.elements.target_sale_price),
    supplier_verified: form.elements.supplier_verified?.checked === true,
    supplier_verification_note: (form.elements.supplier_verification_note?.value || "").trim(),
    supplier_quote_checked_at: toServerTimestamp(form.elements.supplier_quote_checked_at?.value),
    minimum_order_quantity: optionalNumber(form.elements.minimum_order_quantity),
    stock_status: (form.elements.stock_status?.value || "").trim(),
    stock_quantity: optionalNumber(form.elements.stock_quantity),
    stock_checked_at: toServerTimestamp(form.elements.stock_checked_at?.value),
    fulfillment_verified: form.elements.fulfillment_verified?.checked === true,
    fulfillment_verification_note: (form.elements.fulfillment_verification_note?.value || "").trim(),
    neutral_shipping_confirmed: form.elements.neutral_shipping_confirmed?.checked === true,
    dispatch_time_hours: optionalNumber(form.elements.dispatch_time_hours),
    supplier_return_policy: (form.elements.supplier_return_policy?.value || "").trim(),
    damage_liability_note: (form.elements.damage_liability_note?.value || "").trim(),
    images: splitListField(form.elements.images?.value),
    image_rights_type: (form.elements.image_rights_type?.value || "").trim(),
    image_rights_evidence: (form.elements.image_rights_evidence?.value || "").trim(),
    competitor_links: splitListField(form.elements.competitor_links?.value, /,/),
    competitor_price_min: optionalNumber(form.elements.competitor_price_min),
    competitor_price_max: optionalNumber(form.elements.competitor_price_max),
    competitor_checked_at: toServerTimestamp(form.elements.competitor_checked_at?.value),
    estimated_demand_signal: (form.elements.estimated_demand_signal?.value || "").trim(),
  };
  try {
    const result = await api(`/resale/candidates/${candidateId}/activation-packet`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    state.candidateActivationPacket = result.activation_packet;
    const notice = result.manual_decision_invalidated
      ? "候选事实已变化，旧人工决策已失效；证据完成后需要重新决策。"
      : "候选证据已保存并重新核验。";
    renderCandidateActivationPacket(result.activation_packet, notice);
    renderResult({ type: "candidate_activation_packet", result });
    await refreshResaleCandidates();
  } catch (error) {
    showEvidenceFormError(error.message);
  }
}

function showEvidenceFormError(message) {
  const errorBox = $("#evidence-form-error");
  if (errorBox) {
    errorBox.textContent = message || "";
    errorBox.hidden = !message;
  }
}

function setCommercialTermsFormError(message) {
  const errorBox = $("#commercial-terms-form-error");
  if (!errorBox) return;
  errorBox.textContent = message || "";
  errorBox.hidden = !message;
}

function populateCommercialTermsModal(snapshot = null, context = {}) {
  const form = $("#commercial-terms-form");
  if (!form) return;
  form.reset();
  const source = snapshot || {};
  const set = (name, value) => {
    const control = form.elements[name];
    if (!control) return;
    if (control.type === "checkbox") {
      control.checked = Boolean(value);
    } else {
      control.value = value == null ? "" : value;
    }
  };
  set("cookie_id", context.cookie_id || source.cookie_id || "");
  set("category", context.category || source.category || "");
  set("candidate_id", context.candidate_id || "");
  set("seller_type", source.seller_type || "unknown");
  set("account_owner_confirmed", source.account_owner_confirmed);
  set("seller_agreement_confirmed", source.seller_agreement_confirmed);
  set("payment_service_ready", source.payment_service_ready);
  set("category_publishable", source.category_publishable);
  set("return_policy", source.return_policy || "unknown");
  set("return_exception_basis", source.return_exception_basis || "");
  set("return_address_ready", source.return_address_ready);
  set("after_sales_ready", source.after_sales_ready);
  set("guarantee_required", source.guarantee_required);
  set("guarantee_ready", source.guarantee_ready);
  set("guarantee_amount", source.guarantee_amount ?? 0);
  set("platform_fee_rate", source.platform_fee_rate ?? "");
  set("platform_fixed_fee", source.platform_fixed_fee ?? 0);
  set("observed_at", toLocalDatetimeInput(source.observed_at));
  set("evidence_ref", source.evidence_ref || "");
  set("evidence_note", source.evidence_note || "");
  setCommercialTermsFormError("");
}

async function openCommercialTermsDialog() {
  const launch = buildCommercialLaunchPayload();
  if (!launch?.cookie_id || !launch?.candidate_id) {
    alert("请先选择卖家账号和唯一首发候选。");
    return;
  }
  const candidate = state.commercialCandidates.find(
    (item) => item.candidate_id === launch.candidate_id
  );
  const category = String(candidate?.category || "").trim();
  if (!category) {
    alert("首发候选缺少平台类目，先补全候选类目。");
    return;
  }
  const context = {
    cookie_id: launch.cookie_id,
    candidate_id: launch.candidate_id,
    category,
  };
  state.commercialTermsContext = context;
  try {
    const params = new URLSearchParams({ category, candidate_id: launch.candidate_id });
    const result = await api(
      `/accounts/${encodeURIComponent(launch.cookie_id)}/commercial-terms?${params.toString()}`
    );
    populateCommercialTermsModal(result.snapshot, context);
    const modal = $("#commercial-terms-modal");
    if (modal) modal.hidden = false;
  } catch (error) {
    alert(`商业条款读取失败：${error.message}`);
  }
}

function closeCommercialTermsModal() {
  const modal = $("#commercial-terms-modal");
  if (modal) modal.hidden = true;
  state.commercialTermsContext = null;
  setCommercialTermsFormError("");
}

async function saveCommercialTermsModal() {
  const form = $("#commercial-terms-form");
  const context = state.commercialTermsContext;
  if (!form || !context) return;
  const feeRate = numberOrDefault(form.elements.platform_fee_rate?.value, Number.NaN);
  const fixedFee = numberOrDefault(form.elements.platform_fixed_fee?.value, Number.NaN);
  const guaranteeAmount = numberOrDefault(form.elements.guarantee_amount?.value, Number.NaN);
  if (!Number.isFinite(feeRate) || feeRate < 0 || feeRate >= 1) {
    setCommercialTermsFormError("平台比例费率必须是 0 到 1 之间的小数。");
    return;
  }
  if (!Number.isFinite(fixedFee) || fixedFee < 0 || !Number.isFinite(guaranteeAmount) || guaranteeAmount < 0) {
    setCommercialTermsFormError("固定费用和保证金金额必须是非负数。");
    return;
  }
  const observedAt = toUtcTimestamp(form.elements.observed_at?.value);
  if (!observedAt) {
    setCommercialTermsFormError("请填写有效的核验时间。");
    return;
  }
  const payload = {
    category: context.category,
    candidate_id: context.candidate_id,
    seller_type: String(form.elements.seller_type?.value || "unknown"),
    account_owner_confirmed: form.elements.account_owner_confirmed?.checked === true,
    seller_agreement_confirmed: form.elements.seller_agreement_confirmed?.checked === true,
    payment_service_ready: form.elements.payment_service_ready?.checked === true,
    category_publishable: form.elements.category_publishable?.checked === true,
    return_policy: String(form.elements.return_policy?.value || "unknown"),
    return_exception_basis: String(form.elements.return_exception_basis?.value || "").trim(),
    return_address_ready: form.elements.return_address_ready?.checked === true,
    after_sales_ready: form.elements.after_sales_ready?.checked === true,
    guarantee_required: form.elements.guarantee_required?.checked === true,
    guarantee_ready: form.elements.guarantee_ready?.checked === true,
    guarantee_amount: guaranteeAmount,
    platform_fee_rate: feeRate,
    platform_fixed_fee: fixedFee,
    observed_at: observedAt,
    evidence_ref: String(form.elements.evidence_ref?.value || "").trim(),
    evidence_note: String(form.elements.evidence_note?.value || "").trim(),
  };
  try {
    const result = await api(
      `/accounts/${encodeURIComponent(context.cookie_id)}/commercial-terms`,
      { method: "POST", body: JSON.stringify(payload) }
    );
    const failed = result.assessment?.failed_gate_codes || [];
    renderResult({ type: "commercial_terms", result });
    closeCommercialTermsModal();
    await Promise.all([refreshResaleCandidates(), refreshCommercialTrials()]);
    await previewCommercialLaunch();
    if (failed.length) {
      alert(`条款已保存并重算利润，仍有 ${failed.length} 个子门禁未通过。`);
    }
  } catch (error) {
    setCommercialTermsFormError(error.message);
  }
}

async function openDecisionDialog(candidateId) {
  const choice = prompt("人工决策（输入: watch / approve / reject）:", "watch");
  if (!choice) return;
  const note = prompt("决策备注（可留空）:", "");
  let overrideReason = "";
  if (choice.trim() === "approve") {
    overrideReason = prompt("覆盖理由（如 Agent 建议 reject 但你坚持 approve，请填写理由；可留空）:", "") || "";
  }
  try {
    const result = await api(`/resale/candidates/${candidateId}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision: choice.trim(), note: note || "", override_reason: overrideReason }),
    });
    renderResult({ type: "resale_decision", result: result.candidate || result });
    await refreshResaleCandidates();
    await loadDashboard();
  } catch (error) {
    alert("决策失败: " + error.message);
    renderResult({ type: "error", result: { message: error.message } });
  }
}

function chooseListingAccountId() {
  const accountIds = (state.accountProfiles || [])
    .filter((profile) => profile && profile.enabled !== false && profile.cookie_id)
    .map((profile) => profile.cookie_id);
  if (accountIds.length === 0) return "";
  if (accountIds.length === 1) return accountIds[0];
  const selected = prompt(`Seller account for this listing:\n${accountIds.join("\n")}`, accountIds[0]);
  if (selected === null) return null;
  const normalized = selected.trim();
  if (!accountIds.includes(normalized)) {
    alert("Select one of the configured enabled seller accounts.");
    return null;
  }
  return normalized;
}

async function createListingDraft(candidateId) {
  const cookieId = chooseListingAccountId();
  if (cookieId === null) return;
  const accountQuery = cookieId ? `?cookie_id=${encodeURIComponent(cookieId)}` : "";
  if (!confirm("确认生成上架草稿？将创建审批单，需人工批准后才会发布。")) return;
  try {
    const result = await api(`/resale/candidates/${candidateId}/listing-draft${accountQuery}`, {
      method: "POST",
      body: "{}",
    });
    renderResult({ type: "resale_listing_draft", result });
    await refreshResaleCandidates();
    await refreshWorkOrders();
  } catch (error) {
    const detail = error.detail || {};
    const failed = (detail.gates || []).filter((g) => g.status === "fail").map((g) => g.code);
    const reason = failed.length ? `未通过: ${failed.join(", ")}` : error.message;
    alert("发布被阻止（409）: " + reason);
    renderResult({ type: "resale_preflight_blocked", result: detail });
  }
}

async function checkPublishPreflight(candidateId) {
  const cookieId = chooseListingAccountId();
  if (cookieId === null) return;
  const accountQuery = cookieId ? `?cookie_id=${encodeURIComponent(cookieId)}` : "";
  try {
    const result = await api(`/resale/candidates/${candidateId}/publish-preflight${accountQuery}`);
    renderResult({ type: "resale_preflight", result });
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function agentReviewCandidate(candidateId) {
  try {
    const result = await api(`/resale/candidates/${candidateId}/agent-review`, {
      method: "POST",
      body: "{}",
    });
    renderResult({ type: "resale_agent_review", result });
    await refreshResaleCandidates();
  } catch (error) {
    alert("Agent 评审失败: " + error.message);
    renderResult({ type: "error", result: { message: error.message } });
  }
}

async function loadDashboard() {
  try {
    const data = await api("/resale/dashboard/summary");
    renderDashboard(data);
  } catch {
    // silent
  }
}

function renderDashboard(data) {
  const el = $("#resale-dashboard");
  if (!el) return;
  const daily = data.daily_candidate_count || {};
  const dailyLines = Object.keys(daily).sort().slice(-7).map(
    (d) => `${d}: ${daily[d]}`
  ).join(" | ");
  el.innerHTML = `
    <div class="dashboard-stats">
      <span class="snapshot-field">候选总数: ${data.total_candidates}</span>
      <span class="snapshot-field">已阻断: ${data.blocked_count}</span>
      <span class="snapshot-field">已通过: ${data.approved_count}</span>
      <span class="snapshot-field">已拒绝: ${data.rejected_count}</span>
      <span class="snapshot-field">观察中: ${data.watch_count}</span>
      <span class="snapshot-field">平均毛利率: ${(data.avg_gross_margin * 100).toFixed(1)}%</span>
      <span class="snapshot-field">待验证: ${data.candidates_missing_supplier_verification}</span>
      <span class="snapshot-field">可上架: ${data.candidates_ready_for_listing}</span>
    </div>
    <div class="dashboard-daily">近7天: ${dailyLines || "无数据"}</div>
    <div class="dashboard-flags">Top风险: ${(data.top_risk_flags || []).map(escapeHtml).join(", ") || "无"}</div>
  `;
}

// Wire up resale event listeners
$("#resale-form")?.addEventListener("submit", submitResaleForm);
$("#refresh-resale")?.addEventListener("click", refreshResaleCandidates);
$("#refresh-dashboard")?.addEventListener("click", loadDashboard);
$("#refresh-discovery-inbox")?.addEventListener("click", refreshDiscoveryInbox);
$("#resale-filter-scope")?.addEventListener("change", refreshResaleCandidates);
$("#resale-filter-source")?.addEventListener("change", refreshResaleCandidates);
$("#resale-filter-status")?.addEventListener("change", refreshResaleCandidates);

// Wire up the commercial evidence modal
$("#evidence-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  saveEvidenceModal();
});
$("#upload-candidate-evidence")?.addEventListener("click", uploadCandidateEvidenceArtifact);
document.querySelectorAll("[data-close-evidence-modal]").forEach((button) => {
  button.addEventListener("click", closeEvidenceModal);
});
$("#evidence-modal")?.addEventListener("click", (event) => {
  if (event.target === event.currentTarget) {
    closeEvidenceModal();
  }
});
$("#commercial-terms-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  saveCommercialTermsModal();
});
document.querySelectorAll("[data-close-commercial-terms-modal]").forEach((button) => {
  button.addEventListener("click", closeCommercialTermsModal);
});
$("#commercial-terms-modal")?.addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeCommercialTermsModal();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#evidence-modal")?.hidden) {
    closeEvidenceModal();
  }
  if (event.key === "Escape" && !$("#commercial-terms-modal")?.hidden) {
    closeCommercialTermsModal();
  }
});

// Load resale candidates and dashboard on startup
refreshResaleCandidates().catch(() => {});
refreshDiscoveryInbox().catch(() => {});
loadDashboard().catch(() => {});
refreshPurchaseTasks().catch(() => {});

// ============================================================
// Purchase Tasks
// ============================================================

async function refreshPurchaseTasks() {
  try {
    const tasks = await api("/resale/purchase-tasks");
    renderPurchaseTasks(tasks);
  } catch {
    renderPurchaseTasks([]);
  }
}

function renderPurchaseTasks(tasks = []) {
  const feed = $("#purchase-task-feed");
  if (!feed) return;
  if (!tasks.length) {
    feed.innerHTML = '<div class="empty-state">暂无采购任务</div>';
    return;
  }
  feed.innerHTML = tasks.map((t) => {
    const isPending = t.status === "pending_manual_order";
    const isOrdered = t.status === "ordered";
    const specs = t.product_specs || {};
    const specText = Object.keys(specs).length
      ? Object.entries(specs).map(([k, v]) => `${escapeHtml(k)}: ${escapeHtml(v)}`).join(", ")
      : "无";

    const statusBadge = `<span class="resale-badge resale-badge--task-${t.status}">${escapeHtml(taskStatusLabel(t.status))}</span>`;

    const orderedInfo = isOrdered
      ? `<div class="purchase-task-ordered">
          ${t.supplier_order_id ? `<span class="snapshot-field">供应商单号: ${escapeHtml(t.supplier_order_id)}</span>` : ""}
          ${t.tracking_number ? `<span class="snapshot-field">快递单号: ${escapeHtml(t.tracking_number)}</span>` : ""}
          ${t.manual_order_note ? `<span class="snapshot-field">备注: ${escapeHtml(t.manual_order_note)}</span>` : ""}
          ${t.ordered_at ? `<span class="snapshot-field">下单时间: ${escapeHtml(t.ordered_at)}</span>` : ""}
        </div>`
      : "";

    const actions = isPending
      ? `<div class="purchase-task-actions">
          <button class="resale-btn" onclick="markPurchaseTaskOrdered('${t.purchase_task_id}')">标记已下单</button>
          <button class="resale-btn resale-btn--secondary" onclick="createOrderFromTask('${t.purchase_task_id}')">创建订单对账</button>
          <button class="resale-btn resale-btn--secondary" onclick="cancelPurchaseTask('${t.purchase_task_id}')">取消任务</button>
        </div>`
      : isOrdered
      ? `<div class="purchase-task-actions">
          <button class="resale-btn" onclick="createOrderFromTask('${t.purchase_task_id}')">创建订单对账</button>
        </div>`
      : "";

    const warnings = (t.risk_warnings || []).length
      ? `<div class="purchase-task-warnings">${t.risk_warnings.map((w) => '<span class="risk-flag">' + escapeHtml(w) + '</span>').join("")}</div>`
      : "";

    return `
      <div class="purchase-task-item">
        <div class="purchase-task-header">
          <span class="purchase-task-title">采购任务 ${escapeHtml(t.purchase_task_id)}</span>
          ${statusBadge}
        </div>
        <div class="purchase-task-body">
          ${t.supplier_url ? `<div class="snapshot-field"><a href="${escapeHtml(t.supplier_url)}" target="_blank" rel="noopener">供应商链接</a></div>` : ""}
          <div class="snapshot-field">规格: ${specText}</div>
          <div class="snapshot-field">数量: ${t.quantity}</div>
          <div class="snapshot-field">应付: ¥${t.payable_amount}</div>
          <div class="snapshot-field">预期利润: ¥${t.expected_profit}</div>
          ${t.masked_buyer_address ? `<div class="snapshot-field">收货地: ${escapeHtml(t.masked_buyer_address)}</div>` : ""}
        </div>
        ${warnings}
        ${orderedInfo}
        ${actions}
      </div>
    `;
  }).join("");
}

function taskStatusLabel(s) {
  return { pending_manual_order: "待人工下单", ordered: "已下单", cancelled: "已取消", failed: "失败" }[s] || s;
}

async function markPurchaseTaskOrdered(taskId) {
  const supplierOrderId = prompt("供应商订单号（可选）:");
  if (supplierOrderId === null) return;
  const trackingNumber = prompt("快递单号（可选）:");
  if (trackingNumber === null) return;
  const note = prompt("备注（可选）:");
  if (note === null) return;
  try {
    await api(`/resale/purchase-tasks/${taskId}/mark-ordered`, {
      method: "POST",
      body: JSON.stringify({
        supplier_order_id: supplierOrderId || "",
        tracking_number: trackingNumber || "",
        manual_order_note: note || "",
      }),
    });
    await refreshPurchaseTasks();
  } catch (error) {
    alert("操作失败: " + error.message);
  }
}

async function cancelPurchaseTask(taskId) {
  if (!confirm("确认取消此采购任务？")) return;
  try {
    await api(`/resale/purchase-tasks/${taskId}/cancel`, { method: "POST", body: "{}" });
    await refreshPurchaseTasks();
  } catch (error) {
    alert("取消失败: " + error.message);
  }
}

// Wire up purchase task listeners
$("#refresh-purchase-tasks")?.addEventListener("click", refreshPurchaseTasks);

// ============================================================
// Resale Orders
// ============================================================

async function refreshOrders() {
  try {
    const [orders, dashboard] = await Promise.all([
      api("/resale/orders"),
      api("/resale/orders/dashboard"),
    ]);
    renderOrderDashboard(dashboard);
    renderOrders(orders);
  } catch {
    renderOrders([]);
  }
}

function renderOrderDashboard(data) {
  const el = $("#order-dashboard");
  if (!el) return;
  el.innerHTML = `
    <div class="dashboard-stats">
      <span class="snapshot-field">总订单: ${data.total_orders}</span>
      <span class="snapshot-field">已采购: ${data.purchased_count}</span>
      <span class="snapshot-field">已发货: ${data.shipped_count}</span>
      <span class="snapshot-field">已完成: ${data.completed_count}</span>
      <span class="snapshot-field" style="color:var(--danger)">退款风险: ${data.refund_risk_count}</span>
      <span class="snapshot-field" style="color:var(--danger)">已退款: ${data.refunded_count}</span>
    </div>
    <div class="dashboard-stats">
      <span class="snapshot-field">总销售额: ¥${data.total_sale_amount}</span>
      <span class="snapshot-field">总利润: ¥${data.total_actual_profit}</span>
      <span class="snapshot-field">平均毛利率: ${(data.avg_actual_margin * 100).toFixed(1)}%</span>
    </div>
    <div class="dashboard-stats">
      <span class="snapshot-field" style="color:var(--warn)">缺物流: ${data.orders_missing_tracking}</span>
      <span class="snapshot-field" style="color:var(--danger)">亏损订单: ${data.orders_with_negative_profit}</span>
    </div>
  `;
}

function orderStatusLabel(s) {
  return {
    pending_purchase: "待采购", purchased: "已采购", shipped: "已发货",
    completed: "已完成", cancelled: "已取消", refund_risk: "退款风险", refunded: "已退款",
  }[s] || s;
}

function renderOrders(orders) {
  const feed = $("#order-feed");
  if (!feed) return;
  if (!orders.length) {
    feed.innerHTML = '<div class="empty-state">暂无订单</div>';
    return;
  }
  feed.innerHTML = orders.map((o) => {
    const statusBadge = `<span class="resale-badge resale-badge--order-${o.status}">${escapeHtml(orderStatusLabel(o.status))}</span>`;
    const profitColor = o.actual_profit < 0 ? "color:var(--danger)" : "";
    return `
      <div class="order-item">
        <div class="order-header">
          <span class="order-title">订单 ${escapeHtml(o.resale_order_id)}</span>
          ${statusBadge}
        </div>
        <div class="order-body">
          <div class="order-links">
            ${o.xianyu_order_id ? `<span class="snapshot-field">闲鱼单号: ${escapeHtml(o.xianyu_order_id)}</span>` : ""}
            ${o.supplier_order_id ? `<span class="snapshot-field">供应商单号: ${escapeHtml(o.supplier_order_id)}</span>` : ""}
          </div>
          <div class="order-tracking">
            ${o.supplier_tracking_number ? `<span class="snapshot-field">供应物流: ${escapeHtml(o.supplier_tracking_number)}</span>` : ""}
            ${o.xianyu_tracking_number ? `<span class="snapshot-field">闲鱼物流: ${escapeHtml(o.xianyu_tracking_number)}</span>` : ""}
          </div>
          <div class="order-finance">
            <span class="snapshot-field">售价: ¥${o.sale_amount}</span>
            <span class="snapshot-field">成本: ¥${o.supplier_cost}</span>
            <span class="snapshot-field">运费: ¥${o.shipping_cost}</span>
            <span class="snapshot-field">平台费: ¥${o.platform_fee}</span>
            <span class="snapshot-field" style="${profitColor}">利润: ¥${o.actual_profit}</span>
            <span class="snapshot-field" style="${profitColor}">毛利率: ${(o.actual_margin * 100).toFixed(1)}%</span>
          </div>
          ${o.buyer_note ? `<div class="snapshot-field">买家备注: ${escapeHtml(o.buyer_note)}</div>` : ""}
          ${o.supplier_note ? `<div class="snapshot-field">供应商备注: ${escapeHtml(o.supplier_note)}</div>` : ""}
          ${o.aftersale_note ? `<div class="snapshot-field">售后备注: ${escapeHtml(o.aftersale_note)}</div>` : ""}
          ${o.financial_evidence_note ? `<div class="snapshot-field">财务核对: ${escapeHtml(o.financial_evidence_note)}</div>` : ""}
          ${o.financial_evidence_ref ? `<div class="snapshot-field">财务凭证: ${escapeHtml(o.financial_evidence_ref)}</div>` : ""}
        </div>
        <div class="order-actions">
          <select onchange="updateOrderStatus('${o.resale_order_id}', this.value)">
            ${["pending_purchase", "purchased", "shipped", "completed", "cancelled", "refund_risk", "refunded"].map(
              (s) => `<option value="${s}" ${o.status === s ? "selected" : ""}>${orderStatusLabel(s)}</option>`
            ).join("")}
          </select>
          <button class="resale-btn resale-btn--secondary" onclick="updateOrderDetails('${o.resale_order_id}')">更新信息</button>
          <button class="resale-btn resale-btn--secondary" onclick="viewOrderDetail('${o.resale_order_id}')">查看链路详情</button>
        </div>
      </div>
    `;
  }).join("");
}

async function updateOrderStatus(orderId, newStatus) {
  try {
    const order = await api(`/resale/orders/${encodeURIComponent(orderId)}`);
    const payload = { status: newStatus };
    if (order.commercial_trial_id && ["shipped", "completed"].includes(newStatus)) {
      const supplierOrderId = prompt("供应商订单号:", order.supplier_order_id || "");
      if (supplierOrderId === null) { await refreshOrders(); return; }
      const supplierTracking = prompt("供应商物流号:", order.supplier_tracking_number || "");
      if (supplierTracking === null) { await refreshOrders(); return; }
      const xianyuTracking = prompt("闲鱼物流号:", order.xianyu_tracking_number || "");
      if (xianyuTracking === null) { await refreshOrders(); return; }
      payload.supplier_order_id = String(supplierOrderId).trim();
      payload.supplier_tracking_number = String(supplierTracking).trim();
      payload.xianyu_tracking_number = String(xianyuTracking).trim();
    }
    if (order.commercial_trial_id && newStatus === "completed") {
      const financeFields = [
        ["sale_amount", "闲鱼实际实收", order.sale_amount],
        ["supplier_cost", "供应商实际付款", order.supplier_cost],
        ["shipping_cost", "实际运费", order.shipping_cost],
        ["platform_fee", "平台实际费用", order.platform_fee],
      ];
      for (const [field, label, current] of financeFields) {
        const raw = prompt(`${label}（元）:`, String(current ?? 0));
        if (raw === null) { await refreshOrders(); return; }
        const value = Number(raw);
        if (!Number.isFinite(value) || (field === "sale_amount" || field === "supplier_cost" ? value <= 0 : value < 0)) {
          alert(`${label}无效。`);
          await refreshOrders();
          return;
        }
        payload[field] = value;
      }
      const evidenceNote = prompt("财务核对备注（至少 8 个字符）:", order.financial_evidence_note || "");
      if (evidenceNote === null) { await refreshOrders(); return; }
      const evidenceRef = prompt("财务凭证引用（账单截图或文件名）:", order.financial_evidence_ref || "");
      if (evidenceRef === null) { await refreshOrders(); return; }
      payload.financial_evidence_note = String(evidenceNote).trim();
      payload.financial_evidence_ref = String(evidenceRef).trim();
    }
    await api(`/resale/orders/${orderId}/status`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await Promise.all([refreshOrders(), refreshCommercialTrials()]);
  } catch (error) {
    const reasons = Array.isArray(error.detail?.reasons) ? error.detail.reasons.join("、") : error.message;
    alert("更新失败: " + reasons);
    await refreshOrders();
  }
}

async function updateOrderDetails(orderId) {
  const supplierTracking = prompt("供应商物流号（留空跳过）:");
  if (supplierTracking === null) return;
  const xianyuTracking = prompt("闲鱼物流号（留空跳过）:");
  if (xianyuTracking === null) return;
  const supplierOrderId = prompt("供应商订单号（留空跳过）:");
  if (supplierOrderId === null) return;
  const note = prompt("备注（留空跳过）:");
  if (note === null) return;

  const updates = {};
  if (supplierTracking) updates.supplier_tracking_number = supplierTracking;
  if (xianyuTracking) updates.xianyu_tracking_number = xianyuTracking;
  if (supplierOrderId) updates.supplier_order_id = supplierOrderId;
  if (note) updates.supplier_note = note;

  if (!Object.keys(updates).length) return;
  try {
    await api(`/resale/orders/${orderId}/status`, {
      method: "POST",
      body: JSON.stringify(updates),
    });
    await refreshOrders();
  } catch (error) {
    alert("更新失败: " + error.message);
  }
}

async function createOrderFromTask(taskId) {
  const xianyuOrderId = prompt("闲鱼订单号:");
  if (xianyuOrderId === null) return;
  const saleAmount = prompt("售价 (元):");
  if (saleAmount === null) return;
  if (!saleAmount || isNaN(Number(saleAmount))) { alert("请输入有效售价"); return; }
  const platformFee = prompt("平台手续费 (元, 默认0):", "0");
  if (platformFee === null) return;
  const shippingCost = prompt("运费 (元, 默认0):", "0");
  if (shippingCost === null) return;
  const note = prompt("备注 (可选):");
  if (note === null) return;
  try {
    await api(`/resale/purchase-tasks/${taskId}/create-order`, {
      method: "POST",
      body: JSON.stringify({
        xianyu_order_id: xianyuOrderId || "",
        sale_amount: Number(saleAmount),
        platform_fee: Number(platformFee) || 0,
        shipping_cost: Number(shippingCost) || 0,
        buyer_note: note || "",
      }),
    });
    await refreshOrders();
    await refreshPurchaseTasks();
  } catch (error) {
    alert("创建失败: " + error.message);
  }
}

async function viewOrderDetail(orderId) {
  try {
    const detail = await api(`/resale/orders/${orderId}/detail`);
    const o = detail.order || {};
    const task = detail.purchase_task;
    const draft = detail.purchase_draft;
    const plan = detail.plan;
    const candidate = detail.candidate;
    const listing = detail.listing_draft;

    const lines = [`<strong>订单链路详情</strong>`, `<br>--- 订单 ---`];
    lines.push(`闲鱼单号: ${escapeHtml(o.xianyu_order_id || "-")}`);
    lines.push(`状态: ${escapeHtml(orderStatusLabel(o.status))}`);
    lines.push(`利润: ¥${o.actual_profit} (${(o.actual_margin * 100).toFixed(1)}%)`);

    if (candidate) {
      lines.push(`<br>--- 候选商品 ---`);
      lines.push(`标题: ${escapeHtml(candidate.title || "-")}`);
      lines.push(`来源: ${escapeHtml(candidate.source_platform || "-")} → ${escapeHtml(candidate.sales_platform || "-")}`);
    }
    if (task) {
      lines.push(`<br>--- 采购任务 ---`);
      lines.push(`任务ID: ${escapeHtml(task.purchase_task_id)}`);
      lines.push(`应付: ¥${task.payable_amount}`);
      if (task.supplier_url) lines.push(`供应商: ${escapeHtml(task.supplier_url)}`);
    }
    if (listing) {
      lines.push(`<br>--- 上架草稿 ---`);
      lines.push(`草稿ID: ${escapeHtml(listing.draft_id || listing.id || "-")}`);
      lines.push(`售价: ¥${listing.price}`);
    }
    if (plan) {
      lines.push(`<br>--- 倒卖计划 ---`);
      lines.push(`计划ID: ${escapeHtml(plan.plan_id)}`);
    }
    if (draft) {
      lines.push(`<br>--- 采购草稿 ---`);
      lines.push(`草稿ID: ${escapeHtml(draft.draft_id)}`);
      lines.push(`数量: ${draft.quantity}`);
    }

    alert(lines.join("\n"));
  } catch (error) {
    alert("查询失败: " + error.message);
  }
}

// Wire up order listeners
$("#refresh-orders")?.addEventListener("click", refreshOrders);
refreshOrders().catch(() => {});

// ============================================================
// Physical Sourcing (NEW)
// ============================================================

async function submitSourcingForm(event) {
  event.preventDefault();
  const form = $("#sourcing-form");
  const fd = new FormData(form);
  const payload = {
    title: fd.get("title"),
    category: fd.get("category"),
    cost_price: Number(fd.get("cost_price")),
    resale_price: Number(fd.get("resale_price")),
    shipping_cost: Number(fd.get("shipping_cost") || 0),
    shipping_origin: fd.get("shipping_origin"),
  };
  try {
    const result = await api("/sourcing/evaluate", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderSourcingResult(result);
    renderResult({ type: "sourcing_evaluation", result });
  } catch (error) {
    alert("评估失败: " + error.message);
  }
}

function renderSourcingResult(data) {
  const container = $("#sourcing-result");
  if (!container) return;
  const economics = data.economics || {};
  const strategy = data.listing_strategy || {};
  const cities = data.recommended_cities || [];

  container.innerHTML = `
    <div style="margin-bottom: 12px;">
      <strong style="font-size: 16px;">${data.is_viable ? '✅ 值得做' : '⚠️ 需谨慎'}</strong>
      <span style="margin-left: 12px; color: ${economics.profit_margin >= 0.3 ? '#22c55e' : '#ef4444'};">
        利润率 ${(economics.profit_margin * 100).toFixed(1)}%
      </span>
    </div>
    <div class="snapshot-row">
      <span class="snapshot-field">利润: ¥${economics.profit}</span>
      <span class="snapshot-field">总成本: ¥${economics.total_cost}</span>
      <span class="snapshot-field">平台费: ¥${economics.platform_fee}</span>
    </div>
    <div class="snapshot-row" style="margin-top: 8px;">
      <span class="snapshot-field">类型: ${data.is_standard_product ? '标品(看价格)' : '非标(看详情)'}</span>
      <span class="snapshot-field">城市: ${cities.slice(0, 3).join(', ')}</span>
    </div>
    <div class="snapshot-row" style="margin-top: 8px;">
      <span class="snapshot-field">引流策略: ${strategy.note || '-'}</span>
    </div>
  `;
}

async function generateListingStrategy() {
  const form = $("#sourcing-form");
  const fd = new FormData(form);
  const payload = {
    title: fd.get("title"),
    category: fd.get("category"),
    actual_price: Number(fd.get("resale_price")),
    cost_price: Number(fd.get("cost_price")),
    profit_margin: 0.35,
    shipping_origin: fd.get("shipping_origin"),
  };
  try {
    const result = await api("/listings/strategy", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderStrategyResult(result);
    renderResult({ type: "listing_strategy", result });
  } catch (error) {
    alert("生成策略失败: " + error.message);
  }
}

function renderStrategyResult(data) {
  const container = $("#strategy-result");
  if (!container) return;
  const ps = data.price_strategy || {};
  const ls = data.location_strategy || {};
  const is = data.image_strategy || {};

  container.innerHTML = `
    <div style="margin-bottom: 16px;">
      <strong style="font-size: 16px;">上架策略</strong>
      <span style="margin-left: 12px; color: #94a3b8;">${data.product_title}</span>
    </div>
    <div style="background: #1e293b; padding: 12px; border-radius: 8px; margin-bottom: 12px;">
      <div style="color: #22c55e; font-weight: bold; margin-bottom: 8px;">💰 定价策略</div>
      <div>实际售价: ¥${ps.actual_price}</div>
      <div>引流价: ¥${ps.bait_price} (${(ps.bait_price_ratio * 100).toFixed(0)}%)</div>
      <div>方法: ${ps.explanation}</div>
    </div>
    <div style="background: #1e293b; padding: 12px; border-radius: 8px; margin-bottom: 12px;">
      <div style="color: #3b82f6; font-weight: bold; margin-bottom: 8px;">📍 发布地址</div>
      <div>主城市: ${ls.primary_city}</div>
      <div>备选: ${(ls.alternative_cities || []).join(', ')}</div>
      <div style="color: #94a3b8; font-size: 12px;">${ls.reasoning}</div>
    </div>
    <div style="background: #1e293b; padding: 12px; border-radius: 8px; margin-bottom: 12px;">
      <div style="color: #f59e0b; font-weight: bold; margin-bottom: 8px;">🖼️ 首图策略</div>
      <div>类型: ${is.image_type === 'scene' ? '场景图' : is.image_type === 'pile' ? '堆积图' : '实拍图'}</div>
      <ul style="margin: 4px 0; padding-left: 16px; color: #94a3b8;">
        ${(is.recommendations || []).map(r => `<li>${r}</li>`).join('')}
      </ul>
    </div>
    <div style="background: #1e293b; padding: 12px; border-radius: 8px;">
      <div style="color: #a855f7; font-weight: bold; margin-bottom: 8px;">✍️ 描述重点</div>
      <div>${data.description_focus}</div>
      <div style="margin-top: 4px; color: #94a3b8;">标签: ${(data.tag_suggestions || []).join(', ')}</div>
    </div>
  `;
}

// ============================================================
// After Sales (NEW)
// ============================================================

async function submitAftersalesForm(event) {
  event.preventDefault();
  const form = $("#aftersales-form");
  const fd = new FormData(form);
  const payload = {
    buyer_message: fd.get("buyer_message"),
    product_title: fd.get("product_title"),
    product_price: Number(fd.get("product_price") || 0),
    product_size: fd.get("product_size"),
  };
  try {
    const result = await api("/aftersales/cases", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderAftersalesResult(result);
    renderResult({ type: "after_sales", result });
    refreshAftersalesCases();
  } catch (error) {
    alert("创建工单失败: " + error.message);
  }
}

function renderAftersalesResult(data) {
  const container = $("#aftersales-feed");
  if (!container) return;
  const case_ = data;
  container.innerHTML = `
    <div class="purchase-task-item" style="margin-bottom: 12px;">
      <div class="purchase-task-header">
        <span class="purchase-task-title">工单 ${case_.id}</span>
        <span class="resale-badge resale-badge--task-${case_.status}">${case_.status}</span>
      </div>
      <div class="purchase-task-body">
        <div class="snapshot-field">问题类型: ${case_.issue_type}</div>
        <div class="snapshot-field">补偿: ¥${case_.compensation_amount}</div>
      </div>
      <div style="background: #1e293b; padding: 8px; border-radius: 6px; margin-top: 8px;">
        <div style="color: #22c55e; font-weight: bold; margin-bottom: 4px;">💬 建议回复</div>
        <div style="white-space: pre-wrap;">${escapeHtml(case_.handler_response)}</div>
      </div>
    </div>
  `;
}

async function refreshAftersalesCases() {
  try {
    const cases = await api("/aftersales/cases");
    const stats = await api("/aftersales/stats");
    renderAftersalesStats(stats);
    renderAftersalesList(cases);
  } catch {
    $("#aftersales-stats").innerHTML = '无法加载统计';
  }
}

function renderAftersalesStats(data) {
  const container = $("#aftersales-stats");
  if (!container) return;
  container.innerHTML = `
    <div class="snapshot-row">
      <span class="snapshot-field">总工单: ${data.total_cases}</span>
      <span class="snapshot-field">解决率: ${data.resolution_rate}%</span>
      <span class="snapshot-field">总赔偿: ¥${data.total_compensation}</span>
    </div>
  `;
}

function renderAftersalesList(cases) {
  const container = $("#aftersales-feed");
  if (!container) return;
  if (!cases.length) {
    container.innerHTML = '<div class="empty-state">暂无工单</div>';
    return;
  }
  container.innerHTML = cases.slice(-5).map(c => `
    <div class="purchase-task-item" style="margin-bottom: 8px;">
      <div class="purchase-task-header">
        <span class="purchase-task-title">${c.id}</span>
        <span class="resale-badge">${c.status}</span>
      </div>
      <div class="snapshot-field">${c.issue_type} | ¥${c.compensation_amount}</div>
    </div>
  `).join('');
}

// ============================================================
// Stealth Templates (NEW)
// ============================================================

async function loadStealthTemplates(scenario) {
  try {
    const result = await api(`/sourcing/stealth-templates${scenario ? '?scenario=' + scenario : ''}`);
    const container = $("#stealth-content");
    if (!container) return;
    if (scenario) {
      container.textContent = result[scenario] || '话术未找到';
    } else {
      container.textContent = Object.entries(result).map(([k, v]) => `[${k}]\n${v}`).join('\n\n');
    }
  } catch {
    $("#stealth-content").textContent = '加载失败';
  }
}

// Wire up physical sourcing event listeners
$("#sourcing-form")?.addEventListener("submit", submitSourcingForm);
$("#sourcing-strategy-btn")?.addEventListener("click", generateListingStrategy);
$("#refresh-sourcing")?.addEventListener("click", () => {
  $("#sourcing-result").innerHTML = '填写表单后点击评估';
});

$("#aftersales-form")?.addEventListener("submit", submitAftersalesForm);
$("#refresh-aftersales")?.addEventListener("click", refreshAftersalesCases);

$("#refresh-strategy")?.addEventListener("click", () => {
  $("#strategy-result").innerHTML = '在左侧"货源评估"面板中填写产品信息并点击"生成策略"';
});

$("#refresh-stealth")?.addEventListener("click", () => loadStealthTemplates());
document.querySelectorAll(".stealth-scenario").forEach(btn => {
  btn.addEventListener("click", () => loadStealthTemplates(btn.dataset.scenario));
});

// Load physical sourcing data on startup
refreshAftersalesCases().catch(() => {});
loadStealthTemplates().catch(() => {});

// OPC Dashboard
function renderOPCNextActions(actions = []) {
  const list = Array.isArray(actions) ? actions : [];
  if (!list.length) {
    return `
      <div class="opc-section">
        <h4>下一步动作</h4>
        <p class="opc-muted">暂无待处理动作。</p>
      </div>
    `;
  }
  const labelMap = { high: "高", medium: "中", low: "低" };
  const items = list.map((action) => {
    const priority = String(action.priority || "low").replace(/[^a-z_-]/g, "") || "low";
    const count = Number(action.count || 0);
    return `
      <div class="opc-action opc-action-${priority}">
        <span class="opc-priority">${escapeHtml(labelMap[priority] || priority)}</span>
        <div class="opc-action-body">
          <strong>${escapeHtml(action.title || action.kind || "-")}</strong>
          <span>${escapeHtml(action.detail || "")}</span>
        </div>
        ${count ? `<span class="opc-action-count">${count}</span>` : ""}
      </div>
    `;
  }).join("");
  return `
    <div class="opc-section">
      <h4>下一步动作</h4>
      <div class="opc-action-list">${items}</div>
    </div>
  `;
}

function renderOPCReadiness(account = {}, adapter = {}) {
  const adapterReady = adapter.ready ? "就绪" : "未就绪";
  const adapterMissing = (adapter.missing || []).join(", ") || "无";
  const syncCapabilities = adapter.sync_capabilities || {};
  const syncText = [
    syncCapabilities.orders ? "订单同步" : "",
    syncCapabilities.listings ? "商品同步" : "",
  ].filter(Boolean).join(" / ") || "未声明";
  const recommendations = account.recommendations || [];
  const recommendationHtml = recommendations.length
    ? recommendations.slice(0, 3).map((item) => {
      const actions = (item.actions || []).join(" / ");
      return `<li>${escapeHtml(item.cookie_id || "-")}: ${escapeHtml(actions || "待检查")}</li>`;
    }).join("")
    : "<li>账号准备度暂无异常。</li>";
  return `
    <div class="opc-section">
      <h4>准备度</h4>
      <div class="opc-readiness-grid">
        <div>
          <span class="opc-muted">账号</span>
          <strong>${Number(account.ready_for_seller_ops || 0)} / ${Number(account.total_accounts || 0)}</strong>
          <small>可进入 seller ops</small>
        </div>
        <div>
          <span class="opc-muted">AI 回复</span>
          <strong>${Number(account.ai_enabled_accounts || 0)}</strong>
          <small>已启用账号</small>
        </div>
        <div>
          <span class="opc-muted">执行适配器</span>
          <strong>${escapeHtml(adapter.adapter || "unknown")} · ${adapterReady}</strong>
          <small>缺失: ${escapeHtml(adapterMissing)} · ${escapeHtml(syncText)}</small>
        </div>
      </div>
      <ul class="opc-readiness-notes">${recommendationHtml}</ul>
    </div>
  `;
}

function renderOPCMessageImportScheduler(scheduler = {}) {
  const last = scheduler.last_result || {};
  const profiles = scheduler.profiles || {};
  const dueProfiles = scheduler.due_profiles || [];
  const health = scheduler.health || {};
  const healthActions = Array.isArray(health.actions) ? health.actions : [];
  const healthLabel = {
    unconfigured: "setup",
    critical: "critical",
    attention: "attention",
    healthy: "healthy",
  }[health.status] || "setup";
  const status = scheduler.enabled ? (scheduler.due ? "due" : "active") : "paused";
  const error = scheduler.last_error || "";
  const profileItems = Object.entries(profiles).slice(0, 4).map(([key, profile]) => {
    const profileStatus = profile.enabled ? (profile.due ? "due" : "active") : "paused";
    return `<li><strong>${escapeHtml(profile.account || key)} / ${escapeHtml(profileStatus)}</strong><span>runs ${Number(profile.run_count || 0)} / backoff ${Number(profile.backoff_seconds || 0)}s</span></li>`;
  }).join("");
  const healthItems = healthActions.slice(0, 6).map((item) => `
    <li data-priority="${escapeHtml(item.priority || "low")}">
      <strong>${escapeHtml(item.account || "-")} / ${escapeHtml(item.title || item.kind || "review")}</strong>
      <span>${escapeHtml(item.suggested_action || item.detail || "")}</span>
    </li>
  `).join("");
  return `
    <div class="opc-section">
      <h4>Message Import Scheduler</h4>
      <div class="opc-runtime-grid">
        <div><span class="opc-muted">status</span><strong>${escapeHtml(status)}</strong></div>
        <div><span class="opc-muted">health</span><strong>${escapeHtml(healthLabel)}</strong></div>
        <div><span class="opc-muted">account</span><strong>${escapeHtml(scheduler.account || "-")}</strong></div>
        <div><span class="opc-muted">profiles</span><strong>${Number(scheduler.total_profiles || Object.keys(profiles).length || 0)}</strong></div>
        <div><span class="opc-muted">due profiles</span><strong>${dueProfiles.length}</strong></div>
        <div><span class="opc-muted">missing profiles</span><strong>${(health.missing_profiles || []).length}</strong></div>
        <div><span class="opc-muted">interval</span><strong>${Number(scheduler.interval_seconds || 0)}s</strong></div>
        <div><span class="opc-muted">runs</span><strong>${Number(scheduler.run_count || 0)}</strong></div>
        <div><span class="opc-muted">failures</span><strong>${Number(scheduler.consecutive_failures || 0)}</strong></div>
        <div><span class="opc-muted">backoff</span><strong>${Number(scheduler.backoff_seconds || 0)}s</strong></div>
        <div><span class="opc-muted">created</span><strong>${Number(last.created || 0)}</strong></div>
        <div><span class="opc-muted">failed</span><strong>${Number(last.failed || 0)}</strong></div>
      </div>
      <ul class="opc-evidence-list">
        <li>
          <strong>${scheduler.last_run_at ? escapeHtml(scheduler.last_run_at) : "no import yet"}</strong>
          <span>${error ? escapeHtml(error) : `selected ${Number(last.conversations_selected || 0)} conversation(s)`}</span>
        </li>
        ${profileItems}
      </ul>
      <div class="opc-scheduler-health">
        <span class="opc-muted">Account actions</span>
        <ul class="opc-evidence-list opc-scheduler-health-list">
          ${healthItems || "<li><strong>scheduler profiles healthy</strong><span>No account action is required.</span></li>"}
        </ul>
      </div>
    </div>
  `;
}

function renderOPCOrderRuntime(order_runtime = {}) {
  const byStatus = order_runtime.by_status || {};
  const statusItems = Object.entries(byStatus).map(([status, count]) => `
    <span>${escapeHtml(status)} ${Number(count || 0)}</span>
  `).join("");
  const actionItems = (order_runtime.next_actions || []).slice(0, 5).map((item) => `
    <li>
      <strong>${escapeHtml(item.order_id || "-")} · ${escapeHtml(item.status || "-")}</strong>
      <span>${escapeHtml(item.pending_blocker || item.operator_next_action || "")}</span>
    </li>
  `).join("");
  return `
    <div class="opc-section">
      <h4>Order Runtime</h4>
      <div class="opc-runtime-grid">
        <div><span class="opc-muted">operational</span><strong>${Number(order_runtime.operational_orders ?? order_runtime.total_orders ?? 0)}</strong></div>
        <div><span class="opc-muted">synthetic</span><strong>${Number(order_runtime.synthetic_orders || 0)}</strong></div>
        <div><span class="opc-muted">fulfillment</span><strong>${Number(order_runtime.needs_fulfillment || 0)}</strong></div>
        <div><span class="opc-muted">after sales</span><strong>${Number(order_runtime.after_sales || 0)}</strong></div>
        <div><span class="opc-muted">blocked</span><strong>${Number(order_runtime.blocked || 0)}</strong></div>
        <div><span class="opc-muted">locked</span><strong>${Number(order_runtime.locked || 0)}</strong></div>
      </div>
      <div class="opc-runtime-statuses">${statusItems || "<span>none</span>"}</div>
      <ul class="opc-evidence-list">${actionItems || "<li><strong>no active order actions</strong><span></span></li>"}</ul>
    </div>
  `;
}

function renderOPCExecutionEvidence(execution_evidence = {}) {
  const blockers = Array.isArray(execution_evidence.recent_blockers)
    ? execution_evidence.recent_blockers
    : [];
  const items = blockers.slice(0, 5).map((item) => `
    <li>
      <strong>${escapeHtml(item.detected_blocker || "-")}</strong>
      <span>${escapeHtml(item.action || item.job_id || "-")} · ${escapeHtml(item.operator_next_action || "")}</span>
    </li>
  `).join("");
  return `
    <div class="opc-section">
      <h4>Execution Evidence</h4>
      <div class="opc-runtime-grid">
        <div><span class="opc-muted">blocked failures</span><strong>${Number(execution_evidence.failed_with_blocker || 0)}</strong></div>
      </div>
      <ul class="opc-evidence-list">${items || "<li><strong>no captured blockers</strong><span></span></li>"}</ul>
    </div>
  `;
}

function getPrimaryAccountId() {
  const profiles = Array.isArray(state.accountProfiles) ? state.accountProfiles : [];
  const enabled = profiles.find((profile) => profile && profile.enabled !== false && profile.cookie_id);
  const fallback = profiles.find((profile) => profile && profile.cookie_id);
  return (enabled || fallback || {}).cookie_id || "";
}

function focusAccountBootstrapForm() {
  const onboarding = $("#browser-account-onboarding");
  if (!onboarding) {
    return;
  }
  onboarding.scrollIntoView({ behavior: "smooth", block: "center" });
  $("#open-xianyu-login-browser")?.focus();
}

function renderFirstRunGateAction(gate = {}, cookieId = "") {
  const gateId = gate.id || "";
  if (!gateId) {
    return "";
  }
  if (gate.passed && gateId !== "ready") {
    return "";
  }
  const labels = {
    account_enabled: "Enable account",
    account_configured: "导入账号",
    prompt_ready: "初始化 Prompt",
    inventory_context: "同步订单/商品",
    sync_current: "同步订单/商品",
    no_unassigned_work: "预览归属修复",
    adapter_ready: "刷新仪表板",
    sync_capabilities: "刷新仪表板",
    runtime_connected: "检查运行时",
    runtime_fresh: "刷新运行时",
    runtime_unblocked: "处理验证",
    ready: "生成日快照",
  };
  const label = labels[gateId] || "查看下一步";
  return `
    <button
      type="button"
      class="first-run-gate-action"
      data-gate-id="${escapeHtml(gateId)}"
      data-cookie-id="${escapeHtml(cookieId)}"
      onclick="handleFirstRunGateAction(this.dataset.gateId, this.dataset.cookieId)"
    >${escapeHtml(label)}</button>
  `;
}

function renderFirstRunReadiness(readiness = {}) {
  const gates = Array.isArray(readiness.gates) ? readiness.gates : [];
  const next = readiness.next_step || {};
  const selected = readiness.selected_account || {};
  const selectedCookieId = selected.cookie_id || "";
  const status = readiness.ready_for_first_run ? "可以试跑" : "待补齐";
  const items = gates.map((gate) => {
    const cls = gate.passed ? "first-run-gate-ok" : "first-run-gate-warn";
    return `
      <div class="first-run-gate ${cls}">
        <span>${gate.passed ? "PASS" : "TODO"}</span>
        <div>
          <strong>${escapeHtml(gate.title || gate.id || "-")}</strong>
          <small>${escapeHtml(gate.detail || "")}</small>
          ${renderFirstRunGateAction(gate, selectedCookieId)}
        </div>
      </div>
    `;
  }).join("");
  return `
    <div class="opc-section">
      <h4>首轮实盘试跑</h4>
      <div class="first-run-summary">
        <strong>${escapeHtml(status)}</strong>
        <span>${escapeHtml(next.title || "")}</span>
        ${selectedCookieId ? `<span>Account ${escapeHtml(selectedCookieId)}: ${Number(selected.passed_count || 0)}/${Number(selected.total_gates || 0)}</span>` : ""}
      </div>
      <div class="first-run-gates">${items}</div>
    </div>
  `;
}

async function handleFirstRunGateAction(gateId, selectedCookieId = "") {
  if (gateId === "account_configured") {
    focusAccountBootstrapForm();
    return;
  }
  if (gateId === "account_enabled") {
    if (!selectedCookieId) {
      focusAccountBootstrapForm();
      return;
    }
    await api(`/accounts/${encodeURIComponent(selectedCookieId)}/toggle`, { method: "POST" });
    await refreshOPCDashboard();
    return;
  }
  if (gateId === "prompt_ready") {
    const cookieId = selectedCookieId || getPrimaryAccountId();
    if (!cookieId) {
      alert("先导入账号，再初始化 Prompt。");
      focusAccountBootstrapForm();
      return;
    }
    await bootstrapAccountPromptProfile(cookieId);
    return;
  }
  if (gateId === "inventory_context" || gateId === "sync_current") {
    const cookieId = selectedCookieId || getPrimaryAccountId();
    if (!cookieId) {
      alert("先导入账号，再同步订单/商品。");
      focusAccountBootstrapForm();
      return;
    }
    await syncAccountQueue(cookieId);
    return;
  }
  if (gateId === "no_unassigned_work") {
    await repairAccountAttribution();
    return;
  }
  if (gateId === "ready") {
    await runDailyReview();
    return;
  }
  await refreshOPCDashboard();
}

function renderUnassignedWorkReview(review = null) {
  if (!review) return "";
  const items = Array.isArray(review.items) ? review.items : [];
  const accounts = (state.accountProfiles || []).filter((profile) => profile?.cookie_id && profile.enabled !== false);
  const accountOptions = accounts
    .map((profile) => `<option value="${escapeHtml(profile.cookie_id)}">${escapeHtml(profile.cookie_id)}${profile.remark ? ` · ${escapeHtml(profile.remark)}` : ""}</option>`)
    .join("");
  const recommendationLabels = {
    claim: "可认领",
    quarantine: "建议隔离",
    manual_audit: "需人工审计",
  };
  const rows = items.map((item) => {
    const actions = (item.actions || []).join(" / ") || "无平台动作";
    const sourceAccounts = (item.source_cookie_ids || []).join(" / ") || "缺少账号";
    const disabled = !item.resolution_allowed;
    return `
      <label class="account-attribution-review-item ${disabled ? "is-blocked" : ""}">
        <input type="checkbox" class="account-attribution-choice" value="${escapeHtml(item.work_order_id || "")}" ${disabled ? "disabled" : ""} />
        <span class="account-attribution-review-body">
          <strong>${escapeHtml(item.kind || "work_order")} · ${escapeHtml(item.work_order_id || "-")}</strong>
          <span>${escapeHtml(actions)} · 当前归属 ${escapeHtml(sourceAccounts)}</span>
          <small>
            ${Number(item.age_hours || 0)} 小时 · 待审批 ${Number(item.pending_approvals || 0)} ·
            ${escapeHtml(recommendationLabels[item.recommended_resolution] || item.recommended_resolution || "待审阅")}
            ${(item.blocked_reasons || []).length ? ` · ${escapeHtml(item.blocked_reasons.join("、"))}` : ""}
          </small>
        </span>
      </label>
    `;
  }).join("");
  return `
    <div class="account-attribution-review">
      <div class="account-attribution-review-summary">
        <strong>历史未归属任务 ${Number(review.count || 0)}</strong>
        <span>建议隔离 ${Number(review.summary?.quarantine || 0)} · 可认领 ${Number(review.summary?.claim || 0)} · 人工审计 ${Number(review.summary?.manual_audit || 0)}</span>
      </div>
      <div class="account-attribution-review-list">${rows || '<span class="opc-muted">没有待处理的历史任务。</span>'}</div>
      ${items.length ? `
        <div class="account-attribution-resolution-controls">
          <label>认领到账号
            <select id="account-attribution-target">
              <option value="">选择已启用账号</option>
              ${accountOptions}
            </select>
          </label>
          <button type="button" onclick="resolveAccountAttribution('claim')" ${accountOptions ? "" : "disabled"}>认领已选</button>
          <button type="button" class="danger-btn" onclick="resolveAccountAttribution('quarantine')">隔离已选</button>
        </div>
      ` : ""}
    </div>
  `;
}

function renderAccountQueueDrilldown(accountQueues = [], unassigned = {}) {
  const accounts = Array.isArray(accountQueues) ? accountQueues : [];
  const unassignedQueues = unassigned.queues || {};
  const hasUnassigned = Object.values(unassignedQueues).some((value) => Number(value || 0) > 0);
  const cards = accounts.map((account_queue) => renderAccountQueueCard(account_queue)).join("");
  const unassignedCard = hasUnassigned ? renderAccountQueueCard(unassigned, true) : "";
  const repairTools = hasUnassigned ? `
    <div class="account-attribution-tools">
      <button type="button" onclick="repairAccountAttribution()">审阅历史任务</button>
    </div>
  ` : "";
  return `
    <div class="opc-section">
      <h4>账号队列</h4>
      ${repairTools}
      ${renderUnassignedWorkReview(state.unassignedWorkReview)}
      <div class="account-queue-grid">
        ${cards || `<div class="empty-state">暂无账号队列数据</div>`}
        ${unassignedCard}
      </div>
    </div>
  `;
}

async function repairAccountAttribution() {
  try {
    state.unassignedWorkReview = await api("/dashboard/account-queues/unassigned-work");
    await refreshOPCDashboard();
  } catch (error) {
    alert("历史任务审阅失败: " + error.message);
  }
}

async function resolveAccountAttribution(mode) {
  const review = state.unassignedWorkReview;
  if (!review?.preview_token) {
    await repairAccountAttribution();
    return;
  }
  const workOrderIds = [...document.querySelectorAll(".account-attribution-choice:checked")]
    .map((input) => input.value)
    .filter(Boolean);
  if (!workOrderIds.length) {
    alert("请先勾选要处理的工作单。");
    return;
  }
  const normalizedMode = mode === "quarantine" ? "quarantine" : "claim";
  const cookieId = normalizedMode === "claim" ? String($("#account-attribution-target")?.value || "") : "";
  if (normalizedMode === "claim" && !cookieId) {
    alert("请选择这些工作单实际所属的卖家账号。");
    return;
  }
  const actionLabel = normalizedMode === "claim" ? `认领到 ${cookieId}` : "隔离且禁止执行";
  if (!confirm(`确认${actionLabel}这 ${workOrderIds.length} 条历史工作单？该操作只处理已勾选 ID。`)) return;
  try {
    const result = await api("/dashboard/account-queues/repair-attribution", {
      method: "POST",
      body: JSON.stringify({
        dry_run: false,
        mode: normalizedMode,
        cookie_id: cookieId,
        work_order_ids: workOrderIds,
        preview_token: review.preview_token,
        confirmation: `${normalizedMode.toUpperCase()} ${workOrderIds.length}`,
      }),
    });
    renderResult({ type: "unassigned_work_resolution", result });
    state.unassignedWorkReview = null;
    await refreshOPCDashboard();
  } catch (error) {
    const reason = error.detail?.reason || error.message;
    alert(`历史任务处理被阻止：${reason}。请重新审阅后再操作。`);
    await repairAccountAttribution();
  }
}

async function syncAccountQueue(cookieId) {
  if (!cookieId) return;
  if (!confirm(`同步账号 ${cookieId} 的订单和商品？`)) {
    return;
  }
  try {
    const data = await api(`/adapter/sync-persist?cookie_id=${encodeURIComponent(cookieId)}`, {
      method: "POST",
    });
    renderResult({ type: "account_sync", result: data });
    await refreshOPCDashboard();
    await loadAccountProfiles();
  } catch (error) {
    alert(`同步失败：${error.message}`);
  }
}

function renderAccountSyncFreshness(sync_freshness = {}) {
  const resources = Object.keys(sync_freshness || {}).sort();
  if (!resources.length) {
    return `<div class="account-sync-freshness"><span class="account-sync-stale">sync_freshness: none</span></div>`;
  }
  const items = resources.map((resource) => {
    const state = sync_freshness[resource] || {};
    const status = state.fresh ? "fresh" : "stale";
    const cls = state.fresh ? "account-sync-fresh" : "account-sync-stale";
    const count = Number(state.last_count || 0);
    const adapter = state.adapter || "";
    return `<span class="${cls}">${escapeHtml(resource)} ${status} · ${count}${adapter ? ` · ${escapeHtml(adapter)}` : ""}</span>`;
  }).join("");
  return `<div class="account-sync-freshness">${items}</div>`;
}

function renderAccountRuntimeBadge(account_runtime = {}) {
  const status = account_runtime.connection_status || "unknown";
  const tokenFresh = account_runtime.token_fresh === true;
  const cookieFresh = account_runtime.cookie_fresh === true;
  const blocked = Boolean(account_runtime.pending_verification_reason || account_runtime.last_error);
  const connected = ["connected", "degraded"].includes(status);
  const good = connected && tokenFresh && cookieFresh && !blocked;
  const cls = good ? "account-runtime-good" : "account-runtime-warn";
  const manualCount = Number(account_runtime.manual_takeover_count || 0);
  const heartbeat = Number(account_runtime.last_heartbeat_at || 0);
  const heartbeatText = heartbeat ? `heartbeat ${Math.round(heartbeat)}` : "heartbeat none";
  const freshness = [
    tokenFresh ? "token fresh" : "token stale",
    cookieFresh ? "cookie fresh" : "cookie stale",
  ].join(" · ");
  const blocker = account_runtime.pending_verification_reason || account_runtime.last_error || "";
  return `
    <div class="account-runtime-badges">
      <span class="${cls}">runtime ${escapeHtml(status)}</span>
      <span class="${tokenFresh && cookieFresh ? "account-runtime-good" : "account-runtime-warn"}">${escapeHtml(freshness)}</span>
      <span class="${heartbeat ? "account-runtime-good" : "account-runtime-warn"}">${escapeHtml(heartbeatText)}</span>
      ${manualCount ? `<span class="account-runtime-warn">manual ${manualCount}</span>` : ""}
      ${blocker ? `<span class="account-runtime-warn">${escapeHtml(blocker)}</span>` : ""}
    </div>
  `;
}

function renderAccountQueueCard(account_queue = {}, isUnassigned = false) {
  const queues = account_queue.queues || {};
  const prompt = account_queue.prompt_profile || {};
  const actions = Array.isArray(account_queue.next_actions) ? account_queue.next_actions : [];
  const enabled = account_queue.enabled !== false;
  const statusClass = enabled && prompt.ready ? "account-queue-ok" : "account-queue-warn";
  const title = isUnassigned ? "未分配队列" : (account_queue.remark || account_queue.cookie_id || "-");
  const subtitle = isUnassigned ? "缺少 cookie_id 的任务" : (account_queue.cookie_id || "-");
  const cookieId = account_queue.cookie_id || "";
  const runtimeBadge = renderAccountRuntimeBadge(account_queue.account_runtime || {});
  const syncFreshness = renderAccountSyncFreshness(account_queue.sync_freshness || {});
  const syncActions = !isUnassigned && cookieId ? `
    <div class="account-sync-actions">
      <button type="button" data-cookie-id="${escapeHtml(cookieId)}" onclick="syncAccountQueue(this.dataset.cookieId)">同步订单/商品</button>
    </div>
  ` : "";
  const actionItems = actions.slice(0, 3).map((action) => `
    <li>
      <strong>${escapeHtml(action.title || action.kind || "-")}</strong>
      <span>${escapeHtml(action.detail || "")}</span>
    </li>
  `).join("");
  return `
    <div class="account-queue-card ${statusClass}">
      <div class="account-queue-header">
        <div>
          <strong>${escapeHtml(title)}</strong>
          <span>${escapeHtml(subtitle)}</span>
        </div>
        <span class="account-queue-status">${enabled ? (prompt.ready ? "就绪" : "需配置") : "停用"}</span>
      </div>
      <div class="account-queue-metrics">
        <span>审批 ${Number(queues.pending_approvals || 0)}</span>
        <span>执行 ${Number(queues.queued_execution || 0)}</span>
        <span>核验 ${Number(queues.verification_required || 0)}</span>
        <span>失败 ${Number(queues.failed_jobs || 0)}</span>
        <span>回复 ${Number(queues.pending_reply_drafts || 0)}</span>
        <span>商品 ${Number(queues.items || 0)}</span>
      </div>
      ${runtimeBadge}
      ${syncFreshness}
      ${syncActions}
      <ul class="account-queue-actions">
        ${actionItems || "<li><strong>无待办</strong><span>保持当前运营节奏。</span></li>"}
      </ul>
    </div>
  `;
}

async function refreshOPCDashboard() {
  try {
    const [data, accountQueueData, firstRunReadiness] = await Promise.all([
      api("/dashboard/opc-daily"),
      api("/dashboard/account-queues"),
      api("/dashboard/first-run-readiness"),
    ]);
    const el = $("#opc-dashboard-content");
    if (!el) return;

    const actions = data.today_actions || {};
    const funnel = data.funnel || {};
    const bottleneck = data.bottleneck || {};
    const categories = data.category_performance || [];
    const notes = data.source_notes || [];
    const accountReadiness = data.account_readiness || {};
    const adapterHealth = data.adapter_health || {};
    const messageImportScheduler = data.message_import_scheduler || {};
    const orderRuntime = data.order_runtime || {};
    const executionEvidence = data.execution_evidence || {};
    const nextActions = data.next_actions || [];

    let html = "";
    html += renderFirstRunReadiness(firstRunReadiness);
    html += renderOPCNextActions(nextActions);
    html += renderOPCReadiness(accountReadiness, adapterHealth);
    html += renderOPCMessageImportScheduler(messageImportScheduler);
    html += renderOPCOrderRuntime(orderRuntime);
    html += renderOPCExecutionEvidence(executionEvidence);
    html += renderAccountQueueDrilldown(accountQueueData.accounts || [], accountQueueData.unassigned || {});
    html += '<div class="opc-section">';
    html += '<h4>今日待办</h4>';
    html += '<div class="approval-count-badges">';
    html += `<span class="badge">待审批 ${actions.pending_approvals || 0}</span>`;
    html += `<span class="badge">待执行 ${actions.pending_execution || 0}</span>`;
    html += `<span class="badge">待回复 ${actions.pending_reply_drafts || 0}</span>`;
    html += `<span class="badge badge-warn">待核验 ${actions.verification_required || 0}</span>`;
    html += `<span class="badge badge-warn">失败 ${actions.failed_jobs || 0}</span>`;
    html += `<span class="badge">新发现 ${actions.new_discoveries || 0}</span>`;
    html += '</div></div>';

    html += '<div class="opc-section">';
    html += '<h4>漏斗指标</h4>';
    html += '<div class="approval-count-badges">';
    html += `<span class="badge">候选 ${funnel.new_candidates_this_week || 0}</span>`;
    html += `<span class="badge">测试中 ${funnel.testing || 0}</span>`;
    html += `<span class="badge">有咨询 ${funnel.with_inquiries || 0}</span>`;
    html += `<span class="badge badge-ok">成交 ${funnel.converted || 0}</span>`;
    html += `<span class="badge badge-warn">售后 ${funnel.after_sales || 0}</span>`;
    html += '</div></div>';

    html += '<div class="opc-section">';
    html += '<h4>瓶颈分析</h4>';
    html += `<p><strong>阶段：</strong>${escapeHtml(bottleneck.stage || '未知')}</p>`;
    html += `<p><strong>说明：</strong>${escapeHtml(bottleneck.description || '暂无数据')}</p>`;
    if ((bottleneck.affected_categories || []).length > 0) {
      html += `<p><strong>受影响品类：</strong>${bottleneck.affected_categories.map(escapeHtml).join('、')}</p>`;
    }
    html += '</div>';

    if (categories.length > 0) {
      html += '<div class="opc-section">';
      html += '<h4>品类表现</h4>';
      html += '<table style="width:100%;font-size:13px;border-collapse:collapse;">';
      html += '<tr style="border-bottom:1px solid #334155;"><th style="text-align:left;padding:4px;">品类</th><th>候选</th><th>审批</th><th>成交</th><th>利润</th><th style="text-align:left;padding:4px;">建议</th></tr>';
      for (const cat of categories) {
        html += `<tr style="border-bottom:1px solid #1e293b;">`;
        html += `<td style="padding:4px;">${escapeHtml(cat.category || '未分类')}</td>`;
        html += `<td style="text-align:center;">${cat.candidates || 0}</td>`;
        html += `<td style="text-align:center;">${cat.approved || 0}</td>`;
        html += `<td style="text-align:center;">${cat.orders || 0}</td>`;
        html += `<td style="text-align:center;">¥${cat.revenue || 0}</td>`;
        html += `<td style="padding:4px;font-size:12px;">${escapeHtml(cat.action || '')}</td>`;
        html += '</tr>';
      }
      html += '</table></div>';
    }

    if (notes.length > 0) {
      html += '<div class="opc-section" style="margin-top:8px;font-size:12px;color:#94a3b8;">';
      html += '<strong>数据源说明：</strong> ';
      html += notes.map(escapeHtml).join('；');
      html += '</div>';
    }

    el.innerHTML = html;
  } catch (e) {
    const el = $("#opc-dashboard-content");
    if (el) el.innerHTML = `<p style="color:#f87171;">加载失败：${escapeHtml(e.message)}</p>`;
  }
}

async function runWeeklyReview() {
  try {
    const data = await api("/dashboard/weekly-review", { method: "POST" });
    alert(`周复盘已保存：${data.file || '未知路径'}`);
  } catch (e) {
    alert(`生成失败：${e.message}`);
  }
}

async function runDailyReview() {
  try {
    const data = await api("/dashboard/daily-review", { method: "POST" });
    alert(`日快照已保存：${data.file || '未知路径'}`);
  } catch (e) {
    alert(`生成失败：${e.message}`);
  }
}

$("#refresh-opc-dashboard")?.addEventListener("click", refreshOPCDashboard);
$("#run-daily-review")?.addEventListener("click", runDailyReview);
$("#run-weekly-review")?.addEventListener("click", runWeeklyReview);

// Load OPC dashboard on startup
refreshOPCDashboard().catch(() => {});

// ===== Commercial Alpha Trial (Phase 19) =====

async function refreshCommercialTrials() {
  try {
    const [activeResp, listResp, candidatesResp, accountsResp] = await Promise.all([
      api("/commercial-trials/active"),
      api("/commercial-trials"),
      api("/resale/candidates"),
      api("/accounts"),
    ]);
    const el = $("#commercial-trial-summary");
    if (!el) return;
    const activeTrials = Array.isArray(activeResp.trials)
      ? activeResp.trials
      : (activeResp.trial ? [activeResp.trial] : []);
    const allTrials = listResp.trials || [];
    const candidates = Array.isArray(candidatesResp) ? candidatesResp : (candidatesResp.candidates || []);
    state.commercialCandidates = candidates;
    populateCommercialTrialInputs(accountsResp, candidates);
    const launchForm = $("#commercial-trial-create-form");
    const launchPreview = $("#commercial-launch-preview");
    if (launchForm) launchForm.hidden = activeTrials.length > 0;
    if (launchPreview) {
      launchPreview.hidden = activeTrials.length > 0 || !state.commercialLaunchPreview;
      launchPreview.innerHTML = state.commercialLaunchPreview
        ? renderCommercialLaunchPreview(state.commercialLaunchPreview)
        : "";
    }
    if (!activeTrials.length) {
      el.innerHTML = renderCommercialTrial(null, allTrials, candidates);
      return;
    }
    el.innerHTML = activeTrials
      .map((trial) => renderCommercialTrial(trial, allTrials, candidates))
      .join("");
  } catch (e) {
    const el = $("#commercial-trial-summary");
    if (el) el.innerHTML = `<p style="color:#f87171;">加载失败：${escapeHtml(e.message)}</p>`;
  }
}

function populateCommercialTrialInputs(accounts, candidates) {
  const accountSelect = $("#ct-account-select");
  if (accountSelect) {
    const previous = accountSelect.value;
    const enabledAccounts = (Array.isArray(accounts) ? accounts : []).filter(
      (account) => account && account.enabled !== false && account.cookie_id
    );
    accountSelect.innerHTML = [
      '<option value="">选择已启用账号</option>',
      ...enabledAccounts.map((account) => {
        const label = account.remark
          ? `${account.cookie_id} · ${account.remark}`
          : account.cookie_id;
        return `<option value="${escapeHtml(account.cookie_id)}">${escapeHtml(label)}</option>`;
      }),
    ].join("");
    if (enabledAccounts.some((account) => account.cookie_id === previous)) {
      accountSelect.value = previous;
    } else if (enabledAccounts.length === 1) {
      accountSelect.value = enabledAccounts[0].cookie_id;
    }
  }

  const candidateSelect = $("#ct-candidate-select");
  if (candidateSelect) {
    const previous = candidateSelect.value;
    const candidateRows = (Array.isArray(candidates) ? [...candidates] : [])
      .filter((candidate) => (
        candidate?.candidate_id
        && String(candidate.sales_platform || "xianyu").toLowerCase() === "xianyu"
        && String(candidate.market_scope || "domestic").toLowerCase() === "domestic"
      ))
      .sort((left, right) => {
        const leftReady = left.manual_decision === "approve" && left.status === "approved" ? 1 : 0;
        const rightReady = right.manual_decision === "approve" && right.status === "approved" ? 1 : 0;
        return rightReady - leftReady || String(left.title || "").localeCompare(String(right.title || ""), "zh-CN");
      });
    candidateSelect.innerHTML = [
      '<option value="">选择一个候选</option>',
      ...candidateRows.map((candidate) => {
        const decision = candidate.manual_decision === "approve" && candidate.status === "approved" ? "已批准" : "待决策";
        const title = String(candidate.title || candidate.candidate_id).slice(0, 48);
        return `<option value="${escapeHtml(candidate.candidate_id)}">${escapeHtml(title)} · ${escapeHtml(decision)}</option>`;
      }),
    ].join("");
    if (candidateRows.some((candidate) => candidate.candidate_id === previous)) {
      candidateSelect.value = previous;
    } else if (candidateRows.filter((candidate) => candidate.manual_decision === "approve" && candidate.status === "approved").length === 1) {
      candidateSelect.value = candidateRows.find(
        (candidate) => candidate.manual_decision === "approve" && candidate.status === "approved"
      ).candidate_id;
    }
  }
  updateCommercialLaunchCategory();
}

function updateCommercialLaunchCategory() {
  const candidateId = String($("#ct-candidate-select")?.value || "");
  const candidate = (state.commercialCandidates || []).find((item) => item.candidate_id === candidateId);
  const categoryInput = $("#ct-category");
  if (categoryInput) categoryInput.value = candidate?.category || "";
}

function renderCommercialTrialOrders(trial) {
  const orderLinks = Array.isArray(trial.order_links) ? trial.order_links : [];
  if (!orderLinks.length) return "";
  const trialId = escapeHtml(trial.trial_id || "");
  const rows = orderLinks.map((link) => {
    const orderId = escapeHtml(link.order_id || "");
    const handoff = link.handoff || null;
    const fulfillmentAudit = link.fulfillment_audit || {};
    const profitAudit = link.profit_audit || {};
    const releaseAllowed = ["shipped", "delivered", "refund_requested", "disputed", "closed"].includes(link.status)
      || handoff?.approval_status === "rejected";
    const lockText = link.locked ? `已锁定 · ${escapeHtml(link.lock_owner || "-")}` : "未锁定";
    const handoffBody = handoff
      ? `
        <div class="trial-handoff-evidence">
          <span>采购审批 ${escapeHtml(handoff.approval_id || "-")}</span>
          <span>审批状态 ${escapeHtml(handoff.approval_status || "-")}</span>
          <span>任务单 ${escapeHtml(handoff.work_order_id || "-")}</span>
          <span>采购承诺 ¥${Number(handoff.total_cost || 0).toFixed(2)}</span>
          <span>${handoff.released_at ? "履约锁已释放" : lockText}</span>
        </div>
        ${link.locked && releaseAllowed ? `
          <button type="button" class="text-btn" data-trial-id="${trialId}" data-order-id="${orderId}"
            onclick="releaseCommercialFulfillmentHandoff(this.dataset.trialId, this.dataset.orderId)">释放履约锁</button>
        ` : ""}
      `
      : `
        <form class="trial-fulfillment-form" data-trial-id="${trialId}" data-order-id="${orderId}"
          onsubmit="submitCommercialFulfillmentHandoff(event)">
          <label>数量
            <input name="quantity" type="number" min="1" max="100" value="1" required />
          </label>
          <label>收货地摘要
            <input name="buyer_address_summary" maxlength="80" placeholder="上海浦东新区***" required />
          </label>
          <div class="trial-confirmation-grid">
            <label><input name="specs_confirmed" type="checkbox" required />规格已核对</label>
            <label><input name="address_confirmed" type="checkbox" required />地址已核对</label>
            <label><input name="supplier_stock_reconfirmed" type="checkbox" required />库存已复核</label>
            <label><input name="supplier_quote_reconfirmed" type="checkbox" required />报价已复核</label>
          </div>
          <label class="trial-handoff-note">交接备注
            <input name="operator_note" maxlength="500" placeholder="平台付款状态和供应商时效已复核" />
          </label>
          <button type="submit" class="primary">创建履约交接</button>
        </form>
      `;
    return `
      <div class="trial-order-row">
        <div class="trial-order-head">
          <strong>订单 ${orderId}</strong>
          <span>${escapeHtml(link.status || "-")} · ¥${Number(link.amount || 0).toFixed(2)}</span>
          <span>候选 ${escapeHtml(link.candidate_id || "-")}</span>
        </div>
        ${handoffBody}
        <div class="trial-order-audit">
          <span class="${fulfillmentAudit.ready ? "is-ready" : "is-gap"}">
            履约证据 ${fulfillmentAudit.ready ? "完整" : escapeHtml((fulfillmentAudit.gaps || []).join("、") || "待补齐")}
          </span>
          <span class="${profitAudit.ready ? "is-ready" : "is-gap"}">
            利润证据 ${profitAudit.ready ? "完整" : escapeHtml((profitAudit.gaps || []).join("、") || "待补齐")}
          </span>
        </div>
      </div>
    `;
  }).join("");
  return `
    <section class="trial-orders">
      <h4>付款后履约</h4>
      ${rows}
    </section>
  `;
}

function renderCommercialTrialTraffic(trial) {
  const items = Array.isArray(trial.item_performance) ? trial.item_performance : [];
  if (!items.length) return "";
  const trialId = escapeHtml(trial.trial_id || "");
  const variableLabels = {
    title: "标题",
    cover: "首图",
    price: "价格",
    description: "详情",
    location: "所在地",
    shipping_promise: "交付承诺",
  };
  const decisionLabels = {
    keep_change: "保留改动",
    revert: "回退改动",
    stop_listing: "停止商品",
  };
  const rows = items.map((item) => {
    const itemId = escapeHtml(item.item_id || "");
    const latest = item.latest_observation || null;
    const recommendation = item.recommendation || {};
    const active = item.active_experiment || null;
    const latestExperiment = item.latest_experiment || null;
    const pending = item.pending_experiment || (latestExperiment?.status === "proposed" ? latestExperiment : null);
    const observationForm = ["active", "paused"].includes(trial.lifecycle) ? `
      <form class="trial-traffic-form" data-trial-id="${trialId}" data-item-id="${itemId}"
        onsubmit="submitCommercialTrafficObservation(event)">
        <label>浏览
          <input name="views" type="number" min="0" value="${Number(item.views || 0)}" required />
        </label>
        <label>想要
          <input name="wants" type="number" min="0" value="${Number(item.wants || 0)}" required />
        </label>
        <label class="trial-traffic-note">凭证备注
          <input name="evidence_note" minlength="4" maxlength="300" placeholder="闲鱼商品页 08-11 09:30" required />
        </label>
        <label class="trial-traffic-note">凭证引用
          <input name="evidence_ref" maxlength="300" placeholder="截图文件名或同步批次" />
        </label>
        <button type="submit" class="text-btn">记录快照</button>
      </form>
    ` : "";
    const pendingBody = pending ? `
      <div class="trial-experiment-pending">
        <div class="trial-experiment-head">
          <strong>${escapeHtml(variableLabels[pending.variable] || pending.variable)}</strong>
          <span>变更待确认</span>
          <span>审批 ${escapeHtml(pending.approval_status || "missing")}</span>
          <span>执行 ${escapeHtml(pending.job_status || pending.approval_execution_status || "未执行")}</span>
        </div>
        <div class="trial-experiment-change">
          <span>原值：${escapeHtml(pending.before_value || "-")}</span>
          <span>目标值：${escapeHtml(pending.after_value || "-")}</span>
        </div>
        <div class="trial-experiment-audit">
          <span>审批 ${escapeHtml(pending.approval_id || "-")}</span>
          <span>任务单 ${escapeHtml(pending.work_order_id || "-")}</span>
        </div>
        ${pending.approval_status === "approved" ? `
          <form class="trial-experiment-activation" data-trial-id="${trialId}"
            data-experiment-id="${escapeHtml(pending.experiment_id || "")}"
            data-manual-required="${pending.manual_required ? "true" : "false"}"
            onsubmit="submitCommercialExperimentActivation(event)">
            <label class="trial-experiment-confirmation">
              <input name="manual_confirmation" type="checkbox" ${pending.manual_required ? "required" : ""} />
              平台改动已确认
            </label>
            <label>确认备注
              <input name="evidence_note" maxlength="500" ${pending.manual_required ? 'minlength="8" required' : ""}
                placeholder="${pending.manual_required ? "人工编辑时间及核对内容" : "自动执行失败时填写人工确认"}" />
            </label>
            <label>凭证引用
              <input name="evidence_ref" maxlength="300" ${pending.manual_required ? "required" : ""}
                placeholder="截图文件名或同步批次" />
            </label>
            <button type="submit" class="primary">确认变更并开始计时</button>
          </form>
        ` : ""}
        <button type="button" class="text-btn trial-experiment-cancel"
          onclick="cancelCommercialExperiment('${trialId}', '${escapeHtml(pending.experiment_id || "")}')">取消提案</button>
      </div>
    ` : "";
    const experimentBody = active ? `
      <div class="trial-experiment-active">
        <div class="trial-experiment-head">
          <strong>${escapeHtml(variableLabels[active.variable] || active.variable)}</strong>
          <span>${item.experiment_due ? "已到复核时间" : `运行 ${Number(active.planned_days || 0)} 天`}</span>
        </div>
        <div class="trial-experiment-change">
          <span>原值：${escapeHtml(active.before_value || "-")}</span>
          <span>改为：${escapeHtml(active.after_value || "-")}</span>
        </div>
        <form class="trial-experiment-decision" data-trial-id="${trialId}"
          data-experiment-id="${escapeHtml(active.experiment_id || "")}"
          onsubmit="submitCommercialExperimentDecision(event)">
          <input name="note" minlength="4" maxlength="500" placeholder="结论依据" required />
          <label><input name="allow_early" type="checkbox" />提前结束</label>
          ${Object.entries(decisionLabels).map(([value, label]) => `
            <button type="submit" class="text-btn" name="decision" value="${value}" ${item.has_result_observation ? "" : "disabled"}>${label}</button>
          `).join("")}
        </form>
      </div>
    ` : (pending ? pendingBody : (latest && trial.lifecycle === "active" ? `
      <form class="trial-experiment-form" data-trial-id="${trialId}" data-item-id="${itemId}"
        onsubmit="submitCommercialExperimentStart(event)">
        <label>实验变量
          <select name="variable" required>
            ${Object.entries(variableLabels).map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}
          </select>
        </label>
        <label>原值
          <input name="before_value" maxlength="300" required />
        </label>
        <label>改为
          <input name="after_value" maxlength="300" required />
        </label>
        <label>假设
          <input name="hypothesis" minlength="4" maxlength="300" required />
        </label>
        <label>天数
          <input name="planned_days" type="number" min="1" max="14" value="${Number(trial.controls?.experiment_days || 3)}" required />
        </label>
        <button type="submit" class="text-btn">提交变更审批</button>
      </form>
    ` : ""));
    const latestResult = latestExperiment?.status === "decided" ? `
      <div class="trial-experiment-result">
        上次结论 ${escapeHtml(decisionLabels[latestExperiment.decision] || latestExperiment.decision || "-")} ·
        浏览 ${Number(latestExperiment.result?.views_delta || 0) >= 0 ? "+" : ""}${Number(latestExperiment.result?.views_delta || 0)} ·
        想要 ${Number(latestExperiment.result?.wants_delta || 0) >= 0 ? "+" : ""}${Number(latestExperiment.result?.wants_delta || 0)} ·
        咨询 ${Number(latestExperiment.result?.inquiries_delta || 0) >= 0 ? "+" : ""}${Number(latestExperiment.result?.inquiries_delta || 0)}
      </div>
    ` : "";
    return `
      <div class="trial-performance-row">
        <div class="trial-performance-head">
          <strong>商品 ${itemId}</strong>
          <span>候选 ${escapeHtml(item.candidate_id || "-")}</span>
          <span>${latest ? `${escapeHtml(latest.local_date || "-")} · ${escapeHtml(latest.source || "-")}` : "未记录流量"}</span>
        </div>
        <div class="trial-performance-metrics">
          <span>浏览 <strong>${Number(item.views || 0)}</strong></span>
          <span>想要 <strong>${Number(item.wants || 0)}</strong></span>
          <span>咨询 <strong>${Number(item.inquiries || 0)}</strong></span>
          <span>付款 <strong>${Number(item.paid_orders || 0)}</strong></span>
        </div>
        <div class="trial-performance-action">${escapeHtml(recommendation.message || "-")}</div>
        ${observationForm}
        ${experimentBody}
        ${latestResult}
      </div>
    `;
  }).join("");
  return `
    <section class="trial-performance">
      <h4>商品日观察</h4>
      ${rows}
    </section>
  `;
}

function renderCommercialTrial(trial, allTrials, candidates) {
  if (!trial) {
    return `
      <div class="commercial-trial-empty">
        <p>暂无活跃批次。请在上方选择账号和一个候选，检查通过后启动首批 Commercial Alpha 试运行。</p>
        ${allTrials.length ? `<p style="margin-top:6px;">历史批次：${allTrials.length}</p>` : ""}
      </div>
    `;
  }
  const stages = trial.stages || [];
  const stopReasons = trial.stop_reasons || [];
  const nextAction = trial.next_action || {};
  const controls = trial.controls || {};
  const candidateIds = trial.candidate_ids || [];
  const committedCandidateIds = trial.committed_candidate_ids || [];
  const stageLabels = {
    account_ready: "账号就绪",
    candidate_pool: "候选池",
    publish_ready: "发布预检",
    approval_created: "发布审批",
    publish_confirmed: "平台发布",
    inquiry_observed: "有效咨询",
    inquiry_responded: "咨询回复",
    paid_order: "付款订单",
    fulfillment_observed: "履约发货",
    profit_reconciled: "利润对账",
    review_decision: "复盘决策",
  };
  const lifecycleLabels = {
    active: "运行中",
    paused: "已暂停",
    stopped: "已停止",
    completed: "已完成",
  };
  const eligible = (candidates || []).filter(
    (c) =>
      String(c.category || "").trim() === String(trial.category || "").trim() &&
      !candidateIds.includes(c.candidate_id)
  );

  const stageHtml = stages
    .map(
      (stage) => `
    <div class="trial-stage-row ${stage.status === "pass" ? "is-pass" : "is-pending"}">
      <span class="trial-stage-code">${escapeHtml(stageLabels[stage.code] || stage.code)}</span>
      <span class="trial-stage-status">${stage.status === "pass" ? "✓ 通过" : "待办"}</span>
      <span class="trial-stage-evidence">${escapeHtml(stage.evidence || "")}</span>
    </div>
  `
    )
    .join("");

  const stopHtml = trial.stop_required
    ? `
    <div class="trial-stop-alert">
      <strong>停止条件已触发</strong>
      <div>${stopReasons.map(escapeHtml).join("；") || "请立即复核"}</div>
    </div>
  `
    : "";

  const attachHtml =
    eligible.length > 0 && candidateIds.length < Number(controls.max_candidates || 0)
      ? `
    <div class="trial-attach">
      <select id="trial-candidate-select-${escapeHtml(trial.trial_id)}">
        ${eligible
          .map(
            (c) =>
              `<option value="${escapeHtml(c.candidate_id)}">${escapeHtml(String(c.title || c.candidate_id).slice(0, 40))}</option>`
          )
          .join("")}
      </select>
      <button type="button" class="text-btn" onclick="attachTrialCandidate('${escapeHtml(trial.trial_id)}')">添加候选</button>
    </div>
  `
      : `<span class="trial-attach-empty">暂无可添加的同品类候选</span>`;

  const draftHtml = candidateIds
    .map(
      (cid) => {
        const blocked = Boolean(trial.batch_draft_blocked) || committedCandidateIds.includes(cid);
        const reason = committedCandidateIds.includes(cid)
          ? "该候选已有发布审批"
          : (trial.batch_draft_blocked_reason || "当前可创建发布草稿");
        return `<button type="button" class="text-btn trial-draft-btn" title="${escapeHtml(reason)}" ${blocked ? "disabled" : ""} onclick="createTrialListingDraft('${escapeHtml(trial.trial_id)}', '${escapeHtml(cid)}')">草稿 ${escapeHtml(cid)}</button>`;
      }
    )
    .join("");

  const decisionButtons = `
    ${trial.lifecycle === "paused" ? `<button type="button" class="text-btn" onclick="decideCommercialTrial('${escapeHtml(trial.trial_id)}', 'continue')">继续</button>` : ""}
    ${trial.lifecycle === "active" ? `<button type="button" class="text-btn" onclick="decideCommercialTrial('${escapeHtml(trial.trial_id)}', 'pause')">暂停</button>` : ""}
    <button type="button" class="text-btn" onclick="decideCommercialTrial('${escapeHtml(trial.trial_id)}', 'stop')">停止</button>
    <button type="button" class="text-btn" ${trial.review_eligible ? "" : "disabled"} onclick="decideCommercialTrial('${escapeHtml(trial.trial_id)}', 'complete')">完成</button>
    <button type="button" class="text-btn" onclick="reportCommercialTrial('${escapeHtml(trial.trial_id)}')">生成报告</button>
  `;

  return `
    <div class="commercial-trial-card">
      <div class="commercial-trial-header">
        <strong>${escapeHtml(trial.trial_id)}</strong>
        <span class="commercial-trial-lifecycle">${escapeHtml(lifecycleLabels[trial.lifecycle] || trial.lifecycle)}</span>
      </div>
      <div class="commercial-trial-meta">
        <span>账号 ${escapeHtml(trial.cookie_id || "-")}</span>
        <span>品类 ${escapeHtml(trial.category || "-")}</span>
        <span>开始 ${escapeHtml(trial.started_at || "-")}</span>
      </div>
      <div class="trial-controls-grid">
        <span>候选 ${Number(trial.candidate_ids?.length || 0)}/${Number(controls.max_candidates || 0)}</span>
        <span>发布承诺 ${Number(trial.publish_commitment_count || 0)}/${Number(controls.max_published || 0)}</span>
        <span>真实发布 ${Number(trial.published_count || 0)}</span>
        <span>采购敞口 ¥${Number(trial.exposure || 0)}/${Number(controls.cash_budget || 0)}</span>
        <span>发布备货 ¥${Number(trial.committed_cash_requirement || 0)}</span>
        <span>履约采购 ¥${Number(trial.procurement_commitment || 0)}</span>
        <span>已实现利润 ¥${Number(trial.realized_profit || 0)}</span>
        <span>咨询 ${Number(trial.inquiry_count || 0)}/${Number(controls.target_inquiries || 0)}</span>
        <span>已回复 ${Number(trial.inquiry_response_count || 0)}/${Number(controls.target_inquiries || 0)}</span>
        <span>售后率 ${(Number(trial.after_sales_ratio || 0) * 100).toFixed(0)}%</span>
        <span>付款 ${Number(trial.paid_order_count || 0)}/${Number(controls.target_paid_orders || 0)}</span>
      </div>
      ${stopHtml}
      <div class="trial-next-action">
        <strong>下一步：${escapeHtml(nextAction.action || "-")}</strong>
        <span>${escapeHtml(nextAction.message || "")}</span>
        ${nextAction.detail ? `<span class="trial-next-detail">${escapeHtml(nextAction.detail)}</span>` : ""}
      </div>
      <div class="trial-stage-list">
        ${stageHtml}
      </div>
      <div class="trial-attach-block">
        ${attachHtml}
      </div>
      <div class="trial-draft-actions">${draftHtml || "<span>暂无已添加候选</span>"}</div>
      ${renderCommercialTrialTraffic(trial)}
      ${renderCommercialTrialOrders(trial)}
      <div class="trial-decision-actions">${decisionButtons}</div>
    </div>
  `;
}

function buildCommercialLaunchPayload(form = $("#commercial-trial-create-form")) {
  if (!form) return null;
  const fd = new FormData(form);
  return {
    cookie_id: String(fd.get("ct_cookie_id") || "").trim(),
    candidate_id: String(fd.get("ct_candidate_id") || "").trim(),
    controls: {
      max_candidates: 1,
      target_candidates: 1,
      max_published: 1,
      cash_budget: Number(fd.get("ct_cash_budget") || 1000),
      max_realized_loss: Number(fd.get("ct_max_realized_loss") || 100),
      max_after_sales_rate: Number(fd.get("ct_max_after_sales_rate") || 0.5),
      target_inquiries: Number(fd.get("ct_target_inquiries") || 3),
      target_paid_orders: 1,
      monitor_days: Number(fd.get("ct_monitor_days") || 14),
      min_observation_hours: Number(fd.get("ct_min_observation_hours") || 24),
      min_views_for_diagnosis: Number(fd.get("ct_min_views_for_diagnosis") || 20),
      experiment_days: Number(fd.get("ct_experiment_days") || 3),
    },
  };
}

function renderCommercialLaunchPreview(preview = {}) {
  const gates = Array.isArray(preview.gates) ? preview.gates : [];
  const rows = gates.map((gate) => `
    <div class="commercial-launch-gate ${gate.status === "pass" ? "is-pass" : "is-blocked"}">
      <span>${gate.status === "pass" ? "通过" : "阻断"}</span>
      <strong>${escapeHtml(gate.title || gate.code || "-")}</strong>
      <small>${escapeHtml(gate.evidence || "")}</small>
    </div>
  `).join("");
  const termsGates = Array.isArray(preview.commercial_terms?.gates)
    ? preview.commercial_terms.gates
    : [];
  const termsRows = termsGates.map((gate) => `
    <div class="commercial-terms-gate ${gate.status === "pass" ? "is-pass" : "is-blocked"}">
      <span>${gate.status === "pass" ? "通过" : "待补"}</span>
      <strong>${escapeHtml(gate.message || gate.code || "-")}</strong>
      <small>${escapeHtml(gate.evidence || "")}</small>
    </div>
  `).join("");
  const activationSteps = Array.isArray(preview.activation_plan?.steps)
    ? preview.activation_plan.steps.filter((step) => step.status !== "pass")
    : [];
  const activationRows = activationSteps.map((step) => `
    <div class="commercial-activation-step">
      <span>${Number(step.sequence || 0)}</span>
      <div>
        <strong>${escapeHtml(step.stage || "待处理")} · ${escapeHtml(step.title || step.code || "-")}</strong>
        <small>${step.owner === "system" ? "系统" : "人工"} · ${escapeHtml(step.evidence_required || "")}</small>
        <small>验收：${escapeHtml(step.verification || "重新检查")}</small>
      </div>
    </div>
  `).join("");
  const next = preview.next_step || {};
  const actionButton = preview.ready
    ? `<button type="button" class="primary" onclick="startCommercialLaunch()">启动 1 个商品批次</button>`
    : `<button type="button" class="text-btn" data-action="${escapeHtml(next.action || "")}" onclick="handleCommercialLaunchGateAction(this.dataset.action)">处理下一门禁</button>`;
  return `
    <div class="commercial-launch-head">
      <div>
        <strong>${preview.ready ? "Day-0 可以启动" : "Day-0 尚未放行"}</strong>
        <span>${escapeHtml(preview.candidate_title || preview.candidate_id || "未选择候选")}</span>
      </div>
      <span class="${preview.ready ? "is-ready" : "is-gap"}">${Number(gates.length - (preview.failed_gate_codes || []).length)}/${gates.length}</span>
    </div>
    <div class="commercial-launch-next">
      <strong>${escapeHtml(next.title || "-")}</strong>
      <span>${escapeHtml(next.detail || "")}</span>
    </div>
    <div class="commercial-launch-gates">${rows}</div>
    ${termsRows ? `
      <details class="commercial-terms-detail" ${preview.failed_gate_codes?.includes("account_commercial_terms_ready") ? "open" : ""}>
        <summary>账号商业条款子门禁 ${termsGates.filter((gate) => gate.status === "pass").length}/${termsGates.length}</summary>
        <div class="commercial-terms-gates">${termsRows}</div>
      </details>
    ` : ""}
    <div class="commercial-activation-plan">
      <div class="commercial-activation-head">
        <strong>首批次激活工单</strong>
        <span>${Number(preview.activation_plan?.completed_steps || 0)}/${Number(preview.activation_plan?.total_steps || gates.length)}</span>
      </div>
      ${activationRows || '<div class="commercial-activation-ready">全部激活证据已就绪</div>'}
    </div>
    <div class="commercial-launch-actions">
      ${actionButton}
      <button type="button" class="text-btn" onclick="previewCommercialLaunch()">重新检查</button>
    </div>
  `;
}

async function previewCommercialLaunch(form = $("#commercial-trial-create-form")) {
  const payload = buildCommercialLaunchPayload(form);
  if (!payload?.cookie_id || !payload?.candidate_id) {
    alert("请选择一个已启用卖家账号和一个首发候选。");
    return;
  }
  try {
    state.commercialLaunchPreview = await api("/commercial-launch/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const previewNode = $("#commercial-launch-preview");
    if (previewNode) {
      previewNode.hidden = false;
      previewNode.innerHTML = renderCommercialLaunchPreview(state.commercialLaunchPreview);
      previewNode.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  } catch (error) {
    alert(`启动检查失败：${error.message}`);
  }
}

async function startCommercialLaunch() {
  const preview = state.commercialLaunchPreview;
  const payload = buildCommercialLaunchPayload();
  if (!preview?.ready || !preview.preview_token || !payload) {
    await previewCommercialLaunch();
    return;
  }
  if (!confirm(`确认用账号 ${payload.cookie_id} 和候选 ${payload.candidate_id} 启动唯一首发批次？`)) return;
  try {
    const result = await api("/commercial-launch/start", {
      method: "POST",
      body: JSON.stringify({
        ...payload,
        preview_token: preview.preview_token,
        confirmation: "START 1",
      }),
    });
    renderResult({ type: "commercial_launch", result });
    state.commercialLaunchPreview = null;
    await refreshCommercialTrials();
    await refreshOPCDashboard();
  } catch (error) {
    const reason = error.detail?.reason || error.message;
    alert(`启动被阻止：${reason}。系统将重新检查当前证据。`);
    await previewCommercialLaunch();
  }
}

async function handleCommercialLaunchGateAction(action) {
  const payload = buildCommercialLaunchPayload() || {};
  if (action === "review_unassigned_work") {
    await repairAccountAttribution();
    $("#opc-dashboard-content")?.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  if (action === "configure_account" || action === "enable_account") {
    focusAccountBootstrapForm();
    return;
  }
  if (action === "bootstrap_prompt" && payload.cookie_id) {
    await bootstrapAccountPromptProfile(payload.cookie_id);
    return;
  }
  if (action === "complete_commercial_terms") {
    await openCommercialTermsDialog();
    return;
  }
  if (action === "sync_account" && payload.cookie_id) {
    await syncAccountQueue(payload.cookie_id);
    return;
  }
  if (["select_candidate", "approve_candidate", "complete_candidate_category", "complete_candidate_preflight", "complete_candidate_cost"].includes(action)) {
    document.querySelector(".resale-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (action === "increase_budget_or_change_candidate" || action === "fix_launch_controls") {
    $("#commercial-trial-create-form [name='ct_cash_budget']")?.focus();
    return;
  }
  await refreshOPCDashboard();
  await previewCommercialLaunch();
}

async function submitCommercialTrialCreate(event) {
  event.preventDefault();
  await previewCommercialLaunch(event.currentTarget);
}

async function attachTrialCandidate(trialId) {
  const select = $(`#trial-candidate-select-${trialId}`);
  if (!select || !select.value) return;
  try {
    await api(`/commercial-trials/${trialId}/candidates/${encodeURIComponent(select.value)}`, { method: "POST" });
    await refreshCommercialTrials();
  } catch (e) {
    alert(`添加候选失败：${e.message}`);
  }
}

async function createTrialListingDraft(trialId, candidateId) {
  try {
    await api(`/commercial-trials/${trialId}/candidates/${encodeURIComponent(candidateId)}/listing-draft`, {
      method: "POST",
    });
    alert("上架草稿已创建，需审批后才能发布。");
    await refreshCommercialTrials();
  } catch (e) {
    const detail = e.detail;
    if (detail && detail.error === "batch_listing_draft_blocked") {
      alert(`批次草稿被阻断：${detail.reason || "未知原因"}`);
    } else {
      alert(`创建草稿失败：${e.message}`);
    }
  }
}

async function submitCommercialTrafficObservation(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const fd = new FormData(form);
  const payload = {
    views: Number(fd.get("views") || 0),
    wants: Number(fd.get("wants") || 0),
    evidence_note: String(fd.get("evidence_note") || "").trim(),
    evidence_ref: String(fd.get("evidence_ref") || "").trim(),
  };
  try {
    await api(
      `/commercial-trials/${encodeURIComponent(form.dataset.trialId || "")}/items/${encodeURIComponent(form.dataset.itemId || "")}/traffic-observations`,
      { method: "POST", body: JSON.stringify(payload) }
    );
    await refreshCommercialTrials();
  } catch (e) {
    alert(`记录流量失败：${e.message}`);
  }
}

async function submitCommercialExperimentStart(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const fd = new FormData(form);
  const payload = {
    variable: String(fd.get("variable") || ""),
    hypothesis: String(fd.get("hypothesis") || "").trim(),
    before_value: String(fd.get("before_value") || "").trim(),
    after_value: String(fd.get("after_value") || "").trim(),
    planned_days: Number(fd.get("planned_days") || 3),
  };
  try {
    await api(
      `/commercial-trials/${encodeURIComponent(form.dataset.trialId || "")}/items/${encodeURIComponent(form.dataset.itemId || "")}/experiments`,
      { method: "POST", body: JSON.stringify(payload) }
    );
    alert("实验变更已提交审批，平台确认后才开始计时。");
    await refreshCommercialTrials();
  } catch (e) {
    alert(`提交实验变更失败：${e.message}`);
  }
}

async function submitCommercialExperimentActivation(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const fd = new FormData(form);
  const payload = {
    manual_confirmation: fd.get("manual_confirmation") === "on",
    evidence_note: String(fd.get("evidence_note") || "").trim(),
    evidence_ref: String(fd.get("evidence_ref") || "").trim(),
  };
  const submit = form.querySelector('button[type="submit"]');
  if (submit) submit.disabled = true;
  try {
    await api(
      `/commercial-trials/${encodeURIComponent(form.dataset.trialId || "")}/experiments/${encodeURIComponent(form.dataset.experimentId || "")}/activate`,
      { method: "POST", body: JSON.stringify(payload) }
    );
    alert("平台变更证据已确认，实验计时已开始。");
    await Promise.all([refreshCommercialTrials(), refreshWorkOrders()]);
  } catch (e) {
    const detail = e.detail;
    const reason = detail && typeof detail === "object" ? detail.reason : e.message;
    alert(`实验尚未开始：${reason || "平台变更证据不足"}`);
  } finally {
    if (submit) submit.disabled = false;
  }
}

async function cancelCommercialExperiment(trialId, experimentId) {
  const note = prompt("取消原因（至少 4 个字符）");
  if (note === null) return;
  if (String(note).trim().length < 4) {
    alert("取消原因至少需要 4 个字符。");
    return;
  }
  try {
    await api(
      `/commercial-trials/${encodeURIComponent(trialId)}/experiments/${encodeURIComponent(experimentId)}/cancel`,
      { method: "POST", body: JSON.stringify({ note: String(note).trim() }) }
    );
    await refreshCommercialTrials();
  } catch (e) {
    alert(`取消实验提案失败：${e.message}`);
  }
}

async function submitCommercialExperimentDecision(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const fd = new FormData(form);
  const payload = {
    decision: String(event.submitter?.value || ""),
    note: String(fd.get("note") || "").trim(),
    allow_early: fd.get("allow_early") === "on",
  };
  try {
    await api(
      `/commercial-trials/${encodeURIComponent(form.dataset.trialId || "")}/experiments/${encodeURIComponent(form.dataset.experimentId || "")}/decision`,
      { method: "POST", body: JSON.stringify(payload) }
    );
    await refreshCommercialTrials();
  } catch (e) {
    alert(`实验决策失败：${e.message}`);
  }
}

async function submitCommercialFulfillmentHandoff(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const trialId = form.dataset.trialId || "";
  const orderId = form.dataset.orderId || "";
  const fd = new FormData(form);
  const payload = {
    quantity: Number(fd.get("quantity") || 1),
    buyer_address_summary: String(fd.get("buyer_address_summary") || "").trim(),
    specs_confirmed: fd.get("specs_confirmed") === "on",
    address_confirmed: fd.get("address_confirmed") === "on",
    supplier_stock_reconfirmed: fd.get("supplier_stock_reconfirmed") === "on",
    supplier_quote_reconfirmed: fd.get("supplier_quote_reconfirmed") === "on",
    operator_note: String(fd.get("operator_note") || "").trim(),
  };
  const submit = form.querySelector('button[type="submit"]');
  if (submit) submit.disabled = true;
  try {
    const result = await api(
      `/commercial-trials/${encodeURIComponent(trialId)}/orders/${encodeURIComponent(orderId)}/fulfillment-handoff`,
      { method: "POST", body: JSON.stringify(payload) }
    );
    alert(result.idempotent ? "该订单已有履约交接。" : "履约交接已创建，采购仍需人工审批。");
    await Promise.all([refreshCommercialTrials(), refreshWorkOrders(), refreshPurchaseTasks(), refreshOrders()]);
  } catch (e) {
    const detail = e.detail;
    const reason = detail && typeof detail === "object" ? detail.reason : e.message;
    alert(`履约交接失败：${reason || "未知原因"}`);
  } finally {
    if (submit) submit.disabled = false;
  }
}

async function releaseCommercialFulfillmentHandoff(trialId, orderId) {
  try {
    await api(
      `/commercial-trials/${encodeURIComponent(trialId)}/orders/${encodeURIComponent(orderId)}/fulfillment-release`,
      { method: "POST" }
    );
    await refreshCommercialTrials();
  } catch (e) {
    const detail = e.detail;
    const reason = detail && typeof detail === "object" ? detail.reason : e.message;
    alert(`释放履约锁失败：${reason || "未知原因"}`);
  }
}

async function decideCommercialTrial(trialId, decision) {
  const note = prompt(`请输入决策备注（${decision}）`, "");
  if (note === null) return;
  try {
    await api(`/commercial-trials/${trialId}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision, note }),
    });
    await refreshCommercialTrials();
  } catch (e) {
    alert(`决策失败：${e.message}`);
  }
}

async function reportCommercialTrial(trialId) {
  try {
    const data = await api(`/commercial-trials/${trialId}/report`, { method: "POST" });
    alert(`报告已保存：${data.file || "未知路径"}`);
  } catch (e) {
    alert(`生成报告失败：${e.message}`);
  }
}

$("#refresh-commercial-trials")?.addEventListener("click", refreshCommercialTrials);
$("#record-commercial-terms")?.addEventListener("click", openCommercialTermsDialog);
$("#commercial-trial-create-form")?.addEventListener("submit", submitCommercialTrialCreate);
$("#commercial-trial-create-form")?.addEventListener("input", (event) => {
  if (event.target?.id === "ct-candidate-select") updateCommercialLaunchCategory();
  state.commercialLaunchPreview = null;
  const previewNode = $("#commercial-launch-preview");
  if (previewNode) {
    previewNode.hidden = true;
    previewNode.innerHTML = "";
  }
});

// Load commercial alpha trial panel on startup
refreshCommercialTrials().catch(() => {});

// ---------------------------------------------------------------------------
// Commerce opportunity radar (WP03/WP05/WP06/WP09 thin loop):
// supplier quote import -> same-product match -> contribution economics
// -> opportunity -> decision card. Read/write only via /commerce/* APIs;
// no platform action is triggered from this panel.
// ---------------------------------------------------------------------------
const commerceState = {
  preview: null,
  lastMatch: null,
  opportunityId: null,
};

function commerceMoney(m) {
  if (!m) return "-";
  if (m.state === "known") return `${m.amount} ${m.currency || "CNY"}`;
  if (m.state === "not_applicable") return "不适用";
  return `未知${m.reason ? `（${m.reason}）` : ""}`;
}

function commerceStatusBadge(status) {
  const labels = { ok: "可确认", needs_confirmation: "待确认", rejected: "已拒绝" };
  return labels[status] || status;
}

async function commercePreviewImport() {
  const content = $("#commerce-quote-text")?.value?.trim();
  const resultNode = $("#commerce-preview-result");
  if (!content) {
    resultNode.textContent = "请先粘贴报价内容";
    return;
  }
  try {
    const preview = await api("/commerce/supplier-imports/preview", {
      method: "POST",
      body: JSON.stringify({ kind: "pasted_text", content }),
    });
    commerceState.preview = preview;
    const rows = (preview.rows || [])
      .map(
        (row) =>
          `<tr><td>${row.row_number}</td><td>${escapeHtml(row.fields?.sku || "-")}</td>` +
          `<td>${commerceMoney(row.fields?.price)}</td><td>${commerceMoney(row.fields?.freight)}</td>` +
          `<td>${commerceStatusBadge(row.status)}</td><td>${escapeHtml((row.issues || []).join("；") || "-")}</td></tr>`
      )
      .join("");
    resultNode.innerHTML =
      `<p>共 ${preview.summary?.total ?? 0} 行：可确认 ${preview.summary?.ok ?? 0}，待确认 ${preview.summary?.needs_confirmation ?? 0}，拒绝 ${preview.summary?.rejected ?? 0}` +
      `${(preview.unmapped_columns || []).length ? `；未映射列：${escapeHtml(preview.unmapped_columns.join(","))}` : ""}</p>` +
      `<table class="commerce-table"><thead><tr><th>#</th><th>SKU</th><th>单价</th><th>运费</th><th>状态</th><th>问题</th></tr></thead><tbody>${rows}</tbody></table>`;
    $("#commerce-confirm-btn").disabled = false;
  } catch (e) {
    resultNode.textContent = `预览失败：${e.message}`;
  }
}

async function commerceConfirmImport() {
  const preview = commerceState.preview;
  const supplier = $("#commerce-supplier")?.value?.trim();
  if (!preview || !supplier) {
    $("#commerce-preview-result").textContent = "缺少预览或供应商名称";
    return;
  }
  try {
    const result = await api(`/commerce/supplier-imports/${preview.preview_id}/confirm`, {
      method: "POST",
      body: JSON.stringify({
        content_sha256: preview.content_sha256,
        revision: preview.revision,
        supplier,
        operator: "console",
      }),
    });
    const ids = (result.snapshots || []).map((s) => `${s.source_item_id}: ${s.record_id}`);
    $("#commerce-preview-result").innerHTML =
      `<p>批次 ${result.created ? "已创建" : "已存在（幂等）"}：<code>${escapeHtml(result.batch?.record_id || "")}</code></p>` +
      `<p>快照：<br>${ids.map((i) => escapeHtml(i)).join("<br>")}</p>`;
    $("#commerce-confirm-btn").disabled = true;
  } catch (e) {
    $("#commerce-preview-result").textContent = `确认失败：${e.message}`;
  }
}

async function commerceSaveMarket() {
  const body = {
    sku: $("#market-sku")?.value?.trim(),
    title: $("#market-title")?.value?.trim(),
    spec: $("#market-spec")?.value?.trim(),
    brand: $("#market-brand")?.value?.trim(),
    price: $("#market-price")?.value?.trim(),
    condition: $("#market-condition")?.value?.trim(),
    unit: $("#market-unit")?.value?.trim(),
  };
  if (!body.sku || !body.price) {
    $("#market-save-result").textContent = "型号与展示价必填";
    return;
  }
  try {
    const result = await api("/commerce/market-observations", {
      method: "POST",
      body: JSON.stringify(body),
    });
    $("#market-save-result").innerHTML =
      `快照 <code>${escapeHtml(result.snapshot?.record_id || "")}</code> 已登记（人工观察，非成交事实）`;
    $("#match-market-id").value = result.snapshot?.record_id || "";
  } catch (e) {
    $("#market-save-result").textContent = `登记失败：${e.message}`;
  }
}

async function commerceRunMatch() {
  const left = $("#match-market-id")?.value?.trim();
  const right = $("#match-supplier-id")?.value?.trim();
  try {
    const result = await api("/commerce/product-matches", {
      method: "POST",
      body: JSON.stringify({ left_snapshot_id: left, right_snapshot_id: right }),
    });
    commerceState.lastMatch = { ...result, marketId: left, supplierId: right };
    const conflicts = (result.conflicts || [])
      .map((c) => `${c.field}: ${JSON.stringify(c.left)} vs ${JSON.stringify(c.right)}`)
      .join("；");
    $("#match-result").innerHTML =
      `<p>判定 <strong>${result.grade}</strong>（规则 ${escapeHtml(String(result.rule_version || ""))}）` +
      ` 相似度 ${result.recall_score ?? "-"}（仅报告）</p>` +
      `<p>${conflicts ? `冲突：${escapeHtml(conflicts)}` : escapeHtml(result.explanation || "")}</p>` +
      `<p>match_id：<code>${escapeHtml(result.match_id || "")}</code></p>`;
  } catch (e) {
    $("#match-result").textContent = `匹配失败：${e.message}`;
  }
}

async function commerceCreateOpportunity() {
  const match = commerceState.lastMatch;
  if (!match) {
    $("#opp-result").textContent = "请先完成匹配判定";
    return;
  }
  try {
    const opportunity = await api("/commerce/opportunities", {
      method: "POST",
      body: JSON.stringify({
        market_snapshot_id: match.marketId,
        supplier_snapshot_id: match.supplierId,
        match_id: match.match_id,
      }),
    });
    commerceState.opportunityId = opportunity.record_id;
    $("#opp-result").innerHTML = `商机 <code>${escapeHtml(opportunity.record_id)}</code> 已创建（${escapeHtml(opportunity.status)}）`;
  } catch (e) {
    $("#opp-result").textContent = `创建失败：${e.message}`;
  }
}

async function commerceEvaluate() {
  const opportunityId = commerceState.opportunityId;
  if (!opportunityId) {
    $("#opp-result").textContent = "请先创建商机";
    return;
  }
  const low = $("#cost-market-low")?.value?.trim();
  const body = {
    platform_fee_rate: $("#fee-rate")?.value?.trim() || null,
    platform_fee_fixed: $("#fee-fixed")?.value?.trim() || null,
    packaging: $("#cost-packaging")?.value ? { state: "known", amount: $("#cost-packaging").value, currency: "CNY" } : null,
    expected_return_loss: $("#cost-return")?.value ? { state: "known", amount: $("#cost-return").value, currency: "CNY" } : null,
    attributable_costs: $("#cost-attributable")?.value ? { state: "known", amount: $("#cost-attributable").value, currency: "CNY" } : null,
  };
  if (low) {
    body.scenario_overrides = { conservative: { sale_price: { state: "known", amount: low, currency: "CNY" } } };
  }
  try {
    const result = await api(`/commerce/opportunities/${opportunityId}/evaluate`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    const s = result.economics?.scenarios || {};
    const line = (name) => {
      const sc = s[name];
      if (!sc) return "";
      return `<tr><td>${name}</td><td>${commerceMoney(sc.contribution_profit)}</td>` +
        `<td>${sc.contribution_margin ?? "-"}</td><td>${commerceMoney(sc.break_even_price)}</td>` +
        `<td>${escapeHtml((sc.missing || []).join("；") || "-")}</td></tr>`;
    };
    $("#opp-result").innerHTML =
      `<p>状态：<strong>${escapeHtml(result.opportunity?.status || "")}</strong> ｜ 现金占用：${commerceMoney(result.economics?.cash_occupied)}</p>` +
      `<table class="commerce-table"><thead><tr><th>情景</th><th>贡献利润</th><th>利润率</th><th>盈亏平衡价</th><th>缺口</th></tr></thead>` +
      `<tbody>${line("conservative")}${line("base")}${line("optimistic")}</tbody></table>` +
      `<p>${result.economics?.scenarios_note ? escapeHtml(result.economics.scenarios_note) : ""}</p>`;
    await commerceRenderDecisionCard(opportunityId);
    await commerceRefreshFunnel();
  } catch (e) {
    $("#opp-result").textContent = `核算失败：${e.message}`;
  }
}

async function commerceRenderDecisionCard(opportunityId) {
  try {
    const card = await api(`/commerce/opportunities/${opportunityId || commerceState.opportunityId}/decision-card`);
    const decisions = card.decisions || [];
    const latest = decisions[decisions.length - 1];
    if (!latest) {
      $("#decision-card").textContent = "暂无决策记录";
      return;
    }
    const checks = (latest.constraint_checks || [])
      .map((c) => `${c.check}:${c.state}`)
      .join(" ｜ ");
    $("#decision-card").innerHTML =
      `<p><strong>${escapeHtml(latest.actor_role)}</strong> / ${escapeHtml(latest.decision_type)} / ${new Date(latest.created_at).toLocaleString()}</p>` +
      `<p>${escapeHtml(latest.summary)}</p>` +
      `<p>建议：${escapeHtml(latest.recommendation || "-")}</p>` +
      `<p>门禁：${escapeHtml(checks)} ｜ 授权：${escapeHtml(latest.authorization_type)}（人工）</p>` +
      `<p>未知项：${escapeHtml((latest.unknowns || []).join("；"))}</p>` +
      `<p>依据：${(latest.evidence_refs || []).map((r) => escapeHtml(`${r.ref_type}:${r.ref_id}`)).join(" ｜ ")}</p>`;
  } catch (e) {
    $("#decision-card").textContent = `决策卡读取失败：${e.message}`;
  }
}

async function commerceRefreshFunnel() {
  try {
    const funnel = await api("/commerce/opportunities/funnel");
    const opportunities = await api("/commerce/opportunities");
    const rows = (opportunities.opportunities || [])
      .map(
        (o) =>
          `<tr><td>${escapeHtml(o.canonical_sku)}</td><td>${escapeHtml(o.status)}</td>` +
          `<td>${escapeHtml(o.status_reason || "-")}</td><td>r${o.revision}</td></tr>`
      )
      .join("");
    $("#commerce-funnel").innerHTML =
      `<p>原始 ${funnel.discovered_total} ｜ 有匹配 ${funnel.with_match} ｜ 成本齐全 ${funnel.cost_complete} ｜ 保守利润为正 ${funnel.positive_conservative_profit}</p>` +
      `<table class="commerce-table"><thead><tr><th>SKU</th><th>状态</th><th>原因</th><th>版本</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) {
    $("#commerce-funnel").textContent = `漏斗读取失败：${e.message}`;
  }
}

$("#commerce-preview-btn")?.addEventListener("click", commercePreviewImport);
$("#commerce-confirm-btn")?.addEventListener("click", commerceConfirmImport);
$("#market-save-btn")?.addEventListener("click", commerceSaveMarket);
$("#match-run-btn")?.addEventListener("click", commerceRunMatch);
$("#opp-create-btn")?.addEventListener("click", commerceCreateOpportunity);
$("#opp-evaluate-btn")?.addEventListener("click", commerceEvaluate);
$("#commerce-refresh")?.addEventListener("click", commerceRefreshFunnel);

// Pi task desk: submit read-only analysis roles and execute through real Pi
async function piSubmitTask() {
  const role = $("#pi-role-select")?.value;
  try {
    const job = await api("/commerce/pi-tasks/submit", {
      method: "POST",
      body: JSON.stringify({ role }),
    });
    $("#pi-task-result").innerHTML = `任务已入队：<code>${escapeHtml(job.record_id)}</code>（${escapeHtml(role)}）`;
  } catch (e) {
    $("#pi-task-result").textContent = `提交失败：${e.message}`;
  }
}

async function piRunNext() {
  const node = $("#pi-task-result");
  node.textContent = "正在通过真实 Pi 执行（约 1-2 分钟）…";
  try {
    const result = await api("/commerce/pi-tasks/run-next", { method: "POST" });
    if (result.outcome === "no_claimable_job") {
      node.textContent = "没有可执行任务，请先提交";
      return;
    }
    node.innerHTML = `<p>结果：<strong>${escapeHtml(result.outcome)}</strong> ｜ 任务 <code>${escapeHtml(result.job_id || "")}</code>` +
      `${result.decision_id ? ` ｜ 决策 <code>${escapeHtml(result.decision_id)}</code>` : ""}</p>` +
      `<p>证据：GET /commerce/pi-tasks/${escapeHtml(result.job_id || "")}/evidence</p>`;
  } catch (e) {
    node.textContent = `执行失败：${e.message}`;
  }
}

async function piRefreshStatus() {
  try {
    const stats = await api("/commerce/agent-jobs/stats");
    const shadow = await api("/commerce/shadow-observer/status");
    const rows = Object.entries(stats.by_status || {})
      .map(([status, count]) => `${escapeHtml(status)}: ${count}`)
      .join(" ｜ ");
    $("#pi-task-result").innerHTML =
      `<p>任务：${rows || "无"} ｜ 影子线程：${shadow.background_thread ? "运行中" : "关闭"} ｜ 影子样本：${shadow.metrics?.total ?? 0} 条（${shadow.metrics?.allowed ?? 0} 允许）</p>` +
      `<p>${escapeHtml(shadow.metrics?.readiness_note || "")}</p>`;
  } catch (e) {
    $("#pi-task-result").textContent = `状态读取失败：${e.message}`;
  }
}

$("#pi-submit-btn")?.addEventListener("click", piSubmitTask);
$("#pi-run-btn")?.addEventListener("click", piRunNext);
$("#pi-status-btn")?.addEventListener("click", piRefreshStatus);
