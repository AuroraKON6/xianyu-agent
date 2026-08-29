import { Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const DEFAULT_API_BASE = "http://127.0.0.1:8788";
const MAX_TOOL_TEXT = 30000;

function apiBase(): string {
  const raw = (process.env.XIANYU_API_BASE_URL || DEFAULT_API_BASE).trim();
  const parsed = new URL(raw);
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error(`Unsupported Xianyu API protocol: ${parsed.protocol}`);
  }
  return parsed.toString().replace(/\/$/, "");
}

function compactJson(value: unknown): string {
  const text = JSON.stringify(value, null, 2);
  if (text.length <= MAX_TOOL_TEXT) return text;
  return `${text.slice(0, MAX_TOOL_TEXT)}\n... truncated by xianyu-ops`;
}

function errorText(value: unknown): string {
  if (typeof value === "string") return value.slice(0, 1000);
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return compactJson(record.detail ?? record.error ?? record).slice(0, 1000);
  }
  return String(value).slice(0, 1000);
}

async function apiRequest(
  path: string,
  init: RequestInit = {},
  signal?: AbortSignal,
): Promise<any> {
  const timeout = AbortSignal.timeout(20000);
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
  const response = await fetch(`${apiBase()}${path}`, {
    ...init,
    signal: combined,
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers || {}),
    },
  });
  const raw = await response.text();
  let data: unknown = raw;
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch {
      data = raw;
    }
  }
  if (!response.ok) {
    throw new Error(`Xianyu API ${response.status}: ${errorText(data)}`);
  }
  return data;
}

function result(value: unknown, details: Record<string, unknown> = {}) {
  return {
    content: [{ type: "text" as const, text: compactJson(value) }],
    details,
  };
}

function isUnresolved(status: unknown): boolean {
  return !["completed", "rejected", "cancelled", "quarantined"].includes(
    String(status || "").toLowerCase(),
  );
}

function finiteNumber(value: unknown): number | null {
  const parsed = Number(String(value ?? "").replace(/[^0-9.-]/g, ""));
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function priceSummary(items: Array<Record<string, unknown>>) {
  const prices = items
    .map((item) => finiteNumber(item.price))
    .filter((value): value is number => value !== null)
    .sort((a, b) => a - b);
  if (!prices.length) return { observed_count: 0, min: null, median: null, max: null };
  const middle = Math.floor(prices.length / 2);
  const median = prices.length % 2
    ? prices[middle]
    : (prices[middle - 1] + prices[middle]) / 2;
  return {
    observed_count: prices.length,
    min: prices[0],
    median: Number(median.toFixed(2)),
    max: prices[prices.length - 1],
  };
}

export default function xianyuOps(pi: ExtensionAPI) {
  pi.registerTool({
    name: "xianyu_business_snapshot",
    label: "Xianyu Business Snapshot",
    description:
      "Read the current Xianyu operating state, counts, first-run readiness and next actions. Use first for status and planning questions.",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, signal) {
      const [health, accounts, candidates, trials, workOrders, dashboard] = await Promise.all([
        apiRequest("/health", {}, signal),
        apiRequest("/accounts", {}, signal),
        apiRequest("/resale/candidates", {}, signal),
        apiRequest("/commercial-trials/active", {}, signal),
        apiRequest("/work-orders?limit=100", {}, signal),
        apiRequest("/dashboard/opc-daily", {}, signal),
      ]);
      const domesticCandidates = (Array.isArray(candidates) ? candidates : []).filter(
        (item: any) =>
          String(item?.sales_platform || "xianyu").toLowerCase() === "xianyu" &&
          String(item?.market_scope || "domestic").toLowerCase() === "domestic",
      );
      const unresolved = (Array.isArray(workOrders) ? workOrders : []).filter((item: any) =>
        isUnresolved(item?.status),
      );
      const snapshot = {
        runtime: {
          ok: Boolean(health?.ok),
          orchestrator: health?.orchestrator || "pi",
          adapter: health?.adapter || "unknown",
          model: health?.model || "unknown",
        },
        counts: {
          seller_accounts: Array.isArray(accounts) ? accounts.length : 0,
          domestic_xianyu_candidates: domesticCandidates.length,
          active_trials: Number(trials?.count || 0),
          unresolved_work_orders: unresolved.length,
        },
        candidate_summary: domesticCandidates.slice(0, 20).map((item: any) => ({
          candidate_id: item.candidate_id || item.id,
          title: item.title,
          category: item.category,
          status: item.status,
          manual_decision: item.manual_decision,
          recommendation: item.scoring?.recommendation,
          gross_profit: item.economics?.gross_profit,
          gross_margin: item.economics?.gross_margin,
        })),
        first_run: dashboard?.first_run || dashboard?.first_run_readiness || null,
        next_actions: dashboard?.next_actions || [],
        generated_at: new Date().toISOString(),
      };
      return result(snapshot, snapshot.counts);
    },
  });

  pi.registerTool({
    name: "xianyu_market_search",
    label: "Xianyu Market Search",
    description:
      "Read current public Xianyu search results for one exact product phrase. Returns observable listing fields only and never publishes, messages, approves, or mutates business records.",
    parameters: Type.Object({
      keyword: Type.String({ minLength: 1, maxLength: 80, description: "Exact product phrase to search" }),
      max_pages: Type.Optional(Type.Number({ minimum: 1, maximum: 2, description: "Pages to inspect; default 1" })),
      min_price: Type.Optional(Type.Number({ minimum: 0, description: "Optional displayed-price floor" })),
      max_price: Type.Optional(Type.Number({ minimum: 0, description: "Optional displayed-price ceiling" })),
    }),
    async execute(_toolCallId, params, signal) {
      const keyword = String(params.keyword || "").trim();
      if (!keyword) throw new Error("keyword is required");
      const maxPages = Math.max(1, Math.min(2, Math.trunc(params.max_pages || 1)));
      const minPrice = Math.max(0, Number(params.min_price || 0));
      const maxPrice = Math.max(0, Number(params.max_price || 0));
      if (maxPrice > 0 && minPrice > maxPrice) {
        throw new Error("min_price cannot exceed max_price");
      }
      const data = await apiRequest(
        "/browser/search",
        {
          method: "POST",
          body: JSON.stringify({
            keyword,
            max_pages: maxPages,
            min_price: minPrice,
            max_price: maxPrice,
            cookie_id: "default",
          }),
        },
        signal,
      );
      if (!data?.ok) throw new Error(errorText(data?.error || "Xianyu search failed"));
      const items = (Array.isArray(data?.items) ? data.items : []).slice(0, 60).map((item: any) => ({
        product_id: String(item?.product_id || ""),
        title: String(item?.title || "").slice(0, 1200),
        displayed_price: finiteNumber(item?.price),
        location: String(item?.location || "").slice(0, 80),
        seller_name: String(item?.seller_name || "").slice(0, 120),
        image_url: String(item?.image_url || "").slice(0, 1000),
        metrics_available: Boolean(item?.metrics_available),
      }));
      const payload = {
        keyword,
        inspected_pages: maxPages,
        count: items.length,
        displayed_price_summary: priceSummary(
          items.map((item) => ({ price: item.displayed_price })),
        ),
        evidence_limits: [
          "Displayed price may be a lead price rather than the final SKU price.",
          "Search presence does not prove sales, conversion, stock, or seller reliability.",
          "Missing engagement metrics must remain unknown.",
        ],
        items,
        observed_at: new Date().toISOString(),
      };
      return result(payload, { count: items.length, external_action: false });
    },
  });

  pi.registerTool({
    name: "xianyu_candidates",
    label: "Xianyu Candidates",
    description: "List domestic Xianyu candidates with evidence, economics and scoring. Never returns cross-border candidates.",
    parameters: Type.Object({
      status: Type.Optional(Type.String({ description: "Optional candidate status filter" })),
      limit: Type.Optional(Type.Number({ minimum: 1, maximum: 100, description: "Maximum records; default 20" })),
    }),
    async execute(_toolCallId, params, signal) {
      const query = new URLSearchParams({ market_scope: "domestic" });
      if (params.status) query.set("status", params.status);
      const data = await apiRequest(`/resale/candidates?${query}`, {}, signal);
      const limit = Math.max(1, Math.min(100, Math.trunc(params.limit || 20)));
      const candidates = (Array.isArray(data) ? data : [])
        .filter((item: any) => String(item?.sales_platform || "xianyu").toLowerCase() === "xianyu")
        .slice(0, limit);
      return result({ count: candidates.length, candidates }, { count: candidates.length });
    },
  });

  pi.registerTool({
    name: "xianyu_candidate_activation_packet",
    label: "Xianyu Candidate Activation Packet",
    description: "Read the ordered, account-independent evidence gates for one exact Xianyu launch candidate. This is read-only.",
    parameters: Type.Object({
      candidate_id: Type.String({ minLength: 1, description: "Exact candidate id" }),
    }),
    async execute(_toolCallId, params, signal) {
      const data = await apiRequest(
        `/resale/candidates/${encodeURIComponent(params.candidate_id)}/activation-packet`,
        {},
        signal,
      );
      return result(data, {
        candidate_id: params.candidate_id,
        ready: Boolean(data?.ready),
        evidence_ready: Boolean(data?.evidence_ready),
        failed_gate_codes: data?.failed_gate_codes || [],
        external_action: false,
      });
    },
  });

  pi.registerTool({
    name: "xianyu_candidate_evidence",
    label: "Xianyu Candidate Evidence Archive",
    description: "Read candidate-bound evidence metadata, immutable revision state and file-integrity results. It never returns file content or local paths.",
    parameters: Type.Object({
      candidate_id: Type.String({ minLength: 1, description: "Exact candidate id" }),
    }),
    async execute(_toolCallId, params, signal) {
      const data = await apiRequest(
        `/resale/candidates/${encodeURIComponent(params.candidate_id)}/evidence-artifacts`,
        {},
        signal,
      );
      const artifacts = (Array.isArray(data?.artifacts) ? data.artifacts : []).map((item: any) => ({
        artifact_id: item.artifact_id,
        kind: item.kind,
        original_filename: item.original_filename,
        mime_type: item.mime_type,
        byte_size: item.byte_size,
        sha256: item.sha256,
        observed_at: item.observed_at,
        status: item.status,
        supersedes_artifact_id: item.supersedes_artifact_id || "",
        integrity: item.integrity || { ok: false, status: "missing" },
      }));
      return result(
        {
          candidate_id: params.candidate_id,
          required_kinds: data?.required_kinds || [],
          count: artifacts.length,
          artifacts,
        },
        {
          candidate_id: params.candidate_id,
          count: artifacts.length,
          active_verified: artifacts.filter(
            (item: any) => item.status === "active" && item.integrity?.ok,
          ).length,
          external_action: false,
        },
      );
    },
  });

  pi.registerTool({
    name: "xianyu_work_orders",
    label: "Xianyu Work Orders",
    description: "List recent local WorkOrders and approval state. This is read-only and does not decide or execute anything.",
    parameters: Type.Object({
      limit: Type.Optional(Type.Number({ minimum: 1, maximum: 100, description: "Maximum records; default 30" })),
    }),
    async execute(_toolCallId, params, signal) {
      const limit = Math.max(1, Math.min(100, Math.trunc(params.limit || 30)));
      const data = await apiRequest(`/work-orders?limit=${limit}`, {}, signal);
      const items = Array.isArray(data) ? data : [];
      const compact = items.map((item: any) => ({
        work_order_id: item.work_order_id,
        kind: item.kind,
        title: item.title,
        owner: item.owner,
        status: item.status,
        risk_level: item.risk_level,
        cookie_id: item.context?.cookie_id || "",
        pending_approvals: Array.isArray(item.approvals)
          ? item.approvals.filter((approval: any) => approval?.status === "pending").length
          : 0,
        created_at: item.created_at,
      }));
      return result({ count: compact.length, work_orders: compact }, { count: compact.length });
    },
  });

  pi.registerTool({
    name: "xianyu_commercial_terms",
    label: "Xianyu Commercial Terms",
    description: "Read the current sanitized account-and-category commercial terms assessment and fee-version match.",
    parameters: Type.Object({
      cookie_id: Type.String({ minLength: 1, description: "Exact seller account id" }),
      category: Type.String({ minLength: 1, description: "Exact Xianyu publish category" }),
      candidate_id: Type.Optional(Type.String({ description: "Candidate to verify against the terms version" })),
    }),
    async execute(_toolCallId, params, signal) {
      const query = new URLSearchParams({ category: params.category });
      if (params.candidate_id) query.set("candidate_id", params.candidate_id);
      const data = await apiRequest(
        `/accounts/${encodeURIComponent(params.cookie_id)}/commercial-terms?${query}`,
        {},
        signal,
      );
      return result(data, { ready: Boolean(data?.assessment?.ready) });
    },
  });

  pi.registerTool({
    name: "xianyu_publish_preflight",
    label: "Xianyu Publish Preflight",
    description: "Run the read-only publish evidence gates for one exact account and domestic Xianyu candidate.",
    parameters: Type.Object({
      cookie_id: Type.String({ minLength: 1, description: "Exact seller account id" }),
      candidate_id: Type.String({ minLength: 1, description: "Exact candidate id" }),
    }),
    async execute(_toolCallId, params, signal) {
      const query = new URLSearchParams({ cookie_id: params.cookie_id });
      const data = await apiRequest(
        `/resale/candidates/${encodeURIComponent(params.candidate_id)}/publish-preflight?${query}`,
        {},
        signal,
      );
      return result(data, {
        ready: Boolean(data?.ready),
        failed_gate_codes: data?.failed_gate_codes || [],
      });
    },
  });

  pi.registerTool({
    name: "xianyu_commercial_launch_preview",
    label: "Xianyu Commercial Launch Preview",
    description: "Read-only 20-gate Commercial Alpha preview for exactly one account and one domestic Xianyu candidate.",
    parameters: Type.Object({
      cookie_id: Type.String({ description: "Exact seller account id; may be empty to inspect blockers" }),
      candidate_id: Type.String({ description: "Exact candidate id; may be empty to inspect blockers" }),
      controls: Type.Optional(
        Type.Object({
          cash_budget: Type.Optional(Type.Number({ minimum: 0 })),
          max_realized_loss: Type.Optional(Type.Number({ minimum: 0 })),
          max_after_sales_rate: Type.Optional(Type.Number({ minimum: 0, maximum: 1 })),
          target_inquiries: Type.Optional(Type.Number({ minimum: 1 })),
          observation_days: Type.Optional(Type.Number({ minimum: 1 })),
        }),
      ),
    }),
    async execute(_toolCallId, params, signal) {
      const data = await apiRequest(
        "/commercial-launch/preview",
        { method: "POST", body: JSON.stringify(params) },
        signal,
      );
      return result(data, {
        ready: Boolean(data?.ready),
        failed_gate_codes: data?.failed_gate_codes || [],
        preview_token: data?.preview_token || "",
      });
    },
  });

  pi.registerTool({
    name: "xianyu_review_candidate",
    label: "Review Xianyu Candidate",
    description: "Create an internal specialist-agent review for a candidate. It does not approve, publish, or execute the listing.",
    parameters: Type.Object({
      candidate_id: Type.String({ minLength: 1, description: "Exact candidate id" }),
    }),
    async execute(_toolCallId, params, signal) {
      const data = await apiRequest(
        `/resale/candidates/${encodeURIComponent(params.candidate_id)}/agent-review`,
        { method: "POST" },
        signal,
      );
      return result(data, { candidate_id: params.candidate_id, external_action: false });
    },
  });

  pi.registerTool({
    name: "xianyu_prepare_listing",
    label: "Prepare Xianyu Listing",
    description: "Create a local listing draft and approval WorkOrder after all publish gates pass. It never publishes directly.",
    parameters: Type.Object({
      cookie_id: Type.String({ minLength: 1, description: "Exact seller account id" }),
      candidate_id: Type.String({ minLength: 1, description: "Exact candidate id" }),
    }),
    async execute(_toolCallId, params, signal) {
      const query = new URLSearchParams({ cookie_id: params.cookie_id });
      const data = await apiRequest(
        `/resale/candidates/${encodeURIComponent(params.candidate_id)}/listing-draft?${query}`,
        { method: "POST" },
        signal,
      );
      return result(data, {
        candidate_id: params.candidate_id,
        approval_required: true,
        external_action: false,
      });
    },
  });

  pi.registerTool({
    name: "xianyu_prepare_customer_reply",
    label: "Prepare Customer Reply",
    description: "Generate a reply draft for operator review. It does not send the message to Xianyu.",
    parameters: Type.Object({
      cookie_id: Type.String({ minLength: 1, description: "Exact seller account id" }),
      buyer_id: Type.String({ minLength: 1, description: "Buyer id or local alias" }),
      message: Type.String({ minLength: 1, description: "Incoming buyer message" }),
      product_id: Type.Optional(Type.String()),
      product_title: Type.Optional(Type.String()),
      product_description: Type.Optional(Type.String()),
      product_price: Type.Optional(Type.String()),
    }),
    async execute(_toolCallId, params, signal) {
      const data = await apiRequest(
        "/agents/customer-service/reply",
        { method: "POST", body: JSON.stringify(params) },
        signal,
      );
      return result({ ...data, sent: false }, { external_action: false });
    },
  });
}
