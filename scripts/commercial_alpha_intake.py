from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from xianyu_agent.commercial_alpha_intake import (
    CommercialAlphaIntakeError,
    initialize_intake_bundle,
    validate_intake_bundle,
)
from xianyu_agent.config import Settings


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and validate one offline Commercial Alpha candidate bundle."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser("init", help="Create a new empty intake bundle.")
    init_parser.add_argument("--output", required=True, help="New or empty bundle directory.")

    validate_parser = commands.add_parser("validate", help="Read-only validation of an intake bundle.")
    validate_parser.add_argument("--bundle", required=True, help="Directory containing manifest.json.")
    validate_parser.add_argument(
        "--checked-at",
        default="",
        help="Optional ISO timestamp for a reproducible audit; defaults to now.",
    )
    validate_parser.add_argument(
        "--min-profit-amount",
        type=float,
        default=None,
        help="Override the configured minimum unit gross profit.",
    )
    validate_parser.add_argument(
        "--min-profit-rate",
        type=float,
        default=None,
        help="Override the configured minimum gross margin, for example 0.30.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_stdout()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            result = initialize_intake_bundle(args.output)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        settings = (
            Settings.from_env(ROOT / ".env")
            if args.min_profit_amount is None or args.min_profit_rate is None
            else None
        )
        result = validate_intake_bundle(
            args.bundle,
            checked_at=args.checked_at or None,
            min_profit_amount=(
                settings.min_profit_amount
                if args.min_profit_amount is None
                else args.min_profit_amount
            ),
            min_profit_rate=(
                settings.min_profit_rate
                if args.min_profit_rate is None
                else args.min_profit_rate
            ),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["bundle_ready"] else 2
    except (CommercialAlphaIntakeError, OSError, ValueError) as exc:
        print(json.dumps(
            {"ok": False, "status": "error", "error": str(exc)},
            ensure_ascii=False,
            indent=2,
        ))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
