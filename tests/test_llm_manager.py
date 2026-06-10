import unittest

from xianyu_agent.config import Settings
from xianyu_agent.llm_manager import AGENT_DEFINITIONS, llm_manager


class LLMManagerTests(unittest.TestCase):
    def test_agent_clients_preserve_required_settings_fields(self):
        settings = Settings.from_env()
        llm_manager.init(settings)

        for agent_name in AGENT_DEFINITIONS:
            client = llm_manager.get_client(agent_name).client
            self.assertEqual(client.settings.xianyu_cookies, settings.xianyu_cookies)
            self.assertEqual(client.settings.browser_cdp_url, settings.browser_cdp_url)


if __name__ == "__main__":
    unittest.main()
