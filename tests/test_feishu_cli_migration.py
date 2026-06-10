"""Tests verifying Feishu CLI long connection migration."""
import os
import unittest


class FeishuCLITests(unittest.TestCase):
    """Verify Feishu is described as CLI long connection, not webhook."""

    def test_manifest_no_public_tunnel(self):
        """openclaw yaml should not contain YOUR_PUBLIC_TUNNEL."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        manifest = os.path.join(root, "openclaw", "xianyu-commerce-agent.openclaw.yaml")
        with open(manifest, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("YOUR_PUBLIC_TUNNEL", content)

    def test_manifest_no_event_endpoint(self):
        """openclaw yaml should not contain event_endpoint."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        manifest = os.path.join(root, "openclaw", "xianyu-commerce-agent.openclaw.yaml")
        with open(manifest, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("event_endpoint", content)

    def test_manifest_has_feishu_cli_long_connection(self):
        """openclaw yaml should specify feishu_cli_long_connection."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        manifest = os.path.join(root, "openclaw", "xianyu-commerce-agent.openclaw.yaml")
        with open(manifest, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("feishu_cli_long_connection", content)

    def test_manifest_has_local_event_path(self):
        """openclaw yaml should specify local_event_path."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        manifest = os.path.join(root, "openclaw", "xianyu-commerce-agent.openclaw.yaml")
        with open(manifest, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("local_event_path", content)
        self.assertIn("/feishu/events", content)

    def test_readme_no_feishu_webhook_url(self):
        """README should not reference FEISHU_WEBHOOK_URL."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        readme = os.path.join(root, "README.md")
        with open(readme, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("FEISHU_WEBHOOK_URL", content)

    def test_readme_no_your_public_tunnel(self):
        """README should not reference YOUR_PUBLIC_TUNNEL."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        readme = os.path.join(root, "README.md")
        with open(readme, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("YOUR_PUBLIC_TUNNEL", content)

    def test_readme_no_nei_wang_chuan_tou(self):
        """README should not mention 内网穿透 (tunnel setup)."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        readme = os.path.join(root, "README.md")
        with open(readme, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("内网穿透", content)

    def test_skill_md_has_feishu_cli_section(self):
        """SKILL.md should document Feishu CLI long connection."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        skill = os.path.join(root, "skills", "xianyu-commerce-agent", "SKILL.md")
        with open(skill, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Feishu CLI", content)
        self.assertIn("long connection", content.lower())

    def test_feishu_events_route_exists(self):
        """Server should still have /feishu/events route."""
        from xianyu_agent.server import app
        routes = [r.path for r in app.routes]
        self.assertIn("/feishu/events", routes)

    def test_env_example_no_feishu_webhook_url(self):
        """.env.example should not contain FEISHU_WEBHOOK_URL."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env_ex = os.path.join(root, ".env.example")
        with open(env_ex, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("FEISHU_WEBHOOK_URL", content)

    def test_config_check_no_webhook_url_output(self):
        """CLI config-check should not output feishu_webhook_url or feishu_webhook_configured."""
        from xianyu_agent.cli import main
        import inspect
        source = inspect.getsource(main)
        self.assertNotIn("feishu_webhook_url", source)
        self.assertNotIn("feishu_webhook_configured", source)

    def test_config_check_has_feishu_connection(self):
        """CLI config-check should output feishu_connection."""
        from xianyu_agent.cli import main
        import inspect
        source = inspect.getsource(main)
        self.assertIn("feishu_connection", source)

    def test_settings_keeps_verification_token(self):
        """Settings should still have feishu_verification_token."""
        from xianyu_agent.config import Settings
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(Settings)}
        self.assertIn("feishu_verification_token", field_names)

    def test_settings_keeps_feishu_webhook_url_for_notifier(self):
        """Settings keeps feishu_webhook_url for FeishuNotifier backward compat."""
        from xianyu_agent.config import Settings
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(Settings)}
        self.assertIn("feishu_webhook_url", field_names)


if __name__ == "__main__":
    unittest.main()
