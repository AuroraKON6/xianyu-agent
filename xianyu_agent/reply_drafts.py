"""Draft-only customer reply service (WP11 minimal).

Composes the existing fact sources -- ProductFactsStore (revision-backed
listing facts), PriceCommitmentStore (monotonic bargaining memory) and
chat events -- into reply DRAFTS. This module has no send path at all:
every output is a draft that still goes through the existing
draft -> approval -> execution chain if the operator adopts it. The
rollback state ("只产草稿") is therefore the only mode that exists.

Guard rails enforced here (WP11 backlog, zero-tolerance items):
- Money extraction decides speaker/semantics before anything is
  recorded: negated mentions, freight amounts, third-party reference
  prices and multi-item totals are never recorded as buyer offers.
- A bargaining draft never goes below the effective floor, never
  promises unknown stock, unknown arrival dates or unverified claims.
- This module NEVER writes seller price commitments (those only come
  from platform-confirmed sends in the existing GH-05 path).
- While a chat is under manual takeover, new inbound messages produce
  no drafts at all.
- When listing facts are unknown, the draft says so and escalates
  instead of inventing an answer.

The classifier is deterministic on purpose: the 2026-09-18 model keys
are expired (401), so any LLM polish would be untestable this round.
The draft body is assembled from verified facts with revision ids.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .commerce.repositories import LockedJsonList
from .commerce.contracts import new_id, utc_now
from .price_commitments import PriceCommitmentStore, ProductFactsStore

INTENT_PRICE_INQUIRY = "price_inquiry"
INTENT_BARGAIN_OFFER = "bargain_offer"
INTENT_SPEC_QUESTION = "spec_question"
INTENT_SHIPPING_QUESTION = "shipping_question"
INTENT_STOCK_QUESTION = "stock_question"
INTENT_REFUND_REQUEST = "refund_request"
INTENT_OTHER = "other"

INTENTS = (
    INTENT_PRICE_INQUIRY,
    INTENT_BARGAIN_OFFER,
    INTENT_SPEC_QUESTION,
    INTENT_SHIPPING_QUESTION,
    INTENT_STOCK_QUESTION,
    INTENT_REFUND_REQUEST,
    INTENT_OTHER,
)

DRAFT_FACT_ANSWER = "fact_answer"
DRAFT_COUNTER_OFFER = "counter_offer"
DRAFT_DECLINE = "decline_offer"
DRAFT_ESCALATE = "escalate"
DRAFT_TAKEOVER_HOLD = "manual_takeover_hold"

# ---------------------------------------------------------------------------
# Deterministic intent + guarded money extraction
# ---------------------------------------------------------------------------

_REFUND_WORDS = ("退款", "退货", "退钱", "不想要了", "售后", "能退", "想退", "无理由")
_STOCK_WORDS = ("有货吗", "有现货", "还有吗", "库存", "还有没有", "有货", "还在卖", "现货")
_SHIPPING_WORDS = ("运费", "邮费", "快递费", "发货", "什么时候到", "几天到", "几天能到", "物流", "包邮", "能发", "顺丰", "自提", "几天", "送到", "货到付款", "秒发")
_SPEC_WORDS = ("尺寸", "多大", "多厚", "规格", "材质", "型号", "功率", "容量", "重量", "颜色", "成色", "几层", "抽屉", "不锈钢", "脚轮", "插电", "电池", "划痕", "保修", "联保", "多少个")
_PRICE_WORDS = ("多少钱", "什么价", "价格", "卖多少", "最低多少", "便宜点", "少点", "最低价", "太贵", "多个钱", "优惠", "单价", "便宜", "底价", "能刀", "报价", "含税")
_FALLBACK_VERDICTS = ("freight", "reference", "negated", "multi_item_total", "stale")

_FREIGHT_MARKERS = ("运费", "邮费", "快递费", "物流费")
_REFERENCE_MARKERS = ("别人", "其他家", "别家", "其他店", "官网", "旗舰店", "同款", "淘宝", "拼多多", "京东", "抖音", "直播间", "仓库", "隔壁", "线下")
_NEGATION_MARKERS = ("不要", "不用", "不是", "别按", "没有", "不低于", "少于")
_STALE_MARKERS = ("上次", "之前说", "刚才", "昨天", "上回")
_QUANTITY_RE = re.compile(r"(两|二|三|四|五|六|七|八|九|十|\d+)\s*(个|件|张|台|把|套|只|副|条)")
_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[元块]?")
_FREIGHT_AFTER_RE = re.compile(r"^\s*块?\s*(运费|邮费|快递费)")
_MODEL_BEFORE_RE = re.compile(r"[A-Za-z]\s*[-_]?\s*$")

MIN_AMOUNT = 1.0
MAX_AMOUNT = 100000.0


def _scan_amounts(text: str) -> List[Dict[str, Any]]:
    """Scan plausible CNY amounts with positions (deterministic)."""
    clean = str(text or "")
    found: List[Dict[str, Any]] = []
    for match in _AMOUNT_RE.finditer(clean):
        amount = round(float(match.group(1)), 2)
        if not MIN_AMOUNT <= amount <= MAX_AMOUNT:
            continue
        found.append({"amount": amount, "start": match.start(1), "end": match.end(1)})
    return found


def _marker_adjacent_before(clean: str, start: int, before: str, markers: tuple, *, max_gap: int = 2, window: int = 6) -> bool:
    """True when a marker sits just before this amount and owns it.

    The gap between marker and amount must be short and digit-free, so a
    marker that belongs to a PREVIOUS amount ("不是350，320") or crosses
    a clause boundary never leaks onto this one.
    """
    for marker in markers:
        idx = before.rfind(marker)
        if idx == -1 or idx < len(before) - window:
            continue
        absolute = start - len(before) + idx
        gap = clean[absolute + len(marker) : start]
        if len(gap) > max_gap or any(ch.isdigit() for ch in gap):
            continue
        return True
    return False


def _freight_marker_before(clean: str, start: int, before: str) -> bool:
    """A freight marker just before this amount marks it as freight.

    False positives to reject: the marker directly follows a digit
    ("补20运费，300") -- it belongs to the previous amount -- and wide
    gaps that cross clause boundaries.
    """
    for marker in _FREIGHT_MARKERS:
        idx = before.rfind(marker)
        if idx == -1 or idx < len(before) - 6:
            continue
        absolute = start - len(before) + idx
        gap = clean[absolute + len(marker) : start]
        if len(gap) > 2 or any(ch.isdigit() for ch in gap):
            continue
        previous = clean[absolute - 1] if absolute > 0 else ""
        if previous.isdigit():
            continue
        return True
    return False


def analyze_money_mentions(text: str) -> Dict[str, Any]:
    """Position-aware money analysis of one buyer message.

    Every amount gets one verdict: ``offer`` / ``freight`` /
    ``reference`` / ``negated`` / ``multi_item_total`` / ``stale`` /
    ``model_number``. Markers count only on the correct side of the
    amount (a negation AFTER an amount belongs to something else:
    ``商品195，不是运费`` keeps 195 as an offer). The LAST ``offer``
    by position is the operative offer.
    """
    clean = str(text or "")
    analyzed: List[Dict[str, Any]] = []
    for scan in _scan_amounts(clean):
        amount = scan["amount"]
        start, end = scan["start"], scan["end"]
        before = clean[max(0, start - 10) : start]
        after = clean[end : end + 10]

        verdict, reason = "offer", ""
        if _MODEL_BEFORE_RE.search(before):
            # e.g. GZ-1200: the digits are part of a model/code token
            verdict, reason = "model_number", "digits attached to a model/code token"
        elif re.match(r"^\s*(个|件|张|台|把|套|只|送|新)", after or " ") or clean[max(0, start - 1) : start] == "送":
            # e.g. 5个 / 买5送1: counts; 99新: a condition grade
            verdict, reason = "quantity_count", "number is a count or grade, not an amount"
        elif re.match(r"^\s*(不锈钢|钢)", after or " ") and len(str(int(amount))) == 3:
            # e.g. 304不锈钢: a steel grade, not a price
            verdict, reason = "material_grade", "number is a material grade"
        elif _freight_marker_before(clean, start, before) or _FREIGHT_AFTER_RE.match(after or " "):
            verdict, reason = "freight", "amount belongs to freight, not the product price"
        elif any(marker in before for marker in _REFERENCE_MARKERS) or "太贵" in after[:6] or "不合理" in after[:6]:
            verdict, reason = "reference", "third-party or seller price reference"
        elif _marker_adjacent_before(clean, start, before, _NEGATION_MARKERS):
            verdict, reason = "negated", "amount explicitly rejected just before it"
        elif _QUANTITY_RE.search(before[-8:]):
            # quantity BEFORE the amount ("两个60"); a quantity after the
            # amount is usually a purchase plan ("170卖的话我要两台")
            verdict, reason = "multi_item_total", "amount tied to a quantity; unit price unresolved"
        elif any(marker in before for marker in _STALE_MARKERS):
            verdict, reason = "stale", "mentions a previous quote, not a current offer"

        excerpt = clean[max(0, start - 12) : min(len(clean), end + 12)].strip()
        analyzed.append({"amount": amount, "verdict": verdict, "reason": reason, "excerpt": excerpt})

    offers = [m for m in analyzed if m["verdict"] == "offer"]
    return {
        "mentions": analyzed,
        "operative_offer": offers[-1]["amount"] if offers else None,
        "offer_count": len(offers),
    }


def classify_intent(text: str) -> str:
    """Ordered deterministic rules; money verdicts beat word lists."""
    clean = str(text or "").strip()
    if not clean:
        return INTENT_OTHER
    if any(word in clean for word in _REFUND_WORDS):
        return INTENT_REFUND_REQUEST
    money = analyze_money_mentions(clean)
    if money["operative_offer"] is not None:
        return INTENT_BARGAIN_OFFER
    if any(word in clean for word in _STOCK_WORDS):
        return INTENT_STOCK_QUESTION
    if any(word in clean for word in _SHIPPING_WORDS):
        return INTENT_SHIPPING_QUESTION
    if any(word in clean for word in _SPEC_WORDS):
        return INTENT_SPEC_QUESTION
    if any(word in clean for word in _PRICE_WORDS):
        return INTENT_PRICE_INQUIRY
    if money["mentions"] and any(m["verdict"] in _FALLBACK_VERDICTS for m in money["mentions"]):
        # non-offer money (freight/reference/negated/multi/stale) still
        # needs a bargaining-shaped escalation, not silence; pure counts,
        # model numbers and grades do not
        return INTENT_BARGAIN_OFFER
    return INTENT_OTHER


# ---------------------------------------------------------------------------
# Draft service
# ---------------------------------------------------------------------------


class DraftReplyService:
    def __init__(
        self,
        data_dir,
        facts_store: ProductFactsStore,
        commitments_store: PriceCommitmentStore,
        *,
        floor_ratio: float = 0.9,
    ) -> None:
        self._drafts = LockedJsonList(data_dir / "commerce" / "reply_drafts.json")
        self._facts = facts_store
        self._commitments = commitments_store
        self._floor_ratio = float(floor_ratio)

    # ---- public API ----

    def draft_reply(
        self,
        *,
        cookie_id: str,
        chat_id: str,
        item_id: str,
        buyer_text: str,
        floor: Optional[float] = None,
        manual_takeover_until: Optional[float] = None,
        latest_inbound_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Produce one draft (or a hold/escalate record). Never sends.

        ``manual_takeover_until`` is a unix timestamp; when messages
        arrived after it (``latest_inbound_at``), this chat stays under
        human control and no draft is produced.
        """
        if not str(buyer_text or "").strip():
            raise ValueError("buyer_text required")
        if (
            manual_takeover_until is not None
            and latest_inbound_at is not None
            and latest_inbound_at > manual_takeover_until
        ):
            return self._record(
                {
                    "draft_type": DRAFT_TAKEOVER_HOLD,
                    "cookie_id": cookie_id,
                    "chat_id": chat_id,
                    "item_id": item_id,
                    "body": "",
                    "notes": ["chat under manual takeover with newer inbound; no draft generated"],
                    "evidence_refs": [],
                    "guards": [{"guard": "manual_takeover", "state": "blocked"}],
                    "intent": classify_intent(buyer_text),
                }
            )

        intent = classify_intent(buyer_text)
        money = analyze_money_mentions(buyer_text)
        facts = self._facts.get(cookie_id=cookie_id, item_id=item_id)
        commitment = self._commitments.get_record(cookie_id=cookie_id, chat_id=chat_id, item_id=item_id)

        if intent == INTENT_BARGAIN_OFFER and money["operative_offer"] is not None:
            return self._bargain_draft(
                cookie_id=cookie_id,
                chat_id=chat_id,
                item_id=item_id,
                offer=float(money["operative_offer"]),
                money=money,
                facts=facts,
                commitment=commitment,
                floor=floor,
            )
        if intent == INTENT_BARGAIN_OFFER:
            return self._escalate(
                cookie_id, chat_id, item_id, intent,
                notes=["amount present but no usable offer (freight/reference/negated/multi-item)"],
                money=money,
            )
        if intent == INTENT_REFUND_REQUEST:
            return self._escalate(
                cookie_id, chat_id, item_id, intent,
                notes=["refund requests are human decisions; draft only summarizes"],
                money=money,
            )
        if intent in (INTENT_SPEC_QUESTION, INTENT_SHIPPING_QUESTION, INTENT_STOCK_QUESTION, INTENT_PRICE_INQUIRY):
            return self._fact_draft(cookie_id, chat_id, item_id, intent, facts, money)
        return self._escalate(
            cookie_id, chat_id, item_id, intent,
            notes=["intent not covered by deterministic drafts"],
            money=money,
        )

    def list_drafts(self, *, chat_id: str = "", item_id: str = "") -> List[Dict[str, Any]]:
        def predicate(draft: Mapping[str, Any]) -> bool:
            if chat_id and str(draft.get("chat_id")) != chat_id:
                return False
            if item_id and str(draft.get("item_id")) != item_id:
                return False
            return True

        return self._drafts.find(predicate)

    # ---- LLM polish (guards unchanged; deterministic body is the fallback) ----

    _POLISHABLE_TYPES = (DRAFT_FACT_ANSWER, DRAFT_COUNTER_OFFER, DRAFT_DECLINE)
    _PROMISE_TOKENS = ("天内发货", "天内到", "保证到", "现货", "有货")

    def polish_draft(self, draft: Mapping[str, Any], *, client: Any) -> Dict[str, Any]:
        """Rephrase one draft with the model under hard guards.

        The model only rewords. It may not introduce any money amount
        absent from the deterministic draft/facts, any unverified-claim
        pattern, or any stock/arrival promise the deterministic body
        did not make. Any violation (or an LLM failure) keeps the
        deterministic body -- polish is cosmetic, never authoritative.
        """
        from .listing_guard import scan_listing_text

        if str(draft.get("draft_type")) not in self._POLISHABLE_TYPES:
            return dict(draft)
        original_body = str(draft.get("body") or "")
        if not original_body.strip():
            return dict(draft)

        allowed_amounts = {m["amount"] for m in _scan_amounts(original_body)}
        for extra in (draft.get("effective_floor"), draft.get("offer_amount")):
            if extra is not None:
                allowed_amounts.add(round(float(extra), 2))

        system = (
            "你是闲鱼卖家客服措辞助手。只改写输入的回复草稿，使其更自然礼貌。"
            "禁止：添加任何新的数字/价格/日期/库存或到货承诺/平台外联系方式；"
            "禁止改变事实。输出 JSON：{\"reply\": \"改写后的回复\"}。"
        )
        user = f"原始草稿：{original_body}"
        polished_body = ""
        failure = ""
        try:
            result = client.chat_json(system, user, temperature=0.3, max_tokens=300)
            polished_body = str((result or {}).get("reply") or "").strip()
        except Exception as exc:  # noqa: BLE001 - model failures fall back safely
            failure = f"llm error: {exc}"

        guard_notes: List[str] = []
        accepted = False
        if polished_body:
            if scan_listing_text(polished_body):
                guard_notes.append("rejected: polished text contains unverified-claim patterns")
            elif len(polished_body) > 300:
                guard_notes.append("rejected: polished text too long")
            else:
                new_amounts = [m for m in _scan_amounts(polished_body) if m["amount"] not in allowed_amounts]
                if new_amounts:
                    guard_notes.append(
                        "rejected: polished text introduces amounts absent from the deterministic draft: "
                        + ", ".join(str(m["amount"]) for m in new_amounts)
                    )
                else:
                    added_promises = [
                        token
                        for token in self._PROMISE_TOKENS
                        if token in polished_body and token not in original_body
                    ]
                    if added_promises:
                        guard_notes.append("rejected: polished text adds promises not in the draft: " + ", ".join(added_promises))
                    else:
                        accepted = True
        elif not failure:
            failure = "model returned no reply field"

        record = dict(draft)
        record.update(
            {
                "record_id": new_id("rdraft"),
                "created_at": utc_now().isoformat(),
                "polish_of": draft.get("record_id"),
                "body": polished_body if accepted else original_body,
                "polish_accepted": accepted,
                "polish_attempted_body": polished_body or None,
                "polish_failure": failure or None,
                "polish_guard_notes": guard_notes,
                "status": "draft_needs_approval",
                "send_path": "none: this service produces drafts only",
            }
        )
        return self._drafts.append(record)

    # ---- draft builders ----

    def _effective_floor(
        self,
        facts: Optional[Dict[str, Any]],
        commitment: Optional[Dict[str, Any]],
        explicit: Optional[float],
    ) -> Optional[float]:
        # priority: a platform-confirmed past commitment and an explicit
        # operator floor can never be undercut by the list-price ratio
        # heuristic; the ratio only applies when neither exists.
        candidates: List[float] = []
        if commitment:
            lowest = commitment.get("seller_lowest_committed")
            if isinstance(lowest, Mapping) and lowest.get("amount"):
                candidates.append(float(lowest["amount"]))
        if explicit is not None and explicit > 0:
            candidates.append(float(explicit))
        if candidates:
            return max(candidates)
        if facts and facts.get("list_price"):
            return float(facts["list_price"]) * self._floor_ratio
        return None

    def _bargain_draft(
        self,
        *,
        cookie_id: str,
        chat_id: str,
        item_id: str,
        offer: float,
        money: Mapping[str, Any],
        facts: Optional[Dict[str, Any]],
        commitment: Optional[Dict[str, Any]],
        floor: Optional[float],
    ) -> Dict[str, Any]:
        guards: List[Dict[str, Any]] = []
        notes: List[str] = []
        evidence: List[Dict[str, Any]] = []

        # record the buyer offer in the monotonic memory (buyer side only;
        # the GH-05 store keeps it "only rises")
        self._commitments.record_buyer_offer(
            cookie_id=cookie_id,
            chat_id=chat_id,
            item_id=item_id,
            amount=offer,
            source="reply_draft_analysis",
            excerpt=str(money.get("mentions") or "")[:120],
        )
        evidence.append({"ref_type": "price_commitment", "ref_id": f"{cookie_id}|{chat_id}|{item_id}"})

        effective_floor = self._effective_floor(facts, commitment, floor)
        if effective_floor is None:
            guards.append({"guard": "floor_known", "state": "blocked"})
            return self._escalate(
                cookie_id, chat_id, item_id, INTENT_BARGAIN_OFFER,
                notes=["no list price, no committed floor, no explicit floor: cannot judge the offer"],
                money=money,
                guards=guards,
            )
        guards.append({"guard": "floor_known", "state": "pass", "note": f"floor={effective_floor}"})

        stock_known = bool(facts and facts.get("stock_available") is not None)
        guards.append({"guard": "stock_known", "state": "pass" if stock_known else "unknown"})
        if facts:
            revision_id = (facts.get("revisions") or [{}])[-1].get("revision_id")
            if revision_id:
                evidence.append({"ref_type": "product_facts_revision", "ref_id": revision_id})

        if offer >= effective_floor:
            body = (
                f"可以的，{offer:g} 元可以出。"
                + (f"当前有货，{facts.get('ships_in_days')} 天内发货。" if stock_known and facts.get("ships_in_days") is not None else "拍前我确认一下库存再回复您。")
            )
            draft_type = DRAFT_COUNTER_OFFER
            notes.append("offer at/above floor; acceptance draft for operator approval")
        else:
            body = f"抱歉，{offer:g} 元低于我能出的底价，价格到不了那里。"
            draft_type = DRAFT_DECLINE
            notes.append(f"offer below effective floor {effective_floor:g}; decline draft")
        if not stock_known:
            notes.append("stock unknown: draft must not promise stock or arrival dates")

        return self._record(
            {
                "draft_type": draft_type,
                "cookie_id": cookie_id,
                "chat_id": chat_id,
                "item_id": item_id,
                "body": body,
                "notes": notes,
                "evidence_refs": evidence,
                "guards": guards,
                "intent": INTENT_BARGAIN_OFFER,
                "offer_amount": offer,
                "effective_floor": effective_floor,
                "money_analysis": dict(money),
            }
        )

    def _fact_draft(
        self,
        cookie_id: str,
        chat_id: str,
        item_id: str,
        intent: str,
        facts: Optional[Dict[str, Any]],
        money: Mapping[str, Any],
    ) -> Dict[str, Any]:
        evidence: List[Dict[str, Any]] = []
        if facts:
            revision_id = (facts.get("revisions") or [{}])[-1].get("revision_id")
            if revision_id:
                evidence.append({"ref_type": "product_facts_revision", "ref_id": revision_id})
        parts: List[str] = []
        notes: List[str] = []
        answered = False

        if intent in (INTENT_PRICE_INQUIRY,) and facts and facts.get("list_price"):
            parts.append(f"标价 {facts['list_price']:g} 元。")
            answered = True
        if intent == INTENT_SPEC_QUESTION:
            condition = (facts or {}).get("condition") or ""
            notes_text = (facts or {}).get("notes") or ""
            if condition:
                parts.append(f"成色：{condition}。")
                answered = True
            if notes_text:
                parts.append(f"商品说明（以当前资料为准）：{notes_text}")
                answered = True
            if not answered:
                notes.append("spec facts not on record; refusing to invent specifications")
        if intent == INTENT_STOCK_QUESTION:
            stock = (facts or {}).get("stock_available")
            if stock is None:
                notes.append("stock unknown: no stock promise, escalate to operator check")
            else:
                parts.append(f"当前可售 {stock} 件。" if stock > 0 else "暂时缺货。")
                answered = True
        if intent == INTENT_SHIPPING_QUESTION:
            ships = (facts or {}).get("ships_in_days")
            if ships is None:
                notes.append("shipping time unknown: no arrival promise, escalate to operator check")
            else:
                parts.append(f"{ships} 天内发货。")
                answered = True

        if intent == INTENT_SHIPPING_QUESTION and money["operative_offer"] is not None:
            notes.append("freight amount mentioned; treated as freight, never as the product price")

        if answered and parts:
            body = "".join(parts)
            draft_type = DRAFT_FACT_ANSWER
        else:
            body = "这个问题我确认一下具体资料后回复您。"
            draft_type = DRAFT_ESCALATE
        return self._record(
            {
                "draft_type": draft_type,
                "cookie_id": cookie_id,
                "chat_id": chat_id,
                "item_id": item_id,
                "body": body,
                "notes": notes,
                "evidence_refs": evidence,
                "guards": [{"guard": "facts_backed", "state": "pass" if draft_type == DRAFT_FACT_ANSWER else "unknown"}],
                "intent": intent,
                "money_analysis": dict(money),
            }
        )

    def _escalate(
        self,
        cookie_id: str,
        chat_id: str,
        item_id: str,
        intent: str,
        *,
        notes: List[str],
        money: Optional[Mapping[str, Any]] = None,
        guards: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return self._record(
            {
                "draft_type": DRAFT_ESCALATE,
                "cookie_id": cookie_id,
                "chat_id": chat_id,
                "item_id": item_id,
                "body": "",
                "notes": notes,
                "evidence_refs": [],
                "guards": guards or [{"guard": "needs_human", "state": "blocked"}],
                "intent": intent,
                "money_analysis": dict(money or {"mentions": [], "operative_offer": None, "offer_count": 0}),
            }
        )

    def _record(self, draft: Mapping[str, Any]) -> Dict[str, Any]:
        record = dict(draft)
        record.update(
            {
                "record_type": "reply_draft",
                "record_id": new_id("rdraft"),
                "status": "draft_needs_approval",
                "created_at": utc_now().isoformat(),
                "send_path": "none: this service produces drafts only",
            }
        )
        return self._drafts.append(record)


# ---------------------------------------------------------------------------
# Scenario evaluation harness (WP11 acceptance)
# ---------------------------------------------------------------------------


def evaluate_reply_scenarios(scenarios: Sequence[Mapping[str, Any]], *, floor: float = 180.0) -> Dict[str, Any]:
    """Score the deterministic classifier/guards against labeled scenarios.

    Each scenario: {"text": ..., "expect_intent": ..., "expect_offer":
    float|null, "category": "..."}. Reports per-category accuracy with
    the sample size; the >=200-scenario target is only claimable when
    n says so.
    """
    import tempfile
    from pathlib import Path

    total = len(scenarios)
    if not total:
        raise ValueError("no scenarios provided")
    correct_intent = 0
    correct_offer = 0
    offer_cases = 0
    by_category: Dict[str, Dict[str, int]] = {}
    mistakes: List[Dict[str, Any]] = []
    for scenario in scenarios:
        text = str(scenario.get("text") or "")
        expected_intent = scenario.get("expect_intent")
        expected_offer = scenario.get("expect_offer")
        category = str(scenario.get("category") or "uncategorized")
        intent = classify_intent(text)
        money = analyze_money_mentions(text)
        got_intent_ok = intent == expected_intent
        got_offer_ok = True
        if expected_offer is not None or "expect_offer" in scenario:
            offer_cases += 1
            got_offer_ok = money["operative_offer"] == expected_offer
        correct_intent += 1 if got_intent_ok else 0
        correct_offer += 1 if got_offer_ok else 0
        bucket = by_category.setdefault(category, {"total": 0, "intent_ok": 0, "offer_ok": 0})
        bucket["total"] += 1
        bucket["intent_ok"] += 1 if got_intent_ok else 0
        bucket["offer_ok"] += 1 if got_offer_ok else 0
        if not got_intent_ok or not got_offer_ok:
            mistakes.append(
                {
                    "category": category,
                    "text": text[:40],
                    "expected_intent": expected_intent,
                    "actual_intent": intent,
                    "expected_offer": expected_offer,
                    "actual_offer": money["operative_offer"],
                }
            )
    return {
        "total": total,
        "intent_accuracy": round(correct_intent / total, 4),
        "offer_cases": offer_cases,
        "offer_accuracy": round(correct_offer / offer_cases, 4) if offer_cases else None,
        "by_category": by_category,
        "mistakes": mistakes,
        "sample_size_note": (
            f"n={total} scenarios; the 200-scenario gate is "
            + ("met" if total >= 200 else "NOT met")
            + "; guard-critical categories must stay at 100% regardless"
        ),
    }
