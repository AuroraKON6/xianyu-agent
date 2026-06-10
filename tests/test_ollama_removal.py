"""Tests verifying Ollama has been fully removed and customer_service uses DeepSeek V4 Flash."""
import unittest


class OllamaRemovalTests(unittest.TestCase):
    """Verify Ollama is completely removed from the project."""

    def test_customer_service_backend_is_deepseek(self):
        from xianyu_agent.llm_manager import AGENT_DEFINITIONS

        self.assertEqual(AGENT_DEFINITIONS["customer_service"]["backend"], "deepseek")

    def test_customer_service_model_is_deepseek_v4_flash(self):
        from xianyu_agent.config import Settings

        # With deepseek_api_key set, customer_service should use deepseek-v4-flash
        s = Settings.from_env.__func__(Settings)  # won't work, use direct construction
        # Instead, check via llm_manager list_agents
        from xianyu_agent.llm_manager import llm_manager, AGENT_DEFINITIONS
        from xianyu_agent.config import Settings as S

        # Create minimal settings with deepseek key
        import os
        old_key = os.environ.get("DEEPSEEK_API_KEY", "")
        os.environ["DEEPSEEK_API_KEY"] = "test-key"
        try:
            settings = S.from_env()
            llm_manager.init(settings)
            agents = llm_manager.list_agents()
            cs = next(a for a in agents if a["name"] == "customer_service")
            self.assertEqual(cs["backend"], "deepseek")
            self.assertEqual(cs["model"], "deepseek-v4-flash")
        finally:
            if old_key:
                os.environ["DEEPSEEK_API_KEY"] = old_key
            else:
                os.environ.pop("DEEPSEEK_API_KEY", None)

    def test_list_agents_no_ollama_backend(self):
        from xianyu_agent.llm_manager import llm_manager, AGENT_DEFINITIONS

        for name, defn in AGENT_DEFINITIONS.items():
            self.assertNotEqual(defn["backend"], "ollama", f"Agent '{name}' still uses ollama backend")

    def test_no_ollama_in_settings_fields(self):
        """Settings should not have ollama_base_url or ollama_model fields."""
        from xianyu_agent.config import Settings
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(Settings)}
        self.assertNotIn("ollama_base_url", field_names)
        self.assertNotIn("ollama_model", field_names)

    def test_llm_client_does_not_call_ollama_api(self):
        """DeepSeekClient.chat should never call /api/generate (Ollama native endpoint)."""
        from xianyu_agent.llm import DeepSeekClient
        from xianyu_agent.config import Settings
        import inspect

        source = inspect.getsource(DeepSeekClient)
        # Should not contain Ollama-specific API endpoint
        self.assertNotIn("/api/generate", source)
        # Should not contain _ollama_chat method
        self.assertNotIn("_ollama_chat", source)

    def test_llm_enabled_does_not_check_ollama(self):
        """enabled property should only check deepseek_api_key and mimo_api_key."""
        from xianyu_agent.llm import DeepSeekClient
        import inspect

        source = inspect.getsource(DeepSeekClient.enabled.fget)
        self.assertNotIn("ollama", source.lower())

    def test_cli_no_ollama_configured(self):
        """CLI config-check should not expose ollama_configured."""
        from xianyu_agent.cli import main
        import inspect

        source = inspect.getsource(main)
        self.assertNotIn("ollama_configured", source)
        self.assertNotIn("ollama_base_url", source)
        self.assertNotIn("ollama_model", source)

    def test_no_ollama_qwen_in_source(self):
        """Key source files should not reference 'Ollama qwen3.5:9b'."""
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        files_to_check = [
            os.path.join(root, "xianyu_agent", "llm_manager.py"),
            os.path.join(root, "xianyu_agent", "agents", "customer_service.py"),
            os.path.join(root, "xianyu_agent", "agent_registry.py"),
            os.path.join(root, "xianyu_agent", "server.py"),
        ]
        for fpath in files_to_check:
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn(
                "Ollama qwen3.5:9b", content,
                f"File {fpath} still references 'Ollama qwen3.5:9b'"
            )

    def test_no_ollama_in_customer_service_description(self):
        """CustomerServiceAgent description should not mention Ollama."""
        from xianyu_agent.agents.customer_service import CustomerServiceAgent
        self.assertNotIn("Ollama", CustomerServiceAgent.description)
        self.assertNotIn("ollama", CustomerServiceAgent.description.lower())


class DeepSeekBackendTests(unittest.TestCase):
    """Verify DeepSeek backend works correctly for customer_service."""

    def test_create_client_deepseek_backend(self):
        """customer_service agent should get a DeepSeekClient with deepseek settings."""
        from xianyu_agent.llm_manager import AgentLLMClient
        from xianyu_agent.config import Settings
        import os

        os.environ["DEEPSEEK_API_KEY"] = "test-ds-key"
        os.environ["DEEPSEEK_BASE_URL"] = "https://api.deepseek.com"
        try:
            settings = Settings.from_env()
            agent_client = AgentLLMClient("customer_service", settings)
            client = agent_client.client

            # Should use deepseek settings
            self.assertEqual(client.settings.deepseek_api_key, "test-ds-key")
            self.assertEqual(client.settings.deepseek_model, "deepseek-v4-flash")
            # Should NOT have mimo keys (cleared for deepseek backend)
            self.assertEqual(client.settings.mimo_api_key, "")
        finally:
            os.environ.pop("DEEPSEEK_API_KEY", None)

    def test_create_client_mimo_backend_unchanged(self):
        """MiMo agents should still work as before."""
        from xianyu_agent.llm_manager import AgentLLMClient
        from xianyu_agent.config import Settings
        import os

        os.environ["MIMO_API_KEY"] = "test-mimo-key"
        os.environ["MIMO_BASE_URL"] = "https://mimo.example.com"
        try:
            settings = Settings.from_env()
            agent_client = AgentLLMClient("listing", settings)
            client = agent_client.client

            # listing agent uses mimo_listing_key (falls back to mimo_api_key if empty)
            expected_key = settings.mimo_listing_key or settings.mimo_api_key
            self.assertEqual(client.settings.mimo_api_key, expected_key)
            self.assertEqual(client.settings.mimo_base_url, "https://mimo.example.com")
            self.assertEqual(client.settings.deepseek_api_key, "")
        finally:
            os.environ.pop("MIMO_API_KEY", None)
            os.environ.pop("MIMO_BASE_URL", None)


if __name__ == "__main__":
    unittest.main()
