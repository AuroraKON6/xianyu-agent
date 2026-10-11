"""Isolated demo validation; offline by default, never uses data/live.

Example:
  python scripts/run_pi_quote_review_real.py --data-dir data/pi-validation/new-run
  Add --mode real --allow-model only for an explicitly enabled model attempt.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xianyu_agent.pi_validation import main


if __name__ == "__main__":
    sys.exit(main())
