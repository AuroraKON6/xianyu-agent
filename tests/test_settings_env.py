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

    def test_database_defaults_inside_configured_data_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            data_dir = Path(tmp) / "isolated-data"
            env_path.write_text(f"DATA_DIR={data_dir}\n", encoding="utf-8")

            old_data_dir = os.environ.pop("DATA_DIR", None)
            old_db_path = os.environ.pop("DB_PATH", None)
            try:
                settings = Settings.from_env(env_path)
                self.assertEqual(settings.data_dir, data_dir)
                self.assertEqual(Path(settings.db_path), data_dir / "xianyu_data.db")
            finally:
                if old_data_dir is not None:
                    os.environ["DATA_DIR"] = old_data_dir
                if old_db_path is not None:
                    os.environ["DB_PATH"] = old_db_path

    def test_browser_cdp_url_defaults_to_the_configured_debug_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("BROWSER_DEBUG_PORT=9333\n", encoding="utf-8")

            old_port = os.environ.pop("BROWSER_DEBUG_PORT", None)
            old_cdp_url = os.environ.pop("BROWSER_CDP_URL", None)
            try:
                settings = Settings.from_env(env_path)
                self.assertEqual(settings.browser_debug_port, 9333)
                self.assertEqual(settings.browser_cdp_url, "http://127.0.0.1:9333")
            finally:
                if old_port is not None:
                    os.environ["BROWSER_DEBUG_PORT"] = old_port
                if old_cdp_url is not None:
                    os.environ["BROWSER_CDP_URL"] = old_cdp_url


if __name__ == "__main__":
    unittest.main()
