const state = {
  health: null,
  guard: null,
  memory: null,
  collaboration: { messages: [], discussions: [] },
  workOrders: [],
  executionJobs: [],
  messageIntake: { metrics: {}, health: {}, recent: [], runbooks: [] },
  resaleCandidates: [],
  lastResult: {},
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
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(data.detail || response.statusText);
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
  target.innerHTML = [
    `<span class="ops-count ops-count--pending">待审批 ${pending}</span>`,
    `<span class="ops-count ops-count--approved">已批准 ${approved}</span>`,
    `<span class="ops-count ops-count--queued">待执行 ${queued}</span>`,
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
    const actions = [
      job.status === "failed" ? `<button class="text-btn" onclick="retryJob('${job.job_id}')">重试</button>` : "",
      job.status === "queued" ? `<button class="text-btn" onclick="cancelJob('${job.job_id}')">取消</button>` : "",
    ].filter(Boolean).join("");
    return `
      <div class="execution-job-item execution-job-item--${escapeHtml(job.status || "unknown")}">
        <div class="execution-job-title">
          <span>${escapeHtml(job.action || "-")}</span>
          <span class="approval-badge approval-badge--exec-${escapeHtml(job.status || "unknown")}">${escapeHtml(job.status || "-")}</span>
        </div>
        <div class="collab-meta">
          job: ${escapeHtml(job.job_id || "-")} · approval: ${escapeHtml(job.approval_id || "-")} · attempts: ${job.attempts ?? 0}/${job.max_attempts ?? "-"}
        </div>
        ${error || result}
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

function renderMessageIntakeFeed(records = [], metrics = {}, health = {}, runbooks = []) {
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

async function refreshMessageIntake() {
  const [metrics, health, recent, runbookData] = await Promise.all([
    api("/message-intake/metrics"),
    api("/message-intake/health"),
    api("/message-intake/recent?limit=12"),
    api("/message-intake/runbooks"),
  ]);
  const runbooks = runbookData.runbooks || [];
  state.messageIntake = { metrics, health, recent, runbooks };
  renderMessageIntakeFeed(recent, metrics, health, runbooks);
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
    $("#send-btn").textContent = "发送给OpenClaw";
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
$("#prune-message-intake")?.addEventListener("click", pruneMessageIntake);
$("#export-message-intake-bundle")?.addEventListener("click", exportMessageIntakeBundle);
$("#run-message-intake-dry-run")?.addEventListener("click", runMessageIntakeDryRun);
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

    const canListing = !isBlocked && c.supplier_verified && c.manual_decision === "approve" && !c.listing_draft_id;
    const missingReasons = [];
    if (isBlocked) missingReasons.push("已阻断");
    if (!c.supplier_verified) missingReasons.push("未验证供应商");
    if (c.manual_decision !== "approve") missingReasons.push(c.manual_decision ? "决策: " + decisionLabel(c.manual_decision) : "未决策");
    if (c.listing_draft_id) missingReasons.push("已生成草稿");

    const draftStatusHtml = c.listing_draft_id
      ? `<div class="resale-draft-status"><span class="resale-badge resale-badge--draft">草稿: ${escapeHtml(c.listing_draft_id)}</span><span class="snapshot-field">需审批后发布</span></div>`
      : "";

    const actions = isBlocked
      ? `<div class="resale-actions"><span style="color:var(--danger);font-size:11px">已阻断，无法创建计划</span></div>`
      : `<div class="resale-actions">
          <button class="resale-btn" onclick="createResalePlan('${c.candidate_id}')">创建倒卖计划</button>
          ${canListing
            ? `<button class="resale-btn" onclick="createListingDraft('${c.candidate_id}')">生成上架草稿</button>`
            : `<span class="resale-missing" style="color:var(--muted);font-size:11px">上架条件不足: ${escapeHtml(missingReasons.join(", "))}</span>`}
          <button class="resale-btn resale-btn--secondary" onclick="openVerifyDialog('${c.candidate_id}')">记录验证</button>
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
    supplier_price: parseFloat(fd.get("supplier_price")) || 0,
    supplier_shipping: parseFloat(fd.get("supplier_shipping")) || 0,
    target_sale_price: parseFloat(fd.get("target_sale_price")) || 0,
    platform_fee_rate: parseFloat(fd.get("platform_fee_rate")) || 0.08,
    expected_refund_rate: parseFloat(fd.get("expected_refund_rate")) || 0.02,
    specifications,
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
  const verified = confirm("供应商已验证？点击确定标记为已验证，取消标记为未验证。");
  const note = prompt("验证备注（可留空）:", "");
  const linksRaw = prompt("竞品链接（逗号分隔，可留空）:", "");
  const priceMinRaw = prompt("竞品最低价（可留空）:", "");
  const priceMaxRaw = prompt("竞品最高价（可留空）:", "");
  const demand = prompt("需求信号（如: 月销200+，可留空）:", "");
  const payload = {
    supplier_verified: verified,
    supplier_verification_note: note || "",
    competitor_links: linksRaw ? linksRaw.split(",").map((s) => s.trim()).filter(Boolean) : [],
    competitor_price_min: priceMinRaw ? parseFloat(priceMinRaw) : null,
    competitor_price_max: priceMaxRaw ? parseFloat(priceMaxRaw) : null,
    estimated_demand_signal: demand || "",
  };
  try {
    const result = await api(`/resale/candidates/${candidateId}/verify`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderResult({ type: "resale_verification", result: result.candidate || result });
    await refreshResaleCandidates();
  } catch (error) {
    renderResult({ type: "error", result: { message: error.message } });
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

async function createListingDraft(candidateId) {
  if (!confirm("确认生成上架草稿？将创建审批单，需人工批准后才会发布。")) return;
  try {
    const result = await api(`/resale/candidates/${candidateId}/listing-draft`, {
      method: "POST",
      body: "{}",
    });
    renderResult({ type: "resale_listing_draft", result });
    await refreshResaleCandidates();
    await refreshWorkOrders();
  } catch (error) {
    alert("生成失败: " + error.message);
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
$("#resale-filter-scope")?.addEventListener("change", refreshResaleCandidates);
$("#resale-filter-source")?.addEventListener("change", refreshResaleCandidates);
$("#resale-filter-status")?.addEventListener("change", refreshResaleCandidates);

// Load resale candidates and dashboard on startup
refreshResaleCandidates().catch(() => {});
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
    await api(`/resale/orders/${orderId}/status`, {
      method: "POST",
      body: JSON.stringify({ status: newStatus }),
    });
    await refreshOrders();
  } catch (error) {
    alert("更新失败: " + error.message);
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
async function refreshOPCDashboard() {
  try {
    const data = await api("/dashboard/opc-daily");
    const el = $("#opc-dashboard-content");
    if (!el) return;

    const actions = data.today_actions || {};
    const funnel = data.funnel || {};
    const bottleneck = data.bottleneck || {};
    const categories = data.category_performance || [];
    const notes = data.source_notes || [];

    let html = '<div class="opc-section">';
    html += '<h4>今日待办</h4>';
    html += '<div class="approval-count-badges">';
    html += `<span class="badge">待审批 ${actions.pending_approvals || 0}</span>`;
    html += `<span class="badge">待执行 ${actions.pending_execution || 0}</span>`;
    html += `<span class="badge">待回复 ${actions.pending_reply_drafts || 0}</span>`;
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

$("#refresh-opc-dashboard")?.addEventListener("click", refreshOPCDashboard);
$("#run-weekly-review")?.addEventListener("click", runWeeklyReview);

// Load OPC dashboard on startup
refreshOPCDashboard().catch(() => {});
