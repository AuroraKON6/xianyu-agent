import os
import tempfile
import unittest
from pathlib import Path

from xianyu_agent.config import Settings


class SettingsEnvTests(unittest.TestCase):
    def test_process_environment_overrides_dotenv_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("SERVER_PORT=8788\nXIANYU_ADAPTER=stub\n", encoding="utf-8")

            old_port = os.environ.get("SERVER_PORT")
            old_adapter = os.environ.get("XIANYU_ADAPTER")
            try:
                os.environ["SERVER_PORT"] = "8799"
                os.environ["XIANYU_ADAPTER"] = "browser"

                settings = Settings.from_env(env_path)

                self.assertEqual(settings.server_port, 8799)
                self.assertEqual(settings.xianyu_adapter, "browser")
            finally:
                if old_port is None:
                    os.environ.pop("SERVER_PORT", None)
                else:
                    os.environ["SERVER_PORT"] = old_port
                if old_adapter is None:
                    os.environ.pop("XIANYU_ADAPTER", None)
                else:
                    os.environ["XIANYU_ADAPTER"] = old_adapter


if __name__ == "__main__":
    unittest.main()
