(() => {
  "use strict";
  const panel = document.querySelector("#pi-usage-panel");
  if (!panel) return;
  const element = (name) => panel.querySelector(`#pi-usage-${name}`);
  let credential = "";
  let ready = false;
  let busy = false;
  let generation = 0;
  let activeRequest = null;
  let selected = null;
  let preview = null;
  let pendingRequest = null;

  function status(message, tone = "info") {
    element("status").textContent = message;
    element("status").dataset.tone = tone;
  }

  function syncControls() {
    element("token").disabled = busy || ready;
    element("unlock").disabled = busy || ready;
    element("lock").disabled = !credential;
    element("refresh").disabled = busy || !ready || Boolean(pendingRequest);
    element("fields").disabled = busy || !ready || !selected || Boolean(pendingRequest);
    element("confirm").disabled = busy || !ready || !preview || Boolean(pendingRequest);
    element("commit").disabled = busy || !ready || (!pendingRequest && (!preview || !element("confirm").checked));
    element("commit").textContent = pendingRequest ? "以原请求重试 / 取回回执" : "3. 确认并保存回执";
    panel.querySelectorAll("[data-usage-select]").forEach((button) => {
      button.disabled = busy || !ready || Boolean(pendingRequest);
    });
    panel.setAttribute("aria-busy", String(busy));
  }

  function invalidatePreview() {
    preview = null;
    element("confirm").checked = false;
    element("preview").hidden = true;
    element("diff").replaceChildren();
    element("proof").textContent = "";
    syncControls();
  }

  function lock() {
    generation += 1;
    activeRequest?.abort();
    credential = "";
    ready = false;
    busy = false;
    element("token").value = "";
    if (!pendingRequest) {
      selected = null;
      element("proposal").reset();
      element("tokens").readOnly = false;
      element("entries").replaceChildren();
      element("count").textContent = "尚未读取";
      element("selected").textContent = "尚未选择记录";
      element("receipt").textContent = "";
      element("receipt-panel").hidden = true;
      invalidatePreview();
    }
    status(pendingRequest
      ? "凭证已清除；提交结果仍不明，原请求仅保留在本页内存。用同一操作者凭证解锁后原样重试，请勿刷新或关闭页面。"
      : "已锁定并清除凭证与面板数据。", pendingRequest ? "warning" : "info");
    syncControls();
  }

  async function request(action, body) {
    const controller = new AbortController();
    activeRequest = controller;
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(`/commerce/pi-usage/${action}`, {
        method: body === undefined ? "GET" : "POST",
        headers: { Authorization: `Bearer ${credential}`, "Content-Type": "application/json" },
        body, signal: controller.signal, credentials: "omit", redirect: "error",
        mode: "same-origin", cache: "no-store", referrerPolicy: "no-referrer",
      });
      if (!response.ok) {
        const messages = {
          401: "操作者凭证无效，请重新解锁。",
          403: "该身份不允许对账；任务会话不可使用此功能。",
          404: "记录不存在，请刷新并人工排查孤立账本。",
          409: "状态、证据或幂等请求冲突，或任务仍在执行。请核对账本并重新预览，不能强制覆盖。",
          422: "字段或确认信息不符合要求，请检查证据并重新预览。",
          503: "服务端未启用对账或账本存储不可用。",
        };
        const error = new Error(messages[response.status] || "服务返回异常，请保留原请求核对。");
        error.status = response.status;
        throw error;
      }
      return await response.json();
    } finally {
      clearTimeout(timer);
      if (activeRequest === controller) activeRequest = null;
    }
  }

  function renderEntries(data) {
    if (!Array.isArray(data.entries)) throw new Error("未知记录响应格式异常。");
    element("entries").replaceChildren();
    element("count").textContent = `${data.entries.length} 条未知消费（未逐项对账前继续阻断）`;
    for (const entry of data.entries) {
      const row = document.createElement("tr");
      for (const value of [entry.business_day, `${entry.job_id} / attempt ${entry.attempt ?? "缺失"}`, entry.status]) {
        const cell = document.createElement("td");
        cell.textContent = value ?? "未知";
        row.append(cell);
      }
      const action = document.createElement("td");
      if (entry.reconcilable === true) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "text-btn";
        button.dataset.usageSelect = "true";
        button.textContent = "选择核对";
        button.addEventListener("click", () => {
          if (busy || pendingRequest || !ready) return;
          selected = entry;
          element("proposal").reset();
          element("tokens").readOnly = false;
          element("selected").textContent = `任务 ${entry.job_id} · attempt ${entry.attempt} · 原业务日 ${entry.business_day}`;
          invalidatePreview();
          status("请独立核验证据，再填写真实消耗。未知不等于零。");
        });
        action.append(button);
      } else {
        action.textContent = "不可对账：需排查缺失尝试、孤立记录或活跃任务";
      }
      row.append(action);
      element("entries").append(row);
    }
  }

  async function loadEntries(unlock = false) {
    if (busy || (!unlock && (!ready || pendingRequest))) return;
    if (unlock) {
      if (!element("auth").reportValidity()) return;
      credential = element("token").value;
      element("token").value = "";
    }
    const currentGeneration = generation;
    busy = true;
    syncControls();
    try {
      const data = await request("unresolved");
      if (currentGeneration !== generation) return;
      renderEntries(data);
      ready = true;
      if (!pendingRequest) {
        selected = null;
        element("selected").textContent = "请从最新列表选择记录";
        invalidatePreview();
      }
      status(pendingRequest ? "已解锁。提交结果尚待核对，请以原请求重试取回回执。"
        : "已读取。未收到持久化回执前，不代表对账成功。", pendingRequest ? "warning" : "info");
    } catch (error) {
      if (currentGeneration !== generation) return;
      if (unlock || [401, 403].includes(error.status)) {
        credential = "";
        ready = false;
      }
      status(error.message, "error");
    } finally {
      if (currentGeneration === generation) { busy = false; syncControls(); }
    }
  }

  function buildProposal() {
    const tokens = Number(element("tokens").value);
    if (!Number.isSafeInteger(tokens) || tokens < 0 || element("tokens").value.trim() === "") {
      throw new Error("total_tokens 必须是非负安全整数，未知值不可填零替代。");
    }
    const resolution = element("resolution").value;
    const reason = element("reason").value.trim();
    const reference = element("reference").value.trim();
    const statement = element("statement").value.trim();
    if (reason.length < 10 || reference.length < 3 || statement.length < 10) {
      throw new Error("证据引用、理由或说明过短；空格不能代替核验依据。");
    }
    return {
      job_id: selected.job_id, attempt: selected.attempt, resolution, total_tokens: tokens,
      idempotency_key: `ui-${crypto.randomUUID()}`, reason,
      evidence: {
        kind: resolution === "settle" ? "provider_usage" : "dispatch_not_sent",
        reference, job_id: selected.job_id, attempt: selected.attempt, total_tokens: tokens, statement,
      },
    };
  }

  async function previewProposal(event) {
    event.preventDefault();
    if (busy || !ready || !selected || pendingRequest || !element("proposal").reportValidity()) return;
    invalidatePreview();
    const currentGeneration = generation;
    busy = true;
    syncControls();
    try {
      const proposal = buildProposal();
      const result = await request("preview", JSON.stringify(proposal));
      if (currentGeneration !== generation) return;
      if (!/^[0-9a-f]{64}$/.test(result.expected_state) || !/^[0-9a-f]{64}$/.test(result.approval_digest)
          || !result.before || !result.after || !result.operator) throw new Error("预览响应不完整，禁止确认。");
      preview = { proposal, result };
      for (const [title, record] of [["变更前", result.before], ["变更后", result.after]]) {
        const column = document.createElement("div");
        column.textContent = `${title}\n状态：${record.status}\n原业务日：${record.business_day}\ntotal_tokens：${record.total_tokens ?? "未知（阻断）"}`;
        element("diff").append(column);
      }
      element("proof").textContent = JSON.stringify({
        operator: result.operator, idempotency_key: proposal.idempotency_key,
        evidence_sha256: result.evidence_sha256, expected_state: result.expected_state,
        approval_digest: result.approval_digest, evidence: proposal.evidence, reason: proposal.reason,
      }, null, 2);
      element("preview").hidden = false;
      status("仅生成只读预览，尚未记账。核对服务端操作者身份及差异后，勾选确认。", "warning");
    } catch (error) {
      if (currentGeneration !== generation) return;
      if ([401, 403].includes(error.status)) { credential = ""; ready = false; }
      status(error.message, "error");
    } finally {
      if (currentGeneration === generation) { busy = false; syncControls(); }
    }
  }

  async function commit() {
    if (busy || !ready) return;
    if (!pendingRequest) {
      if (!preview || !element("confirm").checked) return;
      pendingRequest = JSON.stringify({ ...preview.proposal,
        expected_state: preview.result.expected_state, approval_digest: preview.result.approval_digest, confirmed: true });
    }
    const currentGeneration = generation;
    busy = true;
    syncControls();
    status("提交中。请勿关闭页面；超时不代表失败，原请求将保留用于核对。", "warning");
    try {
      const result = await request("reconcile", pendingRequest);
      if (currentGeneration !== generation) return;
      const sent = JSON.parse(pendingRequest);
      const receipt = result.receipt;
      if (!receipt?.reconciliation_id || receipt.idempotency_key !== sent.idempotency_key
          || receipt.job_id !== sent.job_id || receipt.attempt !== sent.attempt
          || receipt.total_tokens !== sent.total_tokens || receipt.resolution !== sent.resolution
          || receipt.approval_digest !== sent.approval_digest) throw new Error("回执与请求不匹配。");
      element("receipt").textContent = JSON.stringify(result, null, 2);
      element("receipt-panel").hidden = false;
      pendingRequest = null;
      selected = null;
      preview = null;
      element("confirm").checked = false;
      element("entries").replaceChildren();
      element("count").textContent = "请刷新列表核对剩余未知记录";
      status(`${result.idempotent ? "原回执已取回（未重复记账）" : "已保存对账回执"}。任务未重启；其他未知消费和预算限制继续生效。`);
    } catch (error) {
      if (currentGeneration !== generation) return;
      if ([404, 409, 422].includes(error.status)) {
        pendingRequest = null;
        invalidatePreview();
        status(error.message, "error");
      } else {
        if ([401, 403].includes(error.status)) { credential = ""; ready = false; }
        status(`${error.status ? error.message + " " : ""}结果不明，不能当作失败或成功。原请求已冻结；请保留页面，必要时用同一操作者凭证解锁，再原样重试取回回执。`, "warning");
      }
    } finally {
      if (currentGeneration === generation) { busy = false; syncControls(); }
    }
  }

  element("auth").addEventListener("submit", (event) => { event.preventDefault(); loadEntries(true); });
  element("lock").addEventListener("click", lock);
  element("refresh").addEventListener("click", () => loadEntries());
  element("proposal").addEventListener("submit", previewProposal);
  element("fields").addEventListener("input", () => {
    if (pendingRequest || busy) return;
    const releasing = element("resolution").value === "release";
    element("tokens").readOnly = releasing;
    if (releasing) element("tokens").value = "0";
    invalidatePreview();
  });
  element("resolution").addEventListener("change", () => {
    if (pendingRequest || busy) return;
    element("tokens").value = element("resolution").value === "release" ? "0" : "";
    element("tokens").readOnly = element("resolution").value === "release";
    invalidatePreview();
  });
  element("confirm").addEventListener("change", syncControls);
  element("commit").addEventListener("click", commit);
  window.addEventListener("beforeunload", (event) => {
    if (pendingRequest) { event.preventDefault(); event.returnValue = ""; }
  });
  window.addEventListener("pagehide", () => {
    pendingRequest = null;
    lock();
  });
  syncControls();
})();
