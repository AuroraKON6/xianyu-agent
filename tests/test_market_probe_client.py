"""A50 probe client tests: the browser-backed market probe never fakes
success at any level of the honesty ladder (S level, fake adapters)."""
from __future__ import annotations

import unittest

from xianyu_agent.market_channel import BrowserMarketProbeClient, probe_channel


class _FakeAdapter:
    def __init__(self, state=None, check_result=None, check_raises=None):
        self._state = state
        self._check_result = check_result
        self._check_raises = check_raises
        self.check_calls = []

    def get_login_state(self, cookie_id="default"):
        if self._state is None:
            raise OSError("state file unreadable")
        return self._state

    def check_login_status(self, cookie_id="default"):
        self.check_calls.append(cookie_id)
        if self._check_raises is not None:
            raise self._check_raises
        return self._check_result

    def _run_sync(self, fn, *args):
        return fn(*args)


class BrowserProbeClientTests(unittest.TestCase):
    def _probe(self, resolver, **kwargs):
        return probe_channel(
            BrowserMarketProbeClient(resolver, **kwargs), channel_configured=True
        )

    def test_no_adapter_is_no_credentials(self):
        status = self._probe(lambda: None)
        self.assertEqual(status["status"], "unavailable")
        self.assertEqual(status["error_kind"], "no_credentials")

    def test_no_stored_session_is_no_credentials(self):
        adapter = _FakeAdapter(state={"logged_in": False})
        status = self._probe(lambda: adapter)
        self.assertEqual(status["error_kind"], "no_credentials")

    def test_cached_session_without_real_probe_is_unverified(self):
        adapter = _FakeAdapter(state={"cookie_id": "acct-1", "logged_in": True})
        status = self._probe(lambda: adapter, real_probe_enabled=False)
        self.assertEqual(status["status"], "unavailable")
        self.assertEqual(status["error_kind"], "unverified_session")
        # no platform read happened: the cached claim alone proves nothing
        self.assertEqual(adapter.check_calls, [])

    def test_real_probe_success_reports_available(self):
        adapter = _FakeAdapter(
            state={"cookie_id": "acct-1", "logged_in": True},
            check_result={"ok": True, "logged_in": True},
        )
        status = self._probe(lambda: adapter, real_probe_enabled=True)
        self.assertEqual(status["status"], "available")
        self.assertEqual(status["account_id"], "acct-1")
        self.assertIn("display_price", status["sample_fields"])
        self.assertEqual(adapter.check_calls, ["default"])

    def test_real_probe_rejection_is_auth_invalid(self):
        adapter = _FakeAdapter(
            state={"cookie_id": "acct-1", "logged_in": True},
            check_result={"ok": True, "logged_in": False},
        )
        status = self._probe(lambda: adapter, real_probe_enabled=True)
        self.assertEqual(status["status"], "unavailable")
        self.assertEqual(status["error_kind"], "auth_invalid")

    def test_unreadable_state_is_protocol_error(self):
        adapter = _FakeAdapter(state=None)
        status = self._probe(lambda: adapter)
        self.assertEqual(status["error_kind"], "protocol_error")

    def test_probe_client_has_no_fetch_capability(self):
        client = BrowserMarketProbeClient(lambda: None)
        self.assertFalse(hasattr(client, "fetch_page"))


if __name__ == "__main__":
    unittest.main()
