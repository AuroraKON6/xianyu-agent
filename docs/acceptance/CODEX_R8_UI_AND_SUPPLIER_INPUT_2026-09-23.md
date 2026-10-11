# R8: Reconciliation UI Acceptance and Supplier Input Precision

Date: 2026-09-23 (Asia/Shanghai).
Scope: independent UI acceptance and supplier-input correctness. No real supplier, platform or model call was made in this batch. Previous R7's real Pi evidence remains historical and is not counted again.

## Delivered

1. Independently exercised the existing reconciliation UI with its real reconciliation API and temporary ledger. Its implementation was already present and was not overwritten.
2. Added `tests/test_supplier_import_precision.py` and corrected `xianyu_agent/supplier_import.py`.
3. Added an operational input contract, including explicit remaining gates before real shadow operation: [supplier input contract](../ops/supplier-quote-input-contract.md).

## UI Evidence

Command:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_pi_usage_ui.py
```

Result: 14 tests passed, 21.969s, exit 0. Scenarios include preview-only behavior, stale confirmation, repeated clicks, lost response, storage failure, incomplete response, locked credentials, literal rendering, and mobile width. Tests use a temporary-ledger application mounting the actual reconciliation router and actual console assets. The unrelated legacy app script is intentionally disabled in this fixture; this is not an end-to-end acceptance of every console feature.

Independent visual pass used the repository's installed Python Playwright runtime. The optional Playwright CLI was unavailable locally; no package installation was performed. At 1440x1000 and 390x844, exercised locked -> preview -> committed fixture receipt, captured all six panel screenshots, and inspected the desktop/mobile previews. The panel's client/scroll widths matched: 1088/1088 desktop, 306/306 mobile. No page errors were observed; no credential entered browser storage, URL, body or fixture ledger. Only a publicly labeled test credential was used.

Screenshots under `output/playwright/r8-pi-usage-20260923/`:

- `desktop-locked.png`, `desktop-preview.png`, `desktop-receipt.png`
- `mobile-locked.png`, `mobile-preview.png`, `mobile-receipt.png`

The fixture servers, browser contexts and browsers were shut down. No real reconciliation configuration or credentials were enabled. No development server or startup task was added by this batch.

## Supplier Red-to-Green Evidence

The initial 16-test run had 31 failed subtest assertions and one error. One explicit-CNY compatibility test already passed; it was retained as a guard. Failures included:

- `150-200` and `1e3` becoming known scalar prices;
- `USD 150` and explicit `currency=USD` becoming CNY prices;
- stock `1.5` becoming 1 and zero MOQ remaining valid;
- negative freight aborting the entire file;
- row numbers becoming Decimal prices (150 and 200 instead of 1 and 2);
- case prices converting without aligning the persisted unit or establishing inventory/MOQ units;
- partial tier parsing retaining a discount from ambiguous input.

After the first implementation, all 36 old/new supplier tests passed. Three further regressions were added and all failed before their fixes: unknown-price case unit alignment, duplicate unmapped headers shifting fields, and extra CSV cells silently discarded. The complete supplier suite now has 39 tests (20 existing + 19 new), all passed in 0.119s.

Behavior changes:

- Parse complete decimal values, not the first number found inside a string. Validate grouping before removing commas; no exponent or range guessing.
- Only integral quantities are accepted. Unclear counts stay unknown; explicit negative financial/quantity data rejects the affected row.
- Recognize a currency column. Unsupported currencies become unknown, without an exchange-rate assumption.
- Normalize case prices and units together; convert quantities only with an explicit quantity unit; preserve the conversion basis in the immutable snapshot.
- Keep all unknown values unknown even when an operator elects to archive a needs-confirmation row.
- Reject duplicate headers and excess CSV cells before they can shift financial fields.

No existing snapshot was migrated or reinterpreted. Historical files retain their prior values; new imports use the stricter parser.

## Validation Closure

Full isolated regression: **1445/1445**, 50.573s, exit 0, using `scripts/run_tests_isolated.ps1`. All 24 monitored modified/untracked Python, JavaScript, HTML, CSS, PowerShell and dependency/example-configuration files had identical before/after SHA-256 and file sets. The 20 live files had identical hashes and file sets relative to the start of this batch. This does not claim that documents, installed dependencies or the entire environment were frozen.

Python compilation, JavaScript syntax check and `git diff --check` passed. Exact hashes and the acceptance scope are recorded in [closing manifest](CODEX_R8_UI_AND_SUPPLIER_INPUT_MANIFEST_2026-09-23.json). Existing WP08 ResourceWarnings remain; mock fixture `REAL_RUN_OK` messages in regression output are not real model calls. This batch made zero real model calls and no live reconciliation or platform write. All changes remain uncommitted.

## Next Ordered Package

1. Bind source-observed time, import time, expiry and provenance separately. Current HTTP import confirmation defaults observed time to the import instant; a stale source file must not become current merely by being uploaded today.
2. Make missing/future/expired source times ineligible for business qualification. Preserve raw evidence rather than retroactively editing historical dates.
3. Persist real/demo/unknown provenance through snapshots, Pi inputs and shadow samples; demo events must not satisfy real trial sample/duration gates.
4. Verify those paths with stale/future/unknown/demo fixtures, cancellation/revocation and reload tests. Only then ingest an actual supplier batch and conduct a bounded read-only trial.
5. Keep the established exact-SKU, cost, evidence, account and human-approval checks; no automatic trading release is implied by this report.

The next package is a concrete prerequisite, not an assertion that real quotation intake or commercial readiness is already complete. Source attestations still require independent human verification, and JSON persistence remains single-process scoped.
