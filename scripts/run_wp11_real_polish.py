"""WP11 real-model polish evidence run (separate from mock tests).

Produces one deterministic draft from verified facts, then asks the
configured model to rephrase it, and shows the guard verdict. Uses a
temporary data dir; nothing is persisted to live data.

Usage:
    .venv/Scripts/python.exe scripts/run_wp11_real_polish.py
"""
from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xianyu_agent.config import Settings  # noqa: E402
from xianyu_agent.llm import DeepSeekClient  # noqa: E402
from xianyu_agent.price_commitments import PriceCommitmentStore, ProductFactsStore  # noqa: E402
from xianyu_agent.reply_drafts import DraftReplyService  # noqa: E402


def main() -> int:
    settings = dataclasses.replace(Settings.from_env(), mimo_api_key="")
    client = DeepSeekClient(settings)
    if not client.enabled:
        print("REAL_RUN_FAILED: model provider not enabled")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        facts = ProductFactsStore(root / "facts.json")
        commitments = PriceCommitmentStore(root / "commitments.json")
        service = DraftReplyService(root, facts, commitments)
        facts.upsert(
            cookie_id="acc-demo", item_id="item-demo", list_price=299.0, stock_available=3, ships_in_days=2
        )
        draft = service.draft_reply(
            cookie_id="acc-demo", chat_id="chat-demo", item_id="item-demo",
            buyer_text="200卖吗", floor=180.0,
        )
        polished = service.polish_draft(draft, client=client)
        print(
            json.dumps(
                {
                    "model": settings.deepseek_model,
                    "deterministic_body": draft["body"],
                    "polish_accepted": polished["polish_accepted"],
                    "polished_body": polished["body"],
                    "guard_notes": polished["polish_guard_notes"],
                    "polish_failure": polished["polish_failure"],
                    "note": "guards kept: no new amounts/promises/claims; deterministic body is fallback",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
