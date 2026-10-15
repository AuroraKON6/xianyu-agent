"""Bounded offline combination recheck; no business imports or live data.

Only the three storage scripts are imported. All fixture writes, hard links,
and corruption injections stay inside a TemporaryDirectory. SQLite below is
an explicit immediate-FK witness, NOT a PostgreSQL acceptance run.
Exit: 0 = no reproduced gaps; 2 = reproduced gaps; 1 = harness error.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATHS = {
    "audit": ROOT / "scripts" / "audit_commerce_ledgers.py",
    "export": ROOT / "scripts" / "export_commerce_to_sql.py",
    "backup": ROOT / "scripts" / "commerce_backup_drill.py",
}


def deny_external_activity(event, args):
    if event.startswith("socket.") or event in (
        "subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn",
    ):
        raise RuntimeError("external activity forbidden in offline recheck: " + event)


sys.addaudithook(deny_external_activity)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    return {str(path.relative_to(ROOT)): digest(path) for path in SCRIPT_PATHS.values()}


def tree_hashes(directory):
    return {
        str(path.relative_to(directory)): digest(path)
        for path in directory.rglob("*") if path.is_file()
    }


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def money(amount, currency="CNY"):
    return {"state": "known", "amount": str(amount), "currency": currency}


def ledger_row(identifier, amount, currency="CNY", direction="credit", **extra):
    return dict(record_id=identifier, record_type="ledger_entry", direction=direction,
                amount=money(amount, currency), **extra)


def cli(module, arguments):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = module.main(arguments)
    return code, json.loads(output.getvalue())


def seed(data):
    commerce = data / "commerce"
    offers = [dict(record_id="offer-" + side, provider_id="provider-" + side,
                   source_item_id=side, source_sku_id="sku", observed_at="2026-10-13",
                   payload_hash=side) for side in ("left", "right")]
    write_json(commerce / "offers.json", offers)
    write_json(commerce / "matches.json", [dict(record_id="match-1",
               left_snapshot_id="offer-left", right_snapshot_id="offer-right")])
    write_json(commerce / "opportunities.json", [dict(record_id="opp-1", match_id="match-1",
               offer_snapshot_ids=["offer-left", "offer-right"], market_snapshot_id="offer-left",
               canonical_sku="sku", source="fixture")])
    write_json(commerce / "agent_jobs.json", [dict(record_id="agent-1", status="completed",
               idempotency_key="agent-key")])
    write_json(commerce / "intake_confirmations.json", [dict(record_id="confirm-1",
               idempotency_key="confirm-key", snapshot_ids=["offer-right"])])
    write_json(commerce / "radar_events.json", [dict(record_id="radar-1",
               idempotency_key="radar-key")])
    write_json(commerce / "opportunity_adoptions.json", [dict(record_id="adopt-1",
               idempotency_key="adopt-key", opportunity_id="opp-1")])
    write_json(commerce / "procurement_orders.json", [dict(record_id="proc-1",
               account_id="acct", sales_order_ref="sale-1", status="received")])
    write_json(commerce / "budget_reservations.json", [dict(record_id="budget-1",
               record_type="budget_reservation", account_id="acct", job_id="proc-1",
               budget_bucket="procurement", status="settled", amount=money("100"),
               actual_amount=money("140"), business_day="2026-10-13")])
    write_json(commerce / "ledger.json", [ledger_row("cny-credit", "100.25"),
               ledger_row("cny-debit", "40.10", direction="debit"),
               ledger_row("usd-credit", "9", "USD"),
               ledger_row("eur-debit", "2", "EUR", "debit")])
    write_json(data / "execution_jobs.json", [dict(job_id="exec-1", status="completed",
               idempotency_key="exec-key", approval_id="approval-1", work_order_id="work-1",
               action="fixture_only", created_at=1.0, updated_at=2.0)])
    blob = b"offline evidence fixture"
    sha = hashlib.sha256(blob).hexdigest()
    key = "blobs/" + sha[:2] + "/" + sha + ".bin"
    blob_path = data / "candidate_evidence" / key
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(blob)
    write_json(data / "candidate_evidence" / "index.json", {"artifacts": [
        dict(artifact_id="artifact-1", sha256=sha, blob_key=key),
    ]})


def mapping_currency_ddl_control(base, modules):
    data = base / "mapping" / "data"
    seed(data)
    # Conflicting locations must not replace the real budget/execution authority.
    write_json(data / "budget_reservations.json", [dict(record_id="wrong-budget")])
    write_json(data / "commerce" / "execution_jobs.json", [dict(job_id="wrong-execution")])
    before = tree_hashes(data)
    audit_code, report = cli(modules["audit"], ["--data-dir", str(data)])
    export_code, footer = cli(modules["export"], ["--data-dir", str(data),
                             "--out", str(data.parent / "export.sql")])
    backup_code, restored = cli(modules["backup"], ["--source", str(data),
                              "--work", str(data.parent / "work")])
    sql = (data.parent / "export.sql").read_text(encoding="utf-8")
    manifest = json.loads((data.parent / "work" / "backup" /
                          "_backup_manifest.json").read_text(encoding="utf-8"))
    require((audit_code, export_code, backup_code) == (0, 0, 0), "combined control CLI failed")
    require(report["files"]["budget_reservations.json"]["rows"] == 1, "budget authority mismatch")
    require(report["files"]["execution_jobs.json"]["rows"] == 1, "execution authority mismatch")
    require("wrong-budget" not in sql and "wrong-execution" not in sql, "wrong authority exported")
    require(footer["row_counts"]["budget_reservations"] == 1 and
            footer["row_counts"]["execution_jobs"] == 1, "authority row count mismatch")
    require("actual_amount" in sql and '"140"' in sql, "settled budget payload lost")
    expected_money = {"CNY": "60.15", "EUR": "-2", "USD": "9"}
    require(report["files"]["ledger.json"]["money_net"] == expected_money, "audit mixed currencies")
    require(footer["ledger_cny_net"] == "60.15" and
            footer["ledger_non_cny_currencies"] == ["EUR", "USD"], "export mixed currencies")
    covered = ["commerce/budget_reservations.json", "execution_jobs.json",
               "candidate_evidence/index.json"]
    normalized = {Path(key).as_posix() for key in manifest}
    require(all(key in normalized for key in covered), "authority/evidence missing in manifest")
    require(before == tree_hashes(data), "disjoint workflow mutated authority")
    require(before == tree_hashes(data.parent / "work" / "restored"), "control round-trip changed bytes")
    require("record_id     TEXT PRIMARY KEY" in sql and
            "job_id           TEXT PRIMARY KEY" in sql, "PK DDL missing")
    indexes = [line for line in sql.splitlines() if line.startswith("CREATE UNIQUE INDEX")]
    require(all(any(name in line for line in indexes) for name in (
        "agent_jobs_idem", "radar_events_idem", "opportunity_adoptions_idem",
        "intake_confirmations_idem",
    )), "expected idempotency index DDL missing")
    return dict(status="PASS", scope="offline byte round-trip and emitted DDL text only",
                cli_exit_codes=[audit_code, export_code, backup_code], audit_money=expected_money,
                export_cny_net=footer["ledger_cny_net"], non_cny=footer["ledger_non_cny_currencies"],
                authoritative_manifest_paths=covered, source_unchanged=True,
                idempotency_index_ddl=indexes,
                not_verified=["PostgreSQL execution", "every domain's idempotency semantics",
                              "budget target foreign keys", "execution UNKNOWN classification"])


def reversal_load_order(base, modules):
    data = base / "reversal" / "data"
    original = ledger_row("original", "100")
    reversal = ledger_row("reversal", "25", direction="debit", reversal_of="original")
    results = {}
    for label, rows in (("reversal_first", [reversal, original]),
                        ("original_first", [original, reversal])):
        write_json(data / "commerce" / "ledger.json", rows)
        audit_code, report = cli(modules["audit"], ["--data-dir", str(data)])
        exported = modules["export"].export(data)
        statements = [line for line in exported["sql"].splitlines()
                      if line.startswith("INSERT INTO ledger ")]
        require(audit_code == 0 and report["orphan_references"] == 0, "auditor rejected valid links")
        require("DEFERRABLE" not in modules["export"].LEDGER_DDL.upper(), "FK changed to deferred")
        db = sqlite3.connect(":memory:")
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.executescript(modules["export"].LEDGER_DDL)
            error = None
            for statement in statements:
                # Controlled fixtures contain no cast token inside payload strings.
                require(statement.count("::jsonb") == 1, "unexpected fixture SQL cast count")
                try:
                    db.execute(statement.replace("::jsonb", ""))
                except sqlite3.IntegrityError as exc:
                    error = str(exc)
                    break
            results[label] = dict(audit_exit=audit_code, cny_net=exported["footer"]["ledger_cny_net"],
                                  emitted_record_id_order=[row["record_id"] for row in rows],
                                  sqlite_immediate_fk_witness_error=error,
                                  sqlite_rows_inserted=db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0])
        finally:
            db.close()
    require(results["reversal_first"]["sqlite_immediate_fk_witness_error"] ==
            "FOREIGN KEY constraint failed", "immediate-FK ordering gap not reproduced")
    require(results["original_first"]["sqlite_rows_inserted"] == 2, "parent-first control failed")
    return dict(status="CONFIRMED_GAP", finding="generated immediate FK + unsorted ledger inserts",
                source_lines={"scripts/export_commerce_to_sql.py": [60, 116, 132, 135],
                              "scripts/audit_commerce_ledgers.py": [201, 203]},
                variants=results, postgresql_status="NOT_RUN",
                boundary="SQLite is only an immediate-FK witness; no claim of PostgreSQL runtime success/failure")


def recovery_hash_boundary(base, modules):
    results = {}
    for mode in ("unlisted_restored_files", "covered_file_first_restore_control", "covered_file_final_restore"):
        data = base / mode / "data"
        work = data.parent / "work"
        seed(data)
        write_json(data / "commerce" / "shadow_authorizations.json", [dict(record_id="shadow-1")])
        before = tree_hashes(data)
        restore_count = [0]
        original_copytree = modules["backup"].shutil.copytree

        def injected_copytree(src, dst, *args, **kwargs):
            result = original_copytree(src, dst, *args, **kwargs)
            if Path(dst).resolve() == (work / "restored").resolve():
                restore_count[0] += 1
                if mode == "unlisted_restored_files":
                    write_json(Path(dst) / "commerce" / "shadow_authorizations.json",
                               [dict(record_id="corrupted-shadow")])
                if ((mode == "covered_file_first_restore_control" and restore_count[0] == 1)
                        or (mode == "covered_file_final_restore" and restore_count[0] == 2)):
                    write_json(Path(dst) / "commerce" / "budget_reservations.json",
                               [dict(record_id="corrupted-budget", amount=money("99999"))])
            return result

        with patch.object(modules["backup"].shutil, "copytree", side_effect=injected_copytree):
            code, report = cli(modules["backup"], ["--source", str(data), "--work", str(work)])
        manifest = json.loads((work / "backup" / "_backup_manifest.json").read_text(encoding="utf-8"))
        final_mismatches = [Path(name).as_posix() for name, expected in manifest.items()
                            if not (work / "restored" / name).exists()
                            or digest(work / "restored" / name) != expected]
        shadow_relative = "commerce/shadow_authorizations.json"
        require(before == tree_hashes(data), "fault injector changed source")
        require(report["phases"]["divergence"]["lost_rows"] == 1, "final blind restore did not run")
        shadow_changed = digest(work / "restored" / shadow_relative) != digest(data / shadow_relative)
        results[mode] = dict(cli_exit=code, reported_hash_mismatches=report["phases"]["restore"]["hash_mismatches"],
                             final_manifest_mismatches=final_mismatches, restore_copy_count=restore_count[0],
                             shadow_in_manifest=shadow_relative in {Path(key).as_posix() for key in manifest},
                             shadow_final_bytes_changed=shadow_changed, source_unchanged=True)
    require(results["unlisted_restored_files"]["cli_exit"] == 0 and
            results["unlisted_restored_files"]["shadow_final_bytes_changed"] and
            not results["unlisted_restored_files"]["shadow_in_manifest"], "unlisted-file gap not reproduced")
    require(results["covered_file_first_restore_control"]["cli_exit"] == 2, "tracked corruption control not caught")
    final = results["covered_file_final_restore"]
    require(final["cli_exit"] == 0 and final["reported_hash_mismatches"] == [] and
            final["final_manifest_mismatches"] == ["commerce/budget_reservations.json"],
            "final restored-copy verification gap not reproduced")
    return dict(status="CONFIRMED_GAP", finding="hash report does not cover complete final restored tree",
                source_lines={"scripts/commerce_backup_drill.py": [49, 84, 98, 102, 112, 137, 138, 180]},
                injection="copytree wrapper, only temporary destinations; real main()/drill()/hash checks run",
                variants=results)


def hardlink_guard(base, modules):
    import os

    data = base / "hardlink" / "data"
    authority = data / "commerce" / "ledger.json"
    output = data.parent / "outside.sql"
    write_json(authority, [ledger_row("entry-1", "12")])
    try:
        os.link(str(authority), str(output))
    except OSError as exc:
        return dict(status="NOT_RUN", reason="temporary hardlink creation unavailable", error=str(exc))
    before = digest(authority)
    same_file = authority.samefile(output)
    require(same_file and data.resolve() not in output.resolve().parents, "hardlink fixture invalid")
    code, footer = cli(modules["export"], ["--data-dir", str(data), "--out", str(output)])
    after = digest(authority)
    now_sql = authority.read_text(encoding="utf-8").startswith("-- generated by scripts/export_commerce_to_sql.py")
    require(code == 0 and before != after and now_sql, "hardlink guard bypass not reproduced")
    return dict(status="CONFIRMED_GAP", finding="outside output hardlink overwrites authoritative JSON",
                source_lines={"scripts/export_commerce_to_sql.py": [213, 214, 220, 225]},
                cli_exit=code, distinct_resolved_paths=True, same_file_before_write=same_file,
                authority_sha256_before=before, authority_sha256_after=after,
                authority_now_contains_sql=now_sql, exported_ledger_rows=footer["row_counts"]["ledger"],
                boundary="existing local hardlink; no symlink, network, platform, or live-data access")


def main():
    before = source_hashes()
    modules = {}
    for name, path in SCRIPT_PATHS.items():
        spec = importlib.util.spec_from_file_location("offline_storage_" + name, str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules[name] = module
    cases = []
    with tempfile.TemporaryDirectory(prefix="codex-storage-combinations-") as temporary:
        base = Path(temporary).resolve()
        for name, function in (
            ("authority_currency_ddl_control", mapping_currency_ddl_control),
            ("ledger_fk_load_order", reversal_load_order),
            ("recovery_final_hash_coverage", recovery_hash_boundary),
            ("output_hardlink_authority_guard", hardlink_guard),
        ):
            try:
                result = function(base, modules)
                cases.append(dict(case=name, **result))
            except Exception as exc:
                cases.append(dict(case=name, status="HARNESS_ERROR", error=repr(exc)))
    after = source_hashes()
    report = dict(scope="new offline combinations only; original repros/full suite NOT_RUN",
                  cases=cases, summary={status: sum(case["status"] == status for case in cases)
                  for status in ("PASS", "CONFIRMED_GAP", "NOT_RUN", "HARNESS_ERROR")},
                  source_sha256_before=before, source_sha256_after=after,
                  reviewed_source_unchanged=before == after,
                  restrictions="stdlib; no subprocess/socket/model/platform/server; temporary fixtures removed",
                  postgresql_status="NOT_RUN")
    print(json.dumps(report, indent=2))
    if before != after or report["summary"]["HARNESS_ERROR"]:
        return 1
    return 2 if report["summary"]["CONFIRMED_GAP"] else 0


if __name__ == "__main__":
    sys.exit(main())
