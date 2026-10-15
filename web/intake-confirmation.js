(() => {
  "use strict";
  // G1C material intake loop: paste/upload -> field mapping -> gaps ->
  // use confirmation -> current eligibility. Self-contained on purpose so
  // isolated browser acceptance can load the real markup/styles plus this
  // module without the legacy console entrypoint. Internal IDs are shown
  // for troubleshooting only; the operator picks batches and SKUs by name.
  const panel = document.querySelector("#intake-panel");
  if (!panel) return;
  const $ = (id) => document.getElementById(id);

  const DRAFT_KEY = "intake-declaration-draft";
  const ORIGINAL_KEY = "intake-original-draft";
  let busy = false;
  let importPreview = null;
  let batchStatus = null;
  let selectedBatchId = "";
  let confirmationPreview = null;
  let lastConfirmation = null;
  let idempotencyKey = "";

  function setText(id, html, tone) {
    const node = $(id);
    if (!node) return;
    node.innerHTML = html || "";
    node.dataset.tone = tone || "info";
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[ch]);
  }

  function syncControls() {
    const token = $("intake-operator-token")?.value?.trim() || "";
    $("intake-import-preview-btn").disabled = busy || !$("intake-supplier")?.value?.trim() ||
      (!$("intake-quote-text")?.value?.trim() && !$("intake-original-file")?.files?.length);
    $("intake-import-confirm-btn").disabled = busy || !importPreview;
    $("intake-batch-select").disabled = busy;
    $("intake-load-btn").disabled = busy || !selectedBatchId;
    $("intake-template-btn").disabled = busy || !batchStatus;
    $("intake-preview-btn").disabled = busy || !selectedBatchId || !token || !$("intake-declaration")?.value?.trim();
    $("intake-confirm-btn").disabled = busy || !confirmationPreview || !token;
    $("intake-eligibility-btn").disabled = busy || !selectedBatchId;
    $("intake-lock-btn").disabled = busy || !token;
    panel.setAttribute("aria-busy", String(busy));
  }

  function normalizeError(error, fallback) {
    if (error?.detail) return typeof error.detail === "string" ? error.detail : JSON.stringify(error.detail);
    return fallback;
  }

  async function request(path, { method = "GET", body, auth = false } = {}) {
    const headers = { "Content-Type": "application/json" };
    if (auth) {
      const token = $("intake-operator-token")?.value?.trim() || "";
      if (!token) throw { status: 401, detail: "请先填写操作者凭证" };
      headers.Authorization = `Bearer ${token}`;
    }
    const response = await fetch(path, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "omit", redirect: "error", mode: "same-origin", cache: "no-store",
    });
    let payload = null;
    try { payload = await response.json(); } catch (_) { /* non-JSON error body */ }
    if (!response.ok) {
      throw { status: response.status, detail: payload?.detail ?? response.status };
    }
    return payload;
  }

  function withBusy(action) {
    return async (...args) => {
      if (busy) return; // double-click / concurrent guard (A31)
      busy = true;
      syncControls();
      try {
        await action(...args);
      } catch (error) {
        const status = error?.status;
        const tone = status === 0 || status === undefined ? "error" : status === 409 ? "warning" : "error";
        setText("intake-confirm-result", `请求失败（${status ?? "网络"}）：${escapeHtml(normalizeError(error, "网络错误，已填内容未丢失"))}`, tone);
      } finally {
        busy = false;
        syncControls();
      }
    };
  }

  // ---------------------------------------------------------------- import

  async function originalPayload() {
    const file = $("intake-original-file")?.files?.[0];
    if (file) {
      const buffer = await file.arrayBuffer();
      let binary = "";
      const bytes = new Uint8Array(buffer);
      for (let index = 0; index < bytes.length; index += 1) binary += String.fromCharCode(bytes[index]);
      return { kind: file.name.toLowerCase().endsWith(".xlsx") ? "xlsx" : "csv", filename: file.name, content_b64: btoa(binary), text: "" };
    }
    const text = $("intake-quote-text")?.value || "";
    if (!text.trim()) throw { status: 400, detail: "请粘贴报价原件或选择原件文件" };
    const encoded = new TextEncoder().encode(text);
    let binary = "";
    for (let index = 0; index < encoded.length; index += 1) binary += String.fromCharCode(encoded[index]);
    return { kind: "pasted_text", filename: "", content_b64: btoa(binary), text };
  }

  async function importPreviewAction() {
    const supplier = $("intake-supplier")?.value?.trim();
    if (!supplier) throw { status: 400, detail: "供应商名称必填" };
    const payload = await originalPayload();
    const body = payload.kind === "pasted_text"
      ? { kind: "pasted_text", content: payload.text }
      : { kind: payload.kind, filename: payload.filename, content_b64: payload.content_b64 };
    importPreview = await request("/commerce/supplier-imports/preview", { method: "POST", body });
    sessionStorage.setItem(ORIGINAL_KEY, payload.text);
    const rows = importPreview.rows.map((row) => {
      const fields = row.fields || {};
      const price = fields.price || {};
      return `<tr><td>${row.row_number}</td><td>${escapeHtml(fields.sku)}</td>` +
        `<td>${escapeHtml(row.raw?.price ?? "")}</td><td>${escapeHtml(price.amount ?? price.state)}</td>` +
        `<td>${escapeHtml(row.status)}</td><td>${escapeHtml((row.issues || []).join("；") || "-")}</td></tr>`;
    }).join("");
    setText("intake-import-result",
      `<p>共 ${importPreview.summary.total} 行（ok ${importPreview.summary.ok} / 待确认 ${importPreview.summary.needs_confirmation} / 拒绝 ${importPreview.summary.rejected}）` +
      `｜内容摘要 <code>${escapeHtml(importPreview.content_sha256.slice(0, 12))}…</code></p>` +
      `<table class="commerce-table"><thead><tr><th>行</th><th>SKU</th><th>原值(单价)</th><th>标准化</th><th>状态</th><th>问题</th></tr></thead><tbody>${rows}</tbody></table>`,
      "info");
    syncControls();
  }

  async function importConfirmAction() {
    if (!importPreview) throw { status: 409, detail: "请先预览" };
    const result = await request(`/commerce/supplier-imports/${importPreview.preview_id}/confirm`, {
      method: "POST",
      body: {
        content_sha256: importPreview.content_sha256,
        revision: importPreview.revision,
        supplier: $("intake-supplier")?.value?.trim(),
        operator: "console",
      },
    });
    importPreview = null;
    selectedBatchId = result.batch.record_id;
    setText("intake-import-result",
      `批次已确认：<strong>${escapeHtml(result.batch.supplier)}</strong> · ${result.snapshots.length} 个 SKU · <span data-batch-id="${escapeHtml(result.batch.record_id)}">批次号 ${escapeHtml(result.batch.record_id)}</span>（排障用，后续步骤无需复制）`,
      "ok");
    await refreshBatches();
    $("intake-batch-select").value = selectedBatchId;
    await loadBatchAction();
  }

  // ---------------------------------------------------------------- batches

  async function refreshBatches() {
    const data = await request("/commerce/supplier-imports");
    const select = $("intake-batch-select");
    const previous = selectedBatchId || select.value;
    select.innerHTML = `<option value="">选择导入批次（供应商 · 时间 · 状态）</option>` +
      (data.batches || []).map((batch) =>
        `<option value="${escapeHtml(batch.record_id)}">${escapeHtml(batch.supplier)} · ${escapeHtml(batch.status)} · ${escapeHtml((batch.created_at || "").replace("T", " ").slice(0, 19))} · ${(batch.snapshot_ids || []).length} SKU</option>`
      ).join("");
    if (previous && (data.batches || []).some((batch) => batch.record_id === previous)) {
      select.value = previous;
      selectedBatchId = previous;
    } else {
      selectedBatchId = select.value || "";
    }
    if (!(data.batches || []).length) {
      setText("intake-batch-status", "暂无导入批次：先在上方粘贴报价并确认导入", "info");
    }
    syncControls();
  }

  async function loadBatchAction() {
    if (!selectedBatchId) throw { status: 400, detail: "请先选择批次" };
    batchStatus = await request(`/commerce/supplier-imports/${encodeURIComponent(selectedBatchId)}/confirmation`);
    location.hash = `intake=${encodeURIComponent(selectedBatchId)}`;
    const skuRows = (batchStatus.snapshots || []).map((row) =>
      `<tr><td>${escapeHtml(row.sku)}</td><td>${row.quote_eligible ? "✔ 有效" : "✘ " + escapeHtml((row.quote_reasons || []).join("；"))}</td>` +
      `<td>${escapeHtml((row.expires_at || "").replace("T", " ").slice(0, 19))}</td></tr>`
    ).join("");
    const confirmations = (batchStatus.confirmations || []).map((record) =>
      `<p>确认 <code>${escapeHtml(record.record_id)}</code> · ${escapeHtml((record.allowed_uses || []).join("/"))} · ` +
      `有效期至 ${escapeHtml((record.valid_until || "").replace("T", " ").slice(0, 19))} · 状态 ${escapeHtml(record.status)}` +
      `${record.status === "confirmed" ? ` <button type="button" class="text-btn" data-intake-revoke="${escapeHtml(record.record_id)}">撤销此确认</button>` : ""}</p>`
    ).join("") || "<p>该批次尚无用途确认</p>";
    setText("intake-batch-status",
      `<p>批次状态 <strong>${escapeHtml(batchStatus.status)}</strong>（供应商 ${escapeHtml(batchStatus.supplier)}）</p>` +
      `<table class="commerce-table"><thead><tr><th>SKU</th><th>报价资格</th><th>到期</th></tr></thead><tbody>${skuRows}</tbody></table>${confirmations}`,
      "info");
    bindRevokeButtons();
    syncControls();
  }

  function bindRevokeButtons() {
    panel.querySelectorAll("[data-intake-revoke]").forEach((button) => {
      button.addEventListener("click", withBusy(async () => {
        const confirmationId = button.dataset.intakeRevoke;
        await request(`/commerce/intake-confirmations/${encodeURIComponent(confirmationId)}/revoke`, {
          method: "POST", auth: true, body: { reason: "操作者在资料面板撤销该用途确认（浏览器操作）" },
        });
        await loadBatchAction();
      }));
    });
  }

  // ------------------------------------------------------------ declaration

  function declarationTemplate() {
    if (!batchStatus) throw { status: 409, detail: "请先加载批次状态" };
    const template = {
      batch_id: batchStatus.batch_id,
      content_sha256: batchStatus.content_sha256,
      supplier: batchStatus.supplier,
      source_kind: "supplier_original",
      origin_ref: "",
      supplier_identity_ref: "",
      use_permission_ref: "",
      local_review_permitted: true,
      external_llm_permitted: false,
      issued_at: "",
      received_at: "",
      valid_until: "",
      offers: {},
    };
    for (const row of batchStatus.snapshots || []) {
      template.offers[row.snapshot_id] = {
        currency: "CNY", price_unit: "件", quantity_unit: "件",
        freight_basis: "per_unit", freight_destination: "",
        tax_terms: "", delivery_terms: "", after_sales_terms: "",
        stock_observed_at: "", stock_valid_until: "", evidence_ref: "",
      };
    }
    return template;
  }

  async function templateAction() {
    const template = declarationTemplate();
    $("intake-declaration").value = JSON.stringify(template, null, 2);
    sessionStorage.setItem(DRAFT_KEY, $("intake-declaration").value);
    setText("intake-preview-result", "模板已生成：offers 键为快照 ID（与上方 SKU 表对应，无需手填）；请逐项填写核验到的真实值与时间", "info");
  }

  // ------------------------------------------------------- preview/confirm

  async function confirmationPreviewAction() {
    if (!selectedBatchId) throw { status: 400, detail: "请先选择批次" };
    let declaration;
    try {
      declaration = JSON.parse($("intake-declaration").value);
    } catch (_) {
      throw { status: 400, detail: "声明不是合法 JSON，请检查后重试（已填内容未丢失）" };
    }
    const payload = await originalPayload();
    confirmationPreview = await request(
      `/commerce/supplier-imports/${encodeURIComponent(selectedBatchId)}/confirmation/preview`,
      {
        method: "POST", auth: true,
        body: { filename: payload.filename || "pasted-quote.csv", content_b64: payload.content_b64, declaration },
      },
    );
    if (!idempotencyKey) {
      idempotencyKey = `ui-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
      $("intake-idempotency-key").value = idempotencyKey;
    }
    const confirmableSkus = (confirmationPreview.confirmable_snapshot_ids || [])
      .map((sid) => skuOf(sid)).filter(Boolean).join("、") || "（无）";
    const excluded = (confirmationPreview.excluded_snapshots || [])
      .map((row) => `<li>${escapeHtml(skuOf(row.snapshot_id) || row.snapshot_id)}：${escapeHtml((row.reasons || []).join("；"))}</li>`)
      .join("");
    const gaps = (confirmationPreview.gaps || [])
      .map((gap) => `<li>${escapeHtml(gap.code ?? "")}${gap.snapshot_id ? `（${escapeHtml(skuOf(gap.snapshot_id) || gap.snapshot_id)}）` : ""}</li>`)
      .join("");
    setText("intake-preview-result",
      `<p>可确认 SKU：<strong>${escapeHtml(confirmableSkus)}</strong> ｜ 评估状态 ${escapeHtml(confirmationPreview.assessment_status)}</p>` +
      (excluded ? `<p>排除：${excluded}</p>` : "") +
      (gaps ? `<p>待补缺口：${gaps}</p>` : "<p>无缺口</p>") +
      `<p>确认范围与期限：以上可确认 SKU，用途以勾选项为准，有效期不超过原报价截止；预览 30 分钟内有效</p>`,
      "info");
    syncControls();
  }

  function skuOf(snapshotId) {
    const row = (batchStatus?.snapshots || []).find((item) => item.snapshot_id === snapshotId);
    return row?.sku ?? "";
  }

  async function confirmAction() {
    if (!confirmationPreview) throw { status: 409, detail: "请先生成确认预览" };
    const uses = [];
    if ($("intake-use-local")?.checked) uses.push("local_analysis");
    if ($("intake-use-model")?.checked) uses.push("external_model");
    if ($("intake-use-business")?.checked) uses.push("business_use");
    if (!uses.length) throw { status: 400, detail: "请至少选择一种用途" };
    const requested = $("intake-valid-until")?.value?.trim();
    const validUntil = requested || (batchStatus?.snapshots || [])
      .map((row) => row.expires_at).filter(Boolean).sort()[0] || "";
    if (!validUntil) throw { status: 400, detail: "无法确定确认期限，请填写 ISO 有效期" };
    const note = `操作者经面板核对原件与声明：SKU ${(confirmationPreview.confirmable_snapshot_ids || []).map((sid) => skuOf(sid)).filter(Boolean).join("、")}`;
    const result = await request(`/commerce/supplier-imports/${encodeURIComponent(selectedBatchId)}/confirmation/confirm`, {
      method: "POST", auth: true,
      body: {
        preview_id: confirmationPreview.preview_id,
        preview_digest: confirmationPreview.digest,
        idempotency_key: idempotencyKey,
        allowed_uses: uses,
        valid_until: validUntil,
        account_scope_mode: "none",
        account_scope_accounts: [],
        verification_note: note,
      },
    });
    lastConfirmation = result.confirmation;
    confirmationPreview = null;
    sessionStorage.removeItem(DRAFT_KEY);
    setText("intake-confirm-result",
      `<p>${result.idempotent ? "幂等重放，返回既有确认" : "用途确认已保存"}：<code>${escapeHtml(result.confirmation.record_id)}</code></p>` +
      `<p>用途 ${escapeHtml((result.confirmation.allowed_uses || []).join("/"))} · 有效期至 ${escapeHtml((result.confirmation.valid_until || "").replace("T", " ").slice(0, 19))}（不超过原报价截止）</p>` +
      `<p>幂等键 <code>${escapeHtml(idempotencyKey)}</code>（请求超时重试时原样重用，不会产生第二条确认）</p>`,
      "ok");
    await loadBatchAction();
  }

  // ------------------------------------------------------------ eligibility

  async function eligibilityAction() {
    if (!batchStatus) throw { status: 409, detail: "请先加载批次状态" };
    const uses = [];
    if ($("intake-use-local")?.checked) uses.push("local_analysis");
    if ($("intake-use-model")?.checked) uses.push("external_model");
    if ($("intake-use-business")?.checked) uses.push("business_use");
    const targets = uses.length ? uses : ["local_analysis"];
    const rows = [];
    for (const row of batchStatus.snapshots || []) {
      for (const use of targets) {
        let outcome;
        try {
          outcome = await request(`/commerce/intake-confirmations/eligibility?snapshot_id=${encodeURIComponent(row.snapshot_id)}&use=${encodeURIComponent(use)}`);
        } catch (error) {
          outcome = { eligible: false, reasons: [normalizeError(error, "查询失败")] };
        }
        rows.push(`<tr><td>${escapeHtml(row.sku)}</td><td>${escapeHtml(use)}</td>` +
          `<td>${outcome.eligible ? "✔ 可用" : "✘ " + escapeHtml((outcome.reasons || []).join("；"))}</td>` +
          `<td>${escapeHtml((outcome.valid_until || "").replace("T", " ").slice(0, 19))}</td></tr>`);
      }
    }
    setText("intake-eligibility-result",
      `<table class="commerce-table"><thead><tr><th>SKU</th><th>用途</th><th>当前资格（实时重算）</th><th>到期</th></tr></thead><tbody>${rows.join("")}</tbody></table>`,
      "info");
  }

  // ---------------------------------------------------------------- wiring

  $("intake-batch-select")?.addEventListener("change", async () => {
    selectedBatchId = $("intake-batch-select").value || "";
    batchStatus = null;
    confirmationPreview = null;
    syncControls();
    if (selectedBatchId) await withBusy(loadBatchAction)();
  });
  $("intake-import-preview-btn")?.addEventListener("click", withBusy(importPreviewAction));
  $("intake-import-confirm-btn")?.addEventListener("click", withBusy(importConfirmAction));
  $("intake-load-btn")?.addEventListener("click", withBusy(loadBatchAction));
  $("intake-template-btn")?.addEventListener("click", withBusy(async () => {
    $("intake-template-btn").disabled = true;
    try { templateAction(); } finally { $("intake-template-btn").disabled = false; }
  }));
  $("intake-preview-btn")?.addEventListener("click", withBusy(confirmationPreviewAction));
  $("intake-confirm-btn")?.addEventListener("click", withBusy(confirmAction));
  $("intake-eligibility-btn")?.addEventListener("click", withBusy(eligibilityAction));
  $("intake-lock-btn")?.addEventListener("click", () => {
    $("intake-operator-token").value = "";
    setText("intake-confirm-result", "已清除本页操作者凭证", "info");
    syncControls();
  });
  ["intake-supplier", "intake-quote-text", "intake-operator-token", "intake-declaration"].forEach((id) => {
    $(id)?.addEventListener("input", syncControls);
  });
  $("intake-declaration")?.addEventListener("input", () => {
    sessionStorage.setItem(DRAFT_KEY, $("intake-declaration").value);
  });
  $("intake-original-file")?.addEventListener("change", syncControls);

  // restore draft + selected batch across reloads (A30/A32)
  const draft = sessionStorage.getItem(DRAFT_KEY);
  if (draft && !$("intake-declaration")?.value) $("intake-declaration").value = draft;
  const hashMatch = /^#intake=([^&]+)$/.exec(location.hash || "");
  (async () => {
    await withBusy(refreshBatches)();
    if (hashMatch) {
      const wanted = decodeURIComponent(hashMatch[1]);
      if (selectedBatchId !== wanted) {
        $("intake-batch-select").value = wanted;
        selectedBatchId = wanted;
        syncControls();
      }
      if (selectedBatchId) await withBusy(loadBatchAction)();
    }
  })();
})();
