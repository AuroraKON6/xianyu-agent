import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from xianyu_agent import server


class FakePiRuntime:
    def status(self, active=False):
        return {
            "ok": True if active else None,
            "enabled": True,
            "configured": True,
            "running": active,
            "model": "fake-pi-model",
        }

    def prompt(self, message, context=None, publish=True):
        return {
            "ok": True,
            "type": "pi_agent",
            "entrypoint": "pi",
            "result": {
                "message": f"Pi received: {message}",
                "context": context or {},
                "publish_requested": publish,
                "external_action": False,
            },
        }


class PiServerContractTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakePiRuntime()
        self.patch = patch.object(server, "pi_runtime", self.runtime)
        self.patch.start()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.patch.stop()

    def test_primary_agent_message_uses_pi(self):
        response = self.client.post(
            "/agent/message",
            json={"text": "读取状态", "context": {"scope": "daily"}, "publish": False},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["entrypoint"], "pi")
        self.assertEqual(body["result"]["context"], {"scope": "daily"})
        self.assertFalse(body["result"]["publish_requested"])

    def test_explicit_pi_route_and_active_status(self):
        response = self.client.post("/agents/pi/message", json={"message": "下一步是什么"})
        status = self.client.get("/agents/pi/status?active=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["type"], "pi_agent")
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["ok"])
        self.assertEqual(status.json()["model"], "fake-pi-model")

    def test_collaboration_topology_declares_pi_primary(self):
        response = self.client.get("/agents/collaboration")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["entrypoint"], "pi")
        self.assertEqual(response.json()["legacy_entrypoint"], "openclaw")


if __name__ == "__main__":
    unittest.main()
