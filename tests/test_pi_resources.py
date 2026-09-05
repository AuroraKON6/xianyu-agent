import json
import re
import unittest
from pathlib import Path

from xianyu_agent.pi_runtime import PI_TOOL_ALLOWLIST


class PiProjectResourceTests(unittest.TestCase):
    def test_project_pins_official_pi_runtime(self):
        package = json.loads(Path("package.json").read_text(encoding="utf-8"))

        self.assertEqual(package["dependencies"]["@earendil-works/pi-coding-agent"], "0.84.2")
        self.assertEqual(package["engines"]["node"], ">=22.19.0")

    def test_operator_extension_exposes_only_internal_business_tools(self):
        extension = Path(".pi/extensions/xianyu-ops.ts").read_text(encoding="utf-8")
        expected = {
            "xianyu_business_snapshot",
            "xianyu_market_search",
            "xianyu_candidates",
            "xianyu_candidate_activation_packet",
            "xianyu_candidate_evidence",
            "xianyu_work_orders",
            "xianyu_commercial_terms",
            "xianyu_publish_preflight",
            "xianyu_commercial_launch_preview",
            "xianyu_review_candidate",
            "xianyu_prepare_listing",
            "xianyu_prepare_customer_reply",
        }

        for tool in expected:
            self.assertIn(f'name: "{tool}"', extension)
        registered = set(re.findall(r'name: "(xianyu_[a-z_]+)"', extension))
        self.assertEqual(registered, set(PI_TOOL_ALLOWLIST))
        self.assertNotIn('name: "xianyu_approve', extension)
        self.assertNotIn('name: "xianyu_execute', extension)
        self.assertNotIn('name: "xianyu_send', extension)
        self.assertNotIn('name: "xianyu_refund', extension)
        self.assertNotIn('apiRequest("/approvals/', extension)
        self.assertNotIn('apiRequest("/execution-jobs', extension)

        activation_tool = extension.split('name: "xianyu_candidate_activation_packet"', 1)[1]
        activation_tool = activation_tool.split("pi.registerTool({", 1)[0]
        self.assertIn("/activation-packet", activation_tool)
        self.assertIn("external_action: false", activation_tool)
        self.assertNotIn('method: "PATCH"', activation_tool)

        evidence_tool = extension.split('name: "xianyu_candidate_evidence"', 1)[1]
        evidence_tool = evidence_tool.split("pi.registerTool({", 1)[0]
        self.assertIn("/evidence-artifacts", evidence_tool)
        self.assertIn("external_action: false", evidence_tool)
        self.assertNotIn("/file", evidence_tool)
        self.assertNotIn('method: "POST"', evidence_tool)

    def test_system_contract_and_business_skills_are_present(self):
        system = Path(".pi/SYSTEM.md").read_text(encoding="utf-8")
        skills = sorted(path.parent.name for path in Path(".pi/skills").glob("*/SKILL.md"))

        self.assertIn("local Xianyu FastAPI service", system)
        self.assertIn("deterministic business", system)
        self.assertIn("system of record", system)
        self.assertIn("Do not approve on behalf of the operator", system)
        self.assertEqual(
            skills,
            [
                "buyer-conversion",
                "commercial-alpha",
                "commercial-experiment",
                "competitor-evidence",
                "fulfillment",
                "listing",
                "product-selection",
                "weekly-review",
            ],
        )

    def test_market_research_skill_is_read_only_and_evidence_bounded(self):
        extension = Path(".pi/extensions/xianyu-ops.ts").read_text(encoding="utf-8")
        skill = Path(".pi/skills/competitor-evidence/SKILL.md").read_text(encoding="utf-8")
        experiment = Path(".pi/skills/commercial-experiment/SKILL.md").read_text(encoding="utf-8")

        self.assertIn('apiRequest(\n        "/browser/search"', extension)
        self.assertIn("external_action: false", extension)
        self.assertIn("Displayed price may be a lead price", extension)
        self.assertNotIn('cookie_id: "default"', extension)
        self.assertIn('params.cookie_id ? { cookie_id: params.cookie_id } : {}', extension)
        self.assertIn('String(item?.sales_platform || "")', extension)
        self.assertIn('String(item?.market_scope || "")', extension)
        self.assertIn("xianyu_market_search", skill)
        self.assertIn("Sales, conversion, stock", skill)
        self.assertIn("xianyu_publish_preflight", experiment)
        self.assertIn("only when the operator explicitly asks", experiment)

    def test_product_and_alpha_skills_require_the_versioned_activation_packet(self):
        product = Path(".pi/skills/product-selection/SKILL.md").read_text(encoding="utf-8")
        alpha = Path(".pi/skills/commercial-alpha/SKILL.md").read_text(encoding="utf-8")

        self.assertIn("xianyu_candidate_activation_packet", product)
        self.assertIn("xianyu_candidate_evidence", product)
        self.assertIn("verifiable supplier", product.lower())
        self.assertIn("exact sku", product.lower())
        self.assertIn("current archived quote and stock artifacts", product.lower())
        self.assertIn("xianyu_candidate_activation_packet", alpha)
        self.assertIn("xianyu_candidate_evidence", alpha)
        self.assertIn("first blocker", alpha.lower())

    def test_console_declares_pi_as_the_operator_entrypoint(self):
        html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn("闲鱼 Pi Agent", html)
        self.assertIn("Pi 运营指令", html)
        self.assertIn("发送给Pi", html)
        self.assertNotIn("发送给OpenClaw", html + app_js)
        self.assertIn('data.type === "pi_agent"', app_js)
        self.assertIn('api("/agent/message"', app_js)


if __name__ == "__main__":
    unittest.main()
