-- 0001_core_write_domains.down.sql (taskbook R10 rollback)
-- Reverse of the up migration. Rollback compatibility rule: the running
-- application version that shipped 0001 must be stopped before applying
-- this; the JSON stores remain the recovery source of truth.

DROP TABLE IF EXISTS ledger_entries;
DROP TABLE IF EXISTS inventory_locks;
DROP TABLE IF EXISTS budget_reservations;
DROP TABLE IF EXISTS procurement_orders;
DROP TABLE IF EXISTS sales_orders;
DROP TABLE IF EXISTS outbox;
DROP TABLE IF EXISTS execution_jobs;
DROP TABLE IF EXISTS work_orders;
DROP TABLE IF EXISTS agent_jobs;
DROP TABLE IF EXISTS decision_records;
DROP TABLE IF EXISTS opportunities;
DROP TABLE IF EXISTS product_matches;
DROP TABLE IF EXISTS offer_snapshots;
DROP TABLE IF EXISTS accounts;
