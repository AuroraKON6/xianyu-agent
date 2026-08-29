"""Boundary tests for the deferred SQLite singleton.

The isolated runner sets DATA_DIR/DB_PATH to a temp directory, so in-process
tests exercise the env-var resolution branch. Tests that need the .env branch
run the project interpreter in a subprocess with those variables stripped.
The service composition constructs ``CommerceWorkflow``, which loads reply
rules and therefore connects to the *resolved* path during import -- so a
subprocess import must always run against temp paths, never against the real
live boundary.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
OLD_DEFAULT_DB = PROJECT_ROOT / "data" / "xianyu_data.db"
LIVE_DIR = PROJECT_ROOT / "data" / "live"


def _run_python(code: str) -> subprocess.CompletedProcess:
    """Run a subprocess with DB_PATH/DATA_DIR stripped (uses the project .env)."""
    env = dict(os.environ)
    env.pop("DB_PATH", None)
    env.pop("DATA_DIR", None)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
    )


def _file_fingerprint(path: Path):
    if not path.is_file():
        return None
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _live_files_snapshot():
    snapshot = {}
    for file in LIVE_DIR.rglob("*"):
        if file.is_file():
            snapshot[str(file.relative_to(LIVE_DIR))] = _file_fingerprint(file)
    return snapshot


class DbBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.python = str(VENV_PYTHON if VENV_PYTHON.is_file() else sys.executable)

    def test_constructing_manager_does_not_touch_filesystem(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager_path = Path(tmp) / "boundary.db"
            from xianyu_agent.db_manager import DBManager

            manager = DBManager(str(manager_path))
            self.assertIsNone(manager.conn)
            self.assertFalse(manager_path.exists())

    def test_first_query_creates_schema_at_configured_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager_path = Path(tmp) / "boundary.db"
            from xianyu_agent.db_manager import DBManager

            manager = DBManager(str(manager_path))
            try:
                manager.save_cookie("acc", "value", remark="r")
                self.assertTrue(manager_path.is_file())
                self.assertEqual(manager.get_cookie("acc"), "value")
            finally:
                manager.close()

    def test_configure_rebinds_before_connection_and_refuses_after(self):
        import xianyu_agent.db_manager as module

        saved_singleton = module._singleton
        saved_explicit = module._explicit_path
        try:
            module._singleton = None
            module._explicit_path = None
            with tempfile.TemporaryDirectory() as tmp:
                first = module.configure_db_manager(str(Path(tmp) / "one.db"))
                self.assertEqual(first.db_path, str(Path(tmp) / "one.db"))
                first._ensure_connected()
                try:
                    with self.assertRaises(RuntimeError):
                        module.configure_db_manager(str(Path(tmp) / "two.db"))
                finally:
                    first.close()
        finally:
            module._singleton = saved_singleton
            module._explicit_path = saved_explicit

    def test_dotenv_resolution_when_env_vars_are_stripped(self):
        before_old = _file_fingerprint(OLD_DEFAULT_DB)
        before_live = _live_files_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            temp_db = str(Path(tmp) / "env.db")
            code = (
                "import json, os;"
                "import xianyu_agent.db_manager as m;"
                "from xianyu_agent.config import Settings;"
                "settings_first = str(Settings.from_env().db_path);"
                "baseline = m._resolve_db_path();"
                "os.environ['DB_PATH'] = %r;"
                "override = m._resolve_db_path();"
                "print(json.dumps({'settings_first': settings_first, "
                "'baseline': baseline, 'override': override, "
                "'is_absolute': os.path.isabs(baseline)}))" % temp_db
            )
            result = _run_python(code)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(payload["is_absolute"])
            self.assertTrue(payload["baseline"].endswith("xianyu_data.db"))
            self.assertEqual(payload["baseline"], payload["settings_first"])
            self.assertEqual(payload["override"], temp_db)
        self.assertEqual(_file_fingerprint(OLD_DEFAULT_DB), before_old)
        self.assertEqual(_live_files_snapshot(), before_live)

    def test_service_import_binds_composition_root_to_env_path(self):
        before_old = _file_fingerprint(OLD_DEFAULT_DB)
        before_live = _live_files_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["DB_PATH"] = str(Path(tmp) / "isolated.db")
            env["DATA_DIR"] = str(Path(tmp) / "data")
            env["PYTHONIOENCODING"] = "utf-8"
            code = (
                "import json, os;"
                "import xianyu_agent.server as s;"
                "from xianyu_agent.db_manager import get_db_manager;"
                "print(json.dumps({'resolved': get_db_manager().db_path, "
                "'settings': str(s.settings.db_path), "
                "'connected': os.path.exists(get_db_manager().db_path)}))"
            )
            result = subprocess.run(
                [self.python, "-c", code],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=300,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(payload["resolved"], env["DB_PATH"])
            self.assertEqual(payload["settings"], env["DB_PATH"])
            # Composition loads reply rules during import, so the connection
            # is expected at the resolved path -- the env temp path, never a
            # default-relative location.
            self.assertTrue(payload["connected"])
        self.assertEqual(_file_fingerprint(OLD_DEFAULT_DB), before_old)
        self.assertEqual(_live_files_snapshot(), before_live)

    def test_composition_root_binds_singleton_to_settings_path(self):
        import xianyu_agent.server as server_module
        from xianyu_agent.db_manager import get_db_manager

        self.assertEqual(get_db_manager().db_path, server_module.settings.db_path)


if __name__ == "__main__":
    unittest.main()
