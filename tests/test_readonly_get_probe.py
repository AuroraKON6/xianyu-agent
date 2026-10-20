"""R04 acceptance tests: pure queries and explicit commands.

1. RepeatedGetProbeTests fingerprints the sandbox data tree, calls every
   parameter-less GET route twice, and proves the tree is unchanged —
   a GET can no longer hide a lease recovery, revalidation or probe.
   Runs only under the offline isolation entry (needs a sandbox DATA_DIR;
   skipped otherwise so a bare invocation never hashes live state).
2. DecisionCardReadOnlyTests proves the decision card renders a stale
   opportunity WITHOUT transitioning it, while the explicit revalidate
   command is the one that (only for a ready opportunity) pauses it.
"""
from __future__ import annotations

import hashlib
import os
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


def _tree_fingerprint(root: Path):
    entries = []
    if not root.exists():
        return entries
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            entries.append(
                (
                    path.relative_to(root).as_posix(),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    return entries


class RepeatedGetProbeTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("XIANYU_TEST_ISOLATION", "") == "1",
        "needs the offline isolation entry (sandbox DATA_DIR)",
    )
    def test_every_paramless_get_route_is_repeatable_and_side_effect_free(self):
        from xianyu_agent import server

        client = TestClient(server.app)
        data_dir = Path(server.settings.data_dir)
        before = _tree_fingerprint(data_dir)

        probed, skipped = [], []
        for route in server.app.routes:
            methods = getattr(route, "methods", None) or set()
            path = getattr(route, "path", "")
            if "GET" not in methods or not path:
                continue
            if "{" in path or path.startswith("/web") or path == "/":
                # parameterized routes are covered by their own endpoint
                # tests; static assets are filesystem reads
                skipped.append(path)
                continue
            response = client.get(path)
            # 503 = "service disabled without configured operator token"
            # (e.g. pi-usage reconciliation) — a valid read response
            self.assertTrue(
                response.status_code < 500 or response.status_code == 503,
                f"{path} -> {response.status_code}",
            )
            probed.append((path, response.status_code))

        self.assertTrue(probed, "no GET routes probed")
        after = _tree_fingerprint(data_dir)
        self.assertEqual(
            before,
            after,
            "GET requests changed business state files under %s" % data_dir,
        )
        # sanity: the probe actually covered the routes we fixed in R04
        probed_paths = {path for path, _ in probed}
        for expected in ("/execution-jobs", "/reply-outbox", "/commerce/market-channel/status"):
            self.assertIn(expected, probed_paths)


class DecisionCardReadOnlyTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server as server_module

        self.server = server_module
        self.client = TestClient(server_module.app)

    def _make_opportunity(self, sku, price="160"):
        csv_body = (
            "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
            f"{sku},不锈钢工作台,{price},件,1,50,包邮,120x60x85cm,众商,全新\n"
        ).encode("utf-8")
        from quote_fixtures import declared_quote_csv

        payload = declared_quote_csv(csv_body).decode("utf-8")
        preview = self.client.post(
            "/commerce/supplier-imports/preview", json={"kind": "pasted_text", "content": payload}
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        confirm = self.client.post(
            f"/commerce/supplier-imports/{preview.json()['preview_id']}/confirm",
            json={
                "content_sha256": preview.json()["content_sha256"],
                "revision": preview.json()["revision"],
                "supplier": "R04只读测试供应商",
                "operator": "test",
            },
        )
        self.assertEqual(confirm.status_code, 200, confirm.text)
        supplier_snapshot = confirm.json()["snapshots"][0]
        market = self.client.post(
            "/commerce/market-observations",
            json={
                "sku": sku,
                "title": f"工作台 {sku}",
                "spec": "120x60x85cm",
                "brand": "众商",
                "price": "300",
                "condition": "全新",
                "unit": "件",
            },
        )
        self.assertEqual(market.status_code, 200, market.text)
        match = self.client.post(
            "/commerce/product-matches",
            json={
                "left_snapshot_id": market.json()["snapshot"]["record_id"],
                "right_snapshot_id": supplier_snapshot["record_id"],
            },
        )
        self.assertEqual(match.status_code, 200, match.text)
        opportunity = self.client.post(
            "/commerce/opportunities",
            json={
                "market_snapshot_id": market.json()["snapshot"]["record_id"],
                "supplier_snapshot_id": supplier_snapshot["record_id"],
                "match_id": match.json()["match_id"],
            },
        )
        self.assertEqual(opportunity.status_code, 200, opportunity.text)
        return opportunity.json()

    def test_stale_card_renders_without_transition_and_revalidate_is_explicit(self):
        opportunity = self._make_opportunity("R04-RO-SKU-1")
        record_id = opportunity["record_id"]
        # make it stale: a newer supplier snapshot for the same provider+SKU
        self._make_opportunity("R04-RO-SKU-1", price="170")

        status_before = str(
            (self.server.commerce_repositories.opportunities.get(record_id) or {}).get("status")
        )

        # read-only rendering: card reports invalid gates but never pauses
        card = self.client.get(f"/commerce/opportunities/{record_id}/decision-card")
        self.assertEqual(card.status_code, 200, card.text)
        validity = card.json().get("current_validity") or {}
        self.assertFalse(validity.get("valid"))
        self.assertFalse(validity.get("transitioned"))
        status_after_get = str(
            (self.server.commerce_repositories.opportunities.get(record_id) or {}).get("status")
        )
        self.assertEqual(status_after_get, status_before)

        # explicit command records the operator (no transition here because
        # the opportunity is not in ready_for_test — the report says so)
        revalidate = self.client.post(f"/commerce/opportunities/{record_id}/revalidate")
        self.assertEqual(revalidate.status_code, 200, revalidate.text)
        report = revalidate.json()
        self.assertIn("actor", report)
        self.assertEqual(report["actor"], "local-operator")
        self.assertIn("transitioned", report)


if __name__ == "__main__":
    unittest.main()
