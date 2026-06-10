"""Xianyu auto-reply with multi-expert AI routing.

Inspired by XianyuAutoAgent's architecture:
- ClassifyAgent: intent detection (price/tech/default/no_reply)
- PriceAgent: negotiation with tiered discount strategy
- TechAgent: technical product Q&A
- DefaultAgent: general customer service
- Safety filter: blocks off-platform contact info
- Human takeover: toggle keyword switches between AI and manual mode
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Patterns for off-platform contact info (safety filter)
CONTACT_PATTERNS = [
    r"微信|wechat|weixin|wx",
    r"QQ[：:]?\s*\d+",
    r"支付宝|alipay",
    r"抖音|douyin|tiktok",
    r"电话|手机|tel|phone",
    r"\d{5,}",  # long numbers that might be phone/QQ
    r"加我|加好友|私聊|私信",
    r"咸鱼|闲鱼外|站外",
]

HUMAN_TOGGLE_KEYWORD = "。"  # Default toggle keyword


@dataclass
class ReplyRule:
    """A keyword-based reply rule."""
    id: str
    keyword: str
    reply_text: str
    product_id: str = ""  # Empty = applies to all products
    priority: int = 0
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConversationContext:
    """Chat context for a single buyer-seller conversation."""
    buyer_id: str
    product_id: str
    product_title: str
    messages: List[Dict[str, str]] = field(default_factory=list)
    is_ai_active: bool = True
    last_reply_time: float = 0.0

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content, "time": time.strftime("%H:%M:%S")})
        if role == "assistant":
            self.last_reply_time = time.time()

    def get_context(self, max_messages: int = 10) -> str:
        recent = self.messages[-max_messages:]
        return "\n".join(f"[{m['role']}] {m['content']}" for m in recent)


class AutoReplyEngine:
    """Multi-expert auto-reply engine for Xianyu.

    Priority: product-specific rule > product keywords > general keywords > default > AI
    """

    def __init__(self, settings: Any, llm: Any, storage: Any) -> None:
        self.settings = settings
        self.llm = llm
        self.storage = storage
        self.rules: List[ReplyRule] = []
        self.contexts: Dict[str, ConversationContext] = {}
        self._db = None
        self._load_rules()

    @property
    def db(self):
        if self._db is None:
            try:
                from .db_manager import db_manager
                self._db = db_manager
            except ImportError:
                pass
        return self._db

    def _load_rules(self) -> None:
        # Try database first
        if self.db:
            try:
                rows = self.db.get_auto_reply_rules()
                self.rules = [
                    ReplyRule(
                        id=r["id"],
                        keyword=r["keyword"],
                        reply_text=r["reply_text"],
                        product_id=r.get("product_id", ""),
                        priority=r.get("priority", 0),
                        enabled=bool(r.get("enabled", True)),
                    )
                    for r in rows
                ]
                return
            except Exception:
                pass
        # Fallback to JSON file
        rules_file = self.settings.data_dir / "auto_reply" / "rules.json"
        if rules_file.exists():
            try:
                data = json.loads(rules_file.read_text(encoding="utf-8"))
                self.rules = [ReplyRule(**item) for item in data]
            except Exception as exc:
                logger.warning("Failed to load reply rules: %s", exc)

    def _save_rules(self) -> None:
        # Save to database
        if self.db:
            try:
                for rule in self.rules:
                    self.db.save_auto_reply_rule(
                        rule.id, rule.keyword, rule.reply_text,
                        product_id=rule.product_id, priority=rule.priority, enabled=rule.enabled,
                    )
                return
            except Exception:
                pass
        # Fallback to JSON file
        rules_dir = self.settings.data_dir / "auto_reply"
        rules_dir.mkdir(parents=True, exist_ok=True)
        rules_file = rules_dir / "rules.json"
        rules_file.write_text(
            json.dumps([r.to_dict() for r in self.rules], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_rule(self, rule: ReplyRule) -> Dict[str, Any]:
        self.rules.append(rule)
        self._save_rules()
        return rule.to_dict()

    def remove_rule(self, rule_id: str) -> bool:
        before = len(self.rules)
        self.rules = [r for r in self.rules if r.id != rule_id]
        if len(self.rules) < before:
            if self.db:
                try:
                    self.db.delete_auto_reply_rule(rule_id)
                except Exception:
                    self._save_rules()
            else:
                self._save_rules()
            return True
        return False

    def list_rules(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self.rules]

    def save_conversation(self, cookie_id: str, chat_id: str, user_id: str, item_id: str, role: str, content: str, intent: str = "", bargain_count: int = 0):
        """Save conversation to database for persistence."""
        if self.db:
            try:
                self.db.save_conversation(cookie_id, chat_id, user_id, item_id, role, content, intent, bargain_count)
            except Exception as e:
                logger.warning("Failed to save conversation: %s", e)

    def get_conversation_history(self, cookie_id: str, chat_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Load conversation history from database."""
        if self.db:
            try:
                return self.db.get_conversation_history(cookie_id, chat_id, limit)
            except Exception:
                pass
        return []

    def toggle_ai(self, buyer_id: str, product_id: str = "") -> bool:
        """Toggle AI reply on/off for a conversation."""
        key = f"{buyer_id}:{product_id}"
        ctx = self.contexts.get(key)
        if ctx:
            ctx.is_ai_active = not ctx.is_ai_active
            return ctx.is_ai_active
        return True

    def should_reply(self, buyer_id: str, message: str, product_id: str = "") -> bool:
        """Check if we should auto-reply to this message."""
        # Check human toggle
        if message.strip() == HUMAN_TOGGLE_KEYWORD:
            self.toggle_ai(buyer_id, product_id)
            return False

        # Check if AI is active for this conversation
        key = f"{buyer_id}:{product_id}"
        ctx = self.contexts.get(key)
        if ctx and not ctx.is_ai_active:
            return False

        # Safety filter: don't reply to messages with contact info
        if self._contains_contact_info(message):
            logger.info("Blocked reply to %s: contains contact info", buyer_id)
            return False

        return True

    def generate_reply(
        self,
        buyer_id: str,
        message: str,
        product_id: str = "",
        product_title: str = "",
        product_description: str = "",
        product_price: str = "",
        cookie_id: str = "",
    ) -> Optional[str]:
        """Generate a reply using the multi-expert routing system.

        Returns None if no reply should be sent.
        """
        if not self.should_reply(buyer_id, message, product_id):
            return None

        # Get or create conversation context
        key = f"{buyer_id}:{product_id}"
        if key not in self.contexts:
            self.contexts[key] = ConversationContext(
                buyer_id=buyer_id,
                product_id=product_id,
                product_title=product_title,
            )
        ctx = self.contexts[key]
        ctx.add_message("user", message)

        # Save user message to database
        if cookie_id:
            self.save_conversation(cookie_id, key, buyer_id, product_id, "user", message)

        # Priority 1: Product-specific keyword rules
        rule_reply = self._match_rules(message, product_id)
        if rule_reply:
            ctx.add_message("assistant", rule_reply)
            if cookie_id:
                self.save_conversation(cookie_id, key, buyer_id, product_id, "assistant", rule_reply, "keyword")
            return rule_reply

        # Priority 2: AI expert routing
        intent = self._classify_intent(message, ctx)
        reply = self._route_to_expert(intent, message, ctx, product_title, product_description, product_price)

        if reply:
            reply = self._sanitize_reply(reply)
            ctx.add_message("assistant", reply)
            if cookie_id:
                bargain_count = self.db.get_bargain_count(cookie_id, key) if self.db else 0
                if intent == "price":
                    bargain_count += 1
                self.save_conversation(cookie_id, key, buyer_id, product_id, "assistant", reply, intent, bargain_count)

        return reply

    def _match_rules(self, message: str, product_id: str) -> Optional[str]:
        """Match message against keyword rules."""
        # Product-specific rules first (higher priority)
        product_rules = [r for r in self.rules if r.product_id == product_id and r.enabled]
        general_rules = [r for r in self.rules if not r.product_id and r.enabled]

        for rule in sorted(product_rules + general_rules, key=lambda r: -r.priority):
            if rule.keyword.lower() in message.lower():
                return rule.reply_text
        return None

    def _classify_intent(self, message: str, ctx: ConversationContext) -> str:
        """Classify buyer intent using LLM."""
        if not self.settings.deepseek_api_key:
            return self._heuristic_classify(message)

        prompt = f"""Classify the buyer's intent in this Xianyu chat message. Return ONLY one word:
- price_compare: comparing prices with Taobao, JD, PDD, or other sellers
- bargain: aggressively asking for discount, haggling, "can you lower the price"
- price: asking about price, discount, general price inquiry
- tech: asking about product details, specs, usage, compatibility
- proactive: greeting like "are you there", simple inquiry needing proactive info
- default: general inquiry, availability check
- no_reply: spam, irrelevant, or doesn't need a response

Message: {message}
Context: {ctx.get_context(5)}"""

        try:
            result = self.llm.chat(
                "You are a Xianyu chat intent classifier. Return exactly one word: price_compare, bargain, price, tech, proactive, default, or no_reply.",
                prompt,
            )
            intent = result.strip().lower()
            if intent in ("price_compare", "bargain", "price", "tech", "proactive", "default", "no_reply"):
                return intent
        except Exception:
            pass

        return self._heuristic_classify(message)

    def _heuristic_classify(self, message: str) -> str:
        """Simple keyword-based intent classification (no LLM needed)."""
        msg = message.lower()

        # Price comparison (比价)
        compare_words = ("淘宝", "京东", "拼多多", "pdd", "别家", "其他家", "更便宜", "贵", "比价")
        if any(w in msg for w in compare_words):
            return "price_compare"

        # Bargaining (砍价) - more aggressive price negotiation
        bargain_words = ("便宜点", "少点", "最低价", "能少", "让点", "优惠点", "打个折", "让利", "砍")
        if any(w in msg for w in bargain_words):
            return "bargain"

        # Standard price inquiry
        price_words = ("便宜", "优惠", "打折", "降价", "最低", "多少", "价格", "discount", "price")
        if any(w in msg for w in price_words):
            return "price"

        # Proactive info needed (asking "在吗" or simple greeting)
        proactive_words = ("在吗", "在？", "在么", "hello", "hi", "你好", "咨询", "问问")
        if any(w in msg for w in proactive_words):
            return "proactive"

        tech_words = ("功能", "怎么用", "配置", "参数", "规格", "兼容", "支持", "specs", "feature", "如何")
        if any(w in msg for w in tech_words):
            return "tech"

        skip_words = ("嗯", "好", "ok", "行", "知道了", "谢谢", "感谢")
        if any(w in msg for w in skip_words):
            return "no_reply"
        return "default"

    def _route_to_expert(
        self,
        intent: str,
        message: str,
        ctx: ConversationContext,
        product_title: str,
        product_description: str,
        product_price: str,
    ) -> Optional[str]:
        """Route to the appropriate expert agent."""
        if intent == "no_reply":
            return None

        if not self.settings.deepseek_api_key:
            return self._default_reply(message, product_title)

        expert_prompts = {
            "price": f"""You are a skilled Xianyu price negotiator. The buyer is asking about price.
Product: {product_title}
Price: {product_price}
Strategy: Be friendly but firm. Offer small discounts for bulk or quick purchase. Never go below 80% of listed price.
Respond in Chinese, under 50 characters.""",

            "bargain": f"""You are a Xianyu seller dealing with a bargainer. The buyer wants a lower price.
Product: {product_title}
Price: {product_price}
Strategy (from article):
- Round 1: Be firm but polite, say it's already the best price
- Round 2: Offer a small cash-back (e.g., 10-20 CNY) after positive review
- Never go below 85% of listed price
- Be cute/humorous: "老板，少不了的啦，我们都是实价在卖呢"
Respond in Chinese, under 60 characters.""",

            "price_compare": f"""You are a confident Xianyu seller. The buyer says another platform is cheaper.
Product: {product_title}
Strategy (from article):
- Be confident: "哪里都有高价和低价的"
- Emphasize quality: "我们主营实体店和线下批发，用料足"
- Differentiate: "不走网商路子，不搞精美图片，主打实实在在"
- Do NOT get defensive or aggressive
Respond in Chinese, under 80 characters.""",

            "proactive": f"""You are a proactive Xianyu seller. The buyer just said "hello" or "are you there".
Product: {product_title}
Strategy (from article):
- Don't just say "yes" - proactively share key product info
- Mention specs, material, popular choice, after-sales
- Example: "在的，这款是{product_title}，不锈钢材质，尺寸120cm，大家基本上都选这一款"
Respond in Chinese, under 80 characters.""",

            "tech": f"""You are a knowledgeable Xianyu product expert. Answer the buyer's technical question.
Product: {product_title}
Description: {product_description}
Be accurate and helpful. If you don't know, suggest the buyer check the description or ask the seller directly.
Respond in Chinese, under 100 characters.""",

            "default": f"""You are a friendly Xianyu customer service agent. Respond to the buyer's message.
Product: {product_title}
Be polite, concise, and helpful. Encourage the purchase without being pushy.
Respond in Chinese, under 50 characters.""",
        }

        prompt = expert_prompts.get(intent, expert_prompts["default"])
        context_str = ctx.get_context(5)

        try:
            reply = self.llm.chat(
                f"{prompt}\n\nConversation history:\n{context_str}",
                f"Buyer: {message}",
            )
            return reply.strip()
        except Exception as exc:
            logger.warning("LLM reply generation failed: %s", exc)
            return self._default_reply(message, intent, product_title)

    def _default_reply(self, message: str, intent: str, product_title: str) -> str:
        """Fallback reply when LLM is unavailable."""
        msg = message.lower()

        if intent == "price_compare":
            return "哪里都有高价和低价的，我们主营实体店和线下批发，用料足，不走网商路子，主打的就是实实在在，您可以放心拍。"

        if intent == "bargain":
            return "老板，少不了的啦，我们都是实价在卖呢。您看要不这样吧，您也是诚心要，我这边给您收货都返现20元吧，您觉得还满意给我一个好评行不行呀。"

        if intent == "proactive":
            if product_title:
                return f"在的亲！这款{product_title}，不锈钢材质，尺寸齐全，大家基本上都选的标准款，性价比很高的。需要可以直接拍下~"
            return "在的亲！产品材质和尺寸都很好的，需要可以直接拍下~"

        if any(w in msg for w in ("在吗", "你好", "hi", "hello")):
            return "在的亲，有什么可以帮您？"
        if any(w in msg for w in ("还有", "有货", "库存")):
            return "有的亲，可以直接拍下~"
        if any(w in msg for w in ("发货", "快递", "邮寄")):
            return "拍下后会尽快发货，请放心~"
        return "亲，您好！有什么问题可以随时问我~"

    def _sanitize_reply(self, reply: str) -> str:
        """Remove any contact info that might have leaked from LLM."""
        for pattern in CONTACT_PATTERNS:
            reply = re.sub(pattern, "[已过滤]", reply, flags=re.IGNORECASE)
        return reply.strip()

    def _contains_contact_info(self, message: str) -> bool:
        """Check if message contains off-platform contact info."""
        for pattern in CONTACT_PATTERNS:
            if re.search(pattern, message, re.IGNORECASE):
                return True
        return False
