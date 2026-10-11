# R7: Isolated Pi Validation and Multi-Turn Accounting

Date: 2026-09-22
Status: Real demo validation PASS; not commercial or live-trading acceptance.
Changes are uncommitted. Existing R6 and concurrently authored reconciliation/UI changes were preserved.

## 1. Scope and Sequence

This batch follows the R6 plan: evidence-backed unknown-usage reconciliation, an isolated validation runner, then one nonempty real Pi read-only task. It does not enable automatic replies, listing publication, purchasing, payment, or refunds.

- Independently reran the concurrently authored reconciliation API suite: 17 tests passed in 4.167s. The API remains disabled without its separate operator configuration. No real ledger entry was manually reconciled.
- Corrected Pi multi-turn consumption accounting before making the real call.
- Replaced the old fixed-live-directory validation script with a fresh-directory runner.
- Executed one real demo task through application HTTP, the persisted task runner, Pi RPC, a read-only business tool, structured decision storage, and the evidence endpoint.

The reconciliation API's evidence is an operator-attested reference and statement. Its digest binds the proposal; it does not independently verify a provider invoice or referenced file bytes. See [reconciliation API report](CODEX_R7_USAGE_RECONCILIATION_2026-09-22.md) and [operator instructions](../PI_USAGE_RECONCILIATION.md).

## 2. Runtime Accounting Fix

Previously, each assistant `message_end` replaced the preceding usage. A tool round followed by a final answer therefore left only the last model turn in the ledger. Earlier text could also conceal a later model error.

Three added regressions failed before the fix and passed afterward:

1. Two assistant turns consuming 15 and 27 tokens now total 42; a tool-result message's usage is not counted as model usage.
2. A missing usage field in any assistant turn remains unknown, rather than becoming the final turn's reported value.
3. A later model error rejects the prompt even if earlier text exists.

`PiRpcClient.prompt` now sums only complete nonnegative integer usage fields across assistant turns. The persisted job evidence includes the reported model-turn count. The focused runtime suite passed all 9 tests in 0.680s.

## 3. Isolation and Ownership

Implementation: `xianyu_agent/pi_validation.py`; entry point: `scripts/run_pi_quote_review_real.py`.

- A new `--data-dir` is mandatory. Existing directories and the live root, its descendants, and its ancestors are refused after path resolution.
- Default mode is offline. Real mode additionally requires `--allow-model`.
- Data, SQLite, browser-unused path, Pi workspace, and Pi sessions are all scoped to the new run directory.
- A hidden owned subprocess holds its own loopback socket on an allocated port. It never attaches to an existing application service.
- Before task submission, the parent checks run ID, mode, session, data root, PID/parent PID, and the server's HTTP identity. Windows venv redirectors are accepted only through the direct-child lineage check.
- The application lifespan is disabled in this worker. Discovery, auto-reply, shadow observation, and reconciliation credentials are disabled in its environment.
- The Pi workspace starts empty. Only the explicit repository business extension and `xianyu_business_snapshot` tool are enabled; ambient extensions, skills, and prompt templates are disabled.
- Agent/provider retry and compaction are disabled for this run. The parent submits one task and requests one execution, without retrying a failed model operation.
- Job credentials are restricted to GET and the application's existing allowlist. They cannot invoke validation control endpoints.
- Normal completion requests owned-worker shutdown and waits for exit. Forced shutdown makes validation fail. No startup task or persistent service is installed.
- The live file set and SHA-256 content hashes are checked before and after validation.

Six focused harness tests passed (including a complete actual-HTTP offline run). Early testing exposed Windows launcher/worker PID differences: the offline debug run refused submission, then the verified direct-child identity handling was added. The retained `data/pi-validation/offline-r7-debug` report is a failed debug artifact, not a passed acceptance run; no model task was submitted there.

## 4. Actual Real Run

Command executed exactly once for this directory:

```powershell
.\.venv\Scripts\python.exe scripts/run_pi_quote_review_real.py --data-dir data/pi-validation/r7-real-20260922 --mode real --allow-model
```

| Evidence | Actual result |
| --- | --- |
| Report | `data/pi-validation/r7-real-20260922/validation.json` |
| Mode and provenance | `real`; both quote rows explicitly `source=demo` |
| Supplier | `DEMO-NOT-A-REAL-SUPPLIER` |
| Imported quotes | 2, committed through preview/confirm HTTP endpoints |
| Run ID | `430369ef-a9ca-4bcc-865b-96af68573beb` |
| Session | `bdd78fc3-6f36-41c7-8a94-4235bed6099a` |
| Job | `job_1c0429db084d4e5a`, attempt 1, succeeded |
| Valid decision | `dec_59d65bd30cee481d` |
| Model | `deepseek-flash` |
| Model responses | 2 assistant turns, one task attempt |
| Usage | 3401 total tokens: 1622 input + 499 output + 1280 cache read |
| Tool | 1 successful `xianyu_business_snapshot` event |
| HTTP gate probes | Allowed read 200; write 403; unlisted read 403 |
| Evidence input hash | `1b5e9a17a9f83b88e0a78f54f6fec5f2e81613daa4ab4a0cc5151da555daf29a` |
| Worker | Port 44226; exit 0; no forced shutdown |
| Live storage | Same 20 files and SHA-256 hashes before/after |

The model identified missing freight for DEMO-2 and requested an explicit freight currency with the quote. DEMO-1 had a known freight amount. This is an actual nonempty input/output check, not a provider ping, mock, or empty-dataset success.

The two assistant responses reported 1333 and 2068 tokens. Their sum, 3401, matches the task cost, decision cost, evidence usage, and settled model-usage ledger entry. The raw session trace remains local; this report does not reproduce hidden model reasoning.

Persisted evidence:

- `data/pi-validation/r7-real-20260922/data/commerce/pi_evidence/job_1c0429db084d4e5a__attempt1.json`
- `data/pi-validation/r7-real-20260922/data/commerce/model_usage.json`
- `data/pi-validation/r7-real-20260922/live-hashes.json`
- `data/pi-validation/r7-real-20260922/worker.log`

After exit, the validation port was closed and the process audit found no worker/Pi process matching this run directory or session. No credentials were printed, changed, or copied into the report.

## 5. Regression Evidence

- Reconciliation API: 17/17.
- Pi runtime: 9/9, including three red-to-green regressions.
- Isolated runner: 6/6; latest focused run 5.188s.
- Full isolated suite before the real run: 1412/1412, 65.565s, exit 0.
- Full isolated suite after the real run: 1412/1412, 63.202s, exit 0.
- Integrated suite including the concurrently added browser tests: 1422/1422, 78.920s, exit 0.
- Closing integrated suite: **1424/1424, 89.498s, exit 0**. The 17 monitored backend, CLI, test and configuration files retained identical before/after hashes. The live file set and hashes still matched the start of this batch.
- `git diff --check` and Python compilation passed for the new runner, entry point, runtime, and reconciliation module.

Concurrent-change qualification: a new UI route appeared during the second 1412-test run; two test files changed during the 1422-test run. Before the closing run, a fresh 22-file hash baseline was captured. Twenty files were unchanged afterward; `web/style.css` and `tests/test_pi_usage_ui.py` changed during that run. The entire worktree therefore was not frozen. The backend/CLI validation scope was stable, but this report does not close the separate UI's final visual or current-snapshot acceptance. Its code is preserved and is not the implementation contribution of this validation batch.

Exact monitored hashes, changed paths, real-artifact hashes and closing results are recorded in [validation manifest](CODEX_R7_ISOLATED_PI_VALIDATION_MANIFEST_2026-09-22.json). Earlier status entries and test counts remain historical records, not contradictory claims about this later validation.

Existing WP08 tests emit mock `REAL_RUN_OK`/expected failure messages and ResourceWarnings. Those fixture messages are not counted as the actual real Pi run above.

## 6. Repeatable Commands

Use a different, nonexistent directory on every invocation. Offline mode makes no real model request:

```powershell
.\.venv\Scripts\python.exe scripts/run_pi_quote_review_real.py --data-dir data/pi-validation/my-new-offline-run
```

A deliberate real rerun needs the existing protected provider configuration and explicit opt-in:

```powershell
.\.venv\Scripts\python.exe scripts/run_pi_quote_review_real.py --data-dir data/pi-validation/my-new-real-run --mode real --allow-model
```

Inspect `validation.json`, not only the exit code. A failed/unknown run must not be blindly rerun or manually marked zero. Preserve its attempt ledger and evidence first. Do not reuse a previous directory or point this command at live data.

## 7. Remaining Gates and Next Work

1. Finish independent acceptance of the concurrently authored operator reconciliation UI: preview/confirmation, stale proposal rejection, response-loss idempotency, credential handling, and desktop/mobile inspection. Keep real reconciliation disabled until an operator has evidence to resolve a specific unknown attempt.
2. Prepare real-supplier intake and a read-only trial checklist. Require source identity, quote time/validity, SKU/specification, price, currency, freight, MOQ, stock and after-sales terms; represent missing fields as unknown. Do not relabel these demo quotes as real.
3. Validate a real quote batch through committed import, matching, deterministic cost calculation and opportunity qualification; let Pi produce cited recommendations and show them in the decision panel. Account/platform authorization and material commitments remain human decisions.
4. Run the existing shadow process against real observations and evaluate its established duration/sample gates. Only then assess whether bounded execution deserves a separate release decision.

Boundaries: single-process JSON consistency, not a multi-process database guarantee; this run is not a power-loss test. The 20000-token acceptance budget and 120-second request timeout are not a hard upstream spending ceiling. The task/turn counts come from retained Pi messages and the local attempt ledger, not a provider billing export. No real supplier verification, sale, realized profit, platform write, or automatic-trading release was established by this batch.
