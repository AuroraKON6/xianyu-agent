"""R10 acceptance tests: PostgreSQL contract, migrations and UoW.

The PG-backed cases run ONLY when XIANYU_PG_TEST_ADMIN_DSN (and
XIANYU_PG_TEST_APP_DSN for the role matrix) are provided — an isolated
PostgreSQL instance, per the taskbook's "sqlite tests never substitute
for PG acceptance" rule. Under the offline isolation entry the DSN must
additionally sit in the XIANYU_PG_TEST_ALLOWLIST exact-match list.

The non-PG cases below (migration file contract, lock order) always run.
"""
from __future__ import annotations

import os
import unittest
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

ADMIN_DSN = os.environ.get("XIANYU_PG_TEST_ADMIN_DSN", "")
APP_DSN = os.environ.get("XIANYU_PG_TEST_APP_DSN", "")


class MigrationFileContractTests(unittest.TestCase):
    def test_every_migration_has_down_and_valid_name(self):
        from xianyu_agent.persistence.postgres.migrator import discover

        migrations = discover(MIGRATIONS_DIR)
        self.assertTrue(migrations, "no migrations discovered")
        for entry in migrations:
            self.assertTrue(entry["version"].isdigit())
            self.assertTrue(Path(entry["down_path"]).is_file())
            self.assertEqual(len(entry["checksum"]), 16)

    def test_lock_order_covers_all_core_domains(self):
        from xianyu_agent.persistence.unit_of_work import LOCK_ORDER

        self.assertEqual(len(LOCK_ORDER), len(set(LOCK_ORDER)))
        for domain in ("accounts", "sales_orders", "ledger_entries", "outbox"):
            self.assertIn(domain, LOCK_ORDER)


@unittest.skipUnless(ADMIN_DSN, "XIANYU_PG_TEST_ADMIN_DSN not provided")
class PostgresAcceptanceTests(unittest.TestCase):
    """Real-PG acceptance against an isolated instance."""

    @classmethod
    def setUpClass(cls):
        from xianyu_agent.persistence.postgres.connection import connect
        from xianyu_agent.persistence.postgres.migrator import MigrationRunner

        cls.admin = connect(ADMIN_DSN)
        cls.runner = MigrationRunner(cls.admin, MIGRATIONS_DIR)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.rollback()
        finally:
            cls.admin.close()

    def setUp(self):
        self.admin.rollback()
        # each test starts from the schema state left by 0001 (idempotent)
        self.runner.upgrade()

    def tearDown(self):
        self.admin.rollback()

    def _wipe_business_rows(self):
        with self.admin.cursor() as cursor:
            cursor.execute(
                "TRUNCATE outbox, ledger_entries, inventory_locks, budget_reservations,"
                " procurement_orders, sales_orders, execution_jobs, work_orders,"
                " agent_jobs, decision_records, opportunities, product_matches,"
                " offer_snapshots, accounts RESTART IDENTITY CASCADE"
            )
        self.admin.commit()

    # ---- create / upgrade / rollback compatibility --------------------

    def test_fresh_create_then_upgrade_is_idempotent(self):
        self._wipe_business_rows()
        state = self.runner.status()
        self.assertEqual(state["pending"], [], "schema already at head in setUp")
        applied_again = self.runner.upgrade()
        self.assertEqual(applied_again, [], "second upgrade applies nothing")

    def test_downgrade_then_upgrade_roundtrip(self):
        self._wipe_business_rows()
        removed = self.runner.downgrade(to_version="")
        self.assertEqual(removed, ["0001"])
        with self.admin.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM information_schema.tables"
                " WHERE table_schema='public' AND table_name='sales_orders'"
            )
            self.assertEqual(cursor.fetchone()[0], 0, "downgrade must drop the schema")
        reapplied = self.runner.upgrade()
        self.assertEqual(reapplied, ["0001"])
        # data written before downgrade survives nothing here ( wiped );
        # compatibility = schema returns and reaccepts writes
        with self.admin.cursor() as cursor:
            cursor.execute(
                "INSERT INTO sales_orders (record_id, account_id, channel, external_id, status, payload)"
                " VALUES ('so-rt', 'acc-1', 'CH', 'ext-1', 'new', '{}')"
            )
        self.admin.commit()

    # ---- constraint violations are rejected by the database ----------------

    def test_cross_account_same_external_order_id_coexist(self):
        self._wipe_business_rows()
        with self.admin.cursor() as cursor:
            for account in ("acc-1", "acc-2"):
                cursor.execute(
                    "INSERT INTO sales_orders (record_id, account_id, channel, external_id, status, payload)"
                    " VALUES (%s, %s, 'CH', 'ext-dup', 'new', '{}')",
                    ("so-" + account, account),
                )
        self.admin.commit()  # composite key: both rows accepted

    def test_duplicate_composite_identity_rejected(self):
        self._wipe_business_rows()
        with self.admin.cursor() as cursor:
            cursor.execute(
                "INSERT INTO sales_orders (record_id, account_id, channel, external_id, status, payload)"
                " VALUES ('so-1', 'acc-1', 'CH', 'ext-1', 'new', '{}')"
            )
        self.admin.commit()
        with self.assertRaises(Exception) as ctx:
            with self.admin.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO sales_orders (record_id, account_id, channel, external_id, status, payload)"
                    " VALUES ('so-2', 'acc-1', 'CH', 'ext-1', 'new', '{}')"
                )
        self.assertIn("unique", str(ctx.exception).lower())
        self.admin.rollback()

    def test_foreign_key_violation_rejected(self):
        self._wipe_business_rows()
        with self.assertRaises(Exception) as ctx:
            with self.admin.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO decision_records (record_id, opportunity_id, payload)"
                    " VALUES ('dec-x', 'no-such-opportunity', '{}')"
                )
        self.assertIn("foreign key", str(ctx.exception).lower())
        self.admin.rollback()

    def test_ledger_duplicate_source_event_rejected(self):
        self._wipe_business_rows()
        with self.admin.cursor() as cursor:
            cursor.execute(
                "INSERT INTO ledger_entries (entry_id, entry_type, direction, account_id,"
                " amount_state, amount, currency, source_event_id, payload)"
                " VALUES ('le-1', 'settlement', 'credit', 'acc-1', 'known', 100, 'CNY', 'evt-1', '{}')"
            )
        self.admin.commit()
        with self.assertRaises(Exception) as ctx:
            with self.admin.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO ledger_entries (entry_id, entry_type, direction, account_id,"
                    " amount_state, amount, currency, source_event_id, payload)"
                    " VALUES ('le-2', 'settlement', 'credit', 'acc-1', 'known', 100, 'CNY', 'evt-1', '{}')"
                )
        self.assertIn("duplicate", str(ctx.exception).lower())
        self.admin.rollback()

    def test_numeric_money_roundtrip_exact(self):
        self._wipe_business_rows()
        exact = Decimal("123.456789")
        with self.admin.cursor() as cursor:
            cursor.execute(
                "INSERT INTO ledger_entries (entry_id, entry_type, direction, account_id,"
                " amount_state, amount, currency, source_event_id, payload)"
                " VALUES ('le-n', 'sale', 'credit', 'acc-1', 'known', %s, 'CNY', 'evt-n', '{}')",
                (exact,),
            )
            cursor.execute("SELECT amount FROM ledger_entries WHERE entry_id = 'le-n'")
            stored = cursor.fetchone()[0]
        self.admin.rollback()
        self.assertEqual(Decimal(stored), exact)

    # ---- UoW: atomicity, locks, outbox -----------------------------

    def test_uow_rollback_hides_partial_writes(self):
        from xianyu_agent.persistence.unit_of_work import UnitOfWork

        self._wipe_business_rows()
        uow = UnitOfWork(self.admin).begin()
        uow.lock("accounts:acc-1")
        with uow._conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO sales_orders (record_id, account_id, channel, external_id, status, payload)"
                " VALUES ('so-u1', 'acc-1', 'CH', 'ext-u1', 'new', '{}')"
            )
        uow.write_outbox(intent_key="intent-u1", account_id="acc-1")
        uow.rollback()  # simulate failure before commit
        with self.admin.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM sales_orders WHERE record_id = 'so-u1'")
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM outbox WHERE intent_key = 'intent-u1'")
            self.assertEqual(cursor.fetchone()[0], 0, "outbox row must vanish with the rollback")

    def test_uow_outbox_deduplicates_intent(self):
        from xianyu_agent.persistence.unit_of_work import UnitOfWork

        self._wipe_business_rows()
        uow = UnitOfWork(self.admin).begin()
        first = uow.write_outbox(intent_key="intent-dup", account_id="acc-1")
        second = uow.write_outbox(intent_key="intent-dup", account_id="acc-1")
        uow.commit()
        self.assertTrue(first)
        self.assertEqual(second, "", "duplicate intent returns empty, no second row")

    def test_lock_domain_validation(self):
        from xianyu_agent.persistence.unit_of_work import UnitOfWork

        uow = UnitOfWork(self.admin).begin()
        with self.assertRaises(ValueError):
            uow.lock("not_a_domain:acc-1")
        uow.rollback()

    # ---- repositories over the same contract ---------------------------

    def test_offer_and_opportunity_repositories_over_pg(self):
        from datetime import datetime, timezone

        from xianyu_agent.commerce.contracts import build_offer_snapshot
        from xianyu_agent.persistence.postgres.repositories import (
            PostgresOfferSnapshotRepository,
            PostgresOpportunityRepository,
        )
        from xianyu_agent.persistence.unit_of_work import UnitOfWork
        from quote_fixtures import with_declared_quote_provenance

        self._wipe_business_rows()
        uow = UnitOfWork(self.admin).begin()
        offers = PostgresOfferSnapshotRepository(uow)
        opportunities = PostgresOpportunityRepository(uow)
        snapshot = with_declared_quote_provenance(build_offer_snapshot(
            provider_id="supplier_file:PG测试",
            source_item_id="PG-SKU-1",
            source_sku_id="PG-SKU-1",
            observed_at=datetime.now(timezone.utc),
            attributes={"name": "工作台"},
            condition="全新", unit="件", price_type="supplier_confirmed",
            eligible_price={"state": "known", "amount": "160", "currency": "CNY"},
            shipping_quote={"state": "known", "amount": "15", "currency": "CNY"},
            stock_state="unknown",
            payload_hash="pg-acc-1", account_id="", source_ref="test",
        ))
        stored, created = offers.add(snapshot)
        self.assertTrue(created)
        duplicate, created_again = offers.add(snapshot)
        self.assertFalse(created_again)
        self.assertEqual(duplicate["record_id"], stored["record_id"])
        fetched = offers.get(stored["record_id"])
        self.assertEqual(fetched["eligible_price"]["amount"], "160")

        record = opportunities.create({
            "record_type": "opportunity", "record_id": "opp-pg-1",
            "canonical_sku": "PG-SKU-1", "source": "resale_candidate",
            "status": "discovered", "created_at": datetime.now(timezone.utc).isoformat(),
        })
        existing = opportunities.find_by_natural_key("PG-SKU-1", "resale_candidate")
        self.assertEqual(existing["record_id"], "opp-pg-1")
        moved = opportunities.transition(
            record["record_id"], to_status="researching", reason="test",
            expected_revision=1,
        )
        self.assertEqual(moved["status"], "researching")
        self.assertEqual(moved["revision"], 2)
        with self.assertRaises(Exception):
            opportunities.transition(
                record["record_id"], to_status="paused", reason="stale",
                expected_revision=1,  # conflict: revision is already 2
            )
        uow.commit()

    # ---- role isolation matrix -----------------------------

    @unittest.skipUnless(APP_DSN, "XIANYU_PG_TEST_APP_DSN not provided")
    def test_app_role_cannot_ddl_and_cannot_edit_ledger(self):
        from xianyu_agent.persistence.postgres.connection import connect

        app = connect(APP_DSN)
        try:
            with app.cursor() as cursor:
                cursor.execute(
                    "CREATE TABLE rogue_table (id INT)"
                )
            self.fail("app role must not be able to run DDL")
        except Exception as exc:
            self.assertIn("permission", str(exc).lower())
        finally:
            app.rollback()
            app.close()

        app = connect(APP_DSN)
        try:
            with app.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO ledger_entries (entry_id, entry_type, direction, account_id,"
                    " amount_state, amount, currency, source_event_id, payload)"
                    " VALUES ('le-app', 'sale', 'credit', 'acc-1', 'known', 5, 'CNY', 'evt-app', '{}')"
                )
            app.commit()
            with app.cursor() as cursor:
                cursor.execute("UPDATE ledger_entries SET amount = 999 WHERE entry_id = 'le-app'")
            self.fail("app role must not be able to edit ledger history")
        except Exception as exc:
            self.assertIn("permission", str(exc).lower())
        finally:
            try:
                app.rollback()
                app.close()
            except Exception:
                pass
        # cleanup the appended test row as admin (append itself succeeded)
        with self.admin.cursor() as cursor:
            cursor.execute("DELETE FROM ledger_entries WHERE entry_id = 'le-app'")
        self.admin.commit()


@unittest.skipUnless(
    os.environ.get("XIANYU_TEST_ISOLATION", "") == "1" and ADMIN_DSN,
    "needs isolation entry plus an allowlisted test DSN",
)
class TestDsnGuardTests(unittest.TestCase):
    def test_isolation_mode_requires_exact_allowlist_entry(self):
        from xianyu_agent.persistence.postgres.connection import (
            PersistenceError,
            ensure_test_dsn_allowed,
        )

        os.environ.setdefault("XIANYU_PG_TEST_ALLOWLIST", ADMIN_DSN)
        try:
            ensure_test_dsn_allowed(ADMIN_DSN)  # exact match passes
            with self.assertRaises(PersistenceError):
                ensure_test_dsn_allowed("postgresql://user@localhost/xianyu_prod")
        finally:
            pass


if __name__ == "__main__":
    unittest.main()
