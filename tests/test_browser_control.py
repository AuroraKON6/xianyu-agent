import unittest
from types import SimpleNamespace

from xianyu_agent.browser_control import EdgeBrowserController


class EdgeBrowserControllerTests(unittest.TestCase):
    def test_cdp_requests_ignore_environment_proxy_and_use_debug_port(self):
        controller = EdgeBrowserController(
            SimpleNamespace(browser_debug_port=9333),
            storage=None,
            guard=None,
        )

        self.assertFalse(controller._http.trust_env)
        self.assertEqual(controller._debug_url(), "http://127.0.0.1:9333")

    def test_account_login_open_returns_only_sanitized_browser_metadata(self):
        controller = EdgeBrowserController(
            SimpleNamespace(browser_debug_port=9333),
            storage=None,
            guard=None,
        )
        controller.ensure_running = lambda: {"started": True, "status": {"ok": True}}
        controller._new_tab = lambda _url: {
            "id": "tab-1",
            "title": "private title",
            "webSocketDebuggerUrl": "ws://private-session",
        }

        result = controller.open_account_login()

        self.assertEqual(result["url"], "https://www.goofish.com/")
        self.assertEqual(result["tab_id"], "tab-1")
        self.assertTrue(result["browser_started"])
        self.assertNotIn("title", result)
        self.assertNotIn("webSocketDebuggerUrl", result)


if __name__ == "__main__":
    unittest.main()
