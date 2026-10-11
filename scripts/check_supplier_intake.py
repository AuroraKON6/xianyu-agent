"""Read-only supplier intake check. Exit 0 means human review ready, not approval."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xianyu_agent.supplier_import import MAX_IMPORT_BYTES
from xianyu_agent.supplier_intake import IntakeDeclaration, assess_supplier_intake


def read_bounded(path, limit):
    with path.open("rb") as handle:
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise ValueError("input exceeds size limit")
    return content


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--quote-file", type=Path)
    parser.add_argument("--declaration", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.data_dir.resolve(strict=True)
        paths = {name: root / "commerce" / name for name in ("import_batches.json", "offers.json")}
        content = {name: read_bounded(path, 16 * 1024 * 1024) for name, path in paths.items()}
        records = {name: json.loads(payload.decode("utf-8-sig")) for name, payload in content.items()}
        if not all(isinstance(rows, list) and all(isinstance(row, dict) for row in rows) for rows in records.values()):
            raise ValueError("expected lists of persisted records")
        matches = [batch for batch in records["import_batches.json"] if batch.get("record_id") == args.batch_id]
        if len(matches) != 1:
            raise ValueError("batch absent or ambiguous")
        declaration = IntakeDeclaration()
        if args.declaration:
            declaration = IntakeDeclaration.model_validate_json(read_bounded(args.declaration, MAX_IMPORT_BYTES))
        quote = read_bounded(args.quote_file, MAX_IMPORT_BYTES) if args.quote_file else None
        report = assess_supplier_intake(matches[0], records["offers.json"], declaration, quote_bytes=quote)
        for name, path in paths.items():
            if read_bounded(path, 16 * 1024 * 1024) != content[name]:
                raise ValueError("source changed during assessment; retry on a stable snapshot")
        report["read_file_sha256"] = {name: hashlib.sha256(payload).hexdigest() for name, payload in content.items()}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "ready_for_human_review" else 2
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({"status": "input_error", "error_type": type(error).__name__,
                          "message": "Input missing, malformed, changed or too large; no business data was written.",
                          "external_llm_authorized": False, "platform_action_authorized": False}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
