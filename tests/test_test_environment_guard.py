"""R01 acceptance tests: the offline test-environment guard.

These tests prove, *before any business import is trusted*, that:

* credential-style environment variables have no fallback into tests;
* a planted .env is never loaded in test-isolation mode and the
  production .env is refused outright when named explicitly;
* data directories / DB paths pointing into live storage are rejected;
* future postgres DSNs fail closed without an exact allowlist entry;
* network connects, DNS lookups and unvetted subprocesses raise
  GuardedActionError before any real syscall.

Runs under both the legacy isolated runner and the new
scripts/run_offline_checks.py entry; the isolation flag is set locally
by each test so behavior does not depend on the entry point.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import isolation_guard as guard  # noqa: E402


class GuardScrubTests(unittest.TestCase):
    def test_credential_keys_are_removed_and_benign_keys_kept(self):
        env = {
            "DEEPSEEK_API_KEY": "trap",
            "MIMO_HERMES_KEY": "trap",
            "GITHUB_TOKEN": "trap",
            "XIANGUANJIA_API_KEY": "trap",
            "SERPAPI_KEY": "trap",
            "FEISHU_VERIFICATION_TOKEN": "trap",
            "XIANYU_COOKIES": "trap",
            "SOME_WEBHOOK_URL": "http://trap",
            "DATA_DIR": "/tmp/somewhere",
            "XIANYU_ADAPTER": "stub",
            "PATH": "kept",
        }
        removed = guard.scrub_environment(env)
        self.assertIn("DEEPSEEK_API_KEY", removed)
        self.assertIn("GITHUB_TOKEN", removed)
        self.assertIn("XIANYU_COOKIES", removed)
        self.assertNotIn("DEEPSEEK_API_KEY", env)
        self.assertNotIn("XIANYU_COOKIES", env)
        self.assertEqual(env["DATA_DIR"], "/tmp/somewhere")
        self.assertEqual(env["XIANYU_ADAPTER"], "stub")
        self.assertEqual(env["PATH"], "kept")

    def test_guard_module_imports_without_business_code(self):
        # Static check: the guard source must not import xianyu_agent at
        # all so it can run before business code (a same-process
        # sys.modules probe is unreliable once other tests have imported
        # the package).
        source = Path(guard.__file__).read_text(encoding="utf-8")
        offenders = [
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from ")) and "xianyu_agent" in line
        ]
        self.assertEqual(offenders, [], "isolation_guard must not import business modules")


class GuardPathTests(unittest.TestCase):
    def test_repo_env_and_data_tree_are_forbidden(self):
        self.assertTrue(guard.is_forbidden_path(guard.REPO_ROOT / ".env"))
        self.assertTrue(guard.is_forbidden_path(guard.REPO_ROOT / "data"))
        self.assertTrue(guard.is_forbidden_path(guard.REPO_ROOT / "data" / "live" / "x.json"))
        self.assertTrue(guard.is_forbidden_path(str(guard.REPO_ROOT / "data")))

    def test_temp_paths_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(guard.is_forbidden_path(Path(tmp) / "data"))
            self.assertFalse(guard.is_forbidden_path(Path(tmp) / "xianyu-tests.db"))

    def test_assert_safe_paths_rejects_live_directory(self):
        with self.assertRaises(guard.IsolationError):
            guard.assert_safe_paths(guard.REPO_ROOT / "data")
        with self.assertRaises(guard.IsolationError):
            guard.assert_safe_paths(str(guard.REPO_ROOT / "data" / "xianyu_data.db"))

    def test_assert_safe_paths_accepts_temporary_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard.assert_safe_paths(Path(tmp) / "data", Path(tmp) / "test.db", None, "")

    def test_relative_data_default_is_rejected(self):
        # A path that resolves INTO the live tree must be caught regardless
        # of how it is spelled (relative or absolute); build one relative to
        # the current cwd so the assertion is cwd-independent.
        live = guard.REPO_ROOT / "data"
        relative = os.path.relpath(str(live), os.getcwd())
        with self.assertRaises(guard.IsolationError):
            guard.assert_safe_paths(relative)


class GuardPostgresTests(unittest.TestCase):
    def test_missing_allowlist_rejects_any_dsn(self):
        with self.assertRaises(guard.IsolationError):
            guard.assert_safe_postgres_dsn("postgresql://user:pw@localhost:5432/xianyu_test", ())

    def test_allowlist_requires_exact_match(self):
        allowlist = ("postgresql://user:pw@localhost:5432/xianyu_test",)
        # "looks like a test db" but different host must not pass
        with self.assertRaises(guard.IsolationError):
            guard.assert_safe_postgres_dsn(
                "postgresql://user:pw@prod.internal:5432/xianyu_test", allowlist
            )
        guard.assert_safe_postgres_dsn(
            "postgresql://user:pw@localhost:5432/xianyu_test", allowlist
        )

    def test_empty_dsn_is_simply_unset(self):
        guard.assert_safe_postgres_dsn("", ())
        guard.assert_safe_postgres_dsn(None, ())


class GuardRuntimeTests(unittest.TestCase):
    def setUp(self):
        # Under the offline runner the guard is already installed globally
        # with sys.executable allowlisted; test against that active guard
        # instead of stacking a second installation (which install()
        # refuses). Under the legacy runner we install our own.
        self._owned = not guard.is_installed()
        if self._owned:
            guard.install(allow_subprocess=(sys.executable,))
        else:
            self.assertTrue(
                guard.subprocess_allowed(sys.executable),
                "active guard must allowlist the test interpreter",
            )

    def tearDown(self):
        if self._owned:
            guard.uninstall()

    def test_blocked_hosts(self):
        self.assertTrue(guard.connection_allowed("127.0.0.1"))
        self.assertTrue(guard.connection_allowed("LocalHost"))
        self.assertFalse(guard.connection_allowed("example.com"))
        self.assertFalse(guard.connection_allowed("192.0.2.1"))

    def test_connect_to_external_host_raises_before_syscall(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with self.assertRaises(guard.GuardedActionError):
                sock.connect(("192.0.2.1", 81))
        finally:
            sock.close()

    def test_dns_resolution_for_external_host_blocked(self):
        with self.assertRaises(guard.GuardedActionError):
            socket.getaddrinfo("example.com", 80)

    def test_unvetted_subprocess_blocked(self):
        # The guard raises before any real spawn, so the executable does
        # not even have to exist.
        with self.assertRaises(guard.GuardedActionError):
            subprocess.Popen(["this-binary-does-not-exist.exe", "--x"])

    def test_allowlisted_interpreter_still_runs(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "print('offline-ok')"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            out, err = proc.communicate(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertEqual(proc.returncode, 0, err)
        self.assertIn("offline-ok", out)

    def test_uninstall_restores_originals(self):
        guard.uninstall()
        self.assertFalse(guard._install_marker())
        # real getaddrinfo no longer raises our guard error
        try:
            socket.getaddrinfo("127.0.0.1", 80)
        except guard.GuardedActionError:
            self.fail("guard still active after uninstall")


class ConfigIsolationModeTests(unittest.TestCase):
    """Settings.from_env must not fall back to the repository .env in test mode."""

    def setUp(self):
        self._saved = {
            key: os.environ.get(key)
            for key in (
                "XIANYU_TEST_ISOLATION",
                "XIANYU_TEST_DOTENV",
                "DATA_DIR",
                "DB_PATH",
                "XIANYU_ADAPTER",
            )
        }
        # Ambient shells may export real credentials (observed in this
        # repo's dev environment); tests must not observe or depend on
        # them, so scrub every credential-style key for this class.
        self._saved.update({key: os.environ.get(key) for key in guard.scrub_environment(os.environ)})
        os.environ["XIANYU_TEST_ISOLATION"] = "1"
        os.environ.pop("XIANYU_TEST_DOTENV", None)
        # Both runners preset these; pop them so the sandbox dotenv tests
        # observe what the .env file itself contributes (setdefault order).
        os.environ.pop("DATA_DIR", None)
        os.environ.pop("DB_PATH", None)
        os.environ.pop("XIANYU_ADAPTER", None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _settings(self):
        from xianyu_agent.config import Settings

        return Settings.from_env()

    def test_production_env_file_is_not_loaded_in_test_mode(self):
        # The real repository .env exists on disk (see docs/refactor baseline);
        # with isolation on and no explicit dotenv it must contribute nothing.
        self.assertTrue((guard.REPO_ROOT / ".env").is_file())
        settings = self._settings()
        self.assertEqual(settings.deepseek_api_key, "")
        self.assertEqual(settings.mimo_api_key, "")
        self.assertEqual(settings.github_token, "")
        self.assertEqual(settings.xianyu_cookies, "")

    def test_explicit_sandbox_dotenv_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox_env = Path(tmp) / "sandbox.env"
            sandbox_env.write_text(
                "DATA_DIR=%s\nXIANYU_ADAPTER=stub\n" % (Path(tmp) / "data").as_posix(),
                encoding="utf-8",
            )
            os.environ["XIANYU_TEST_DOTENV"] = str(sandbox_env)
            settings = self._settings()
            self.assertEqual(settings.data_dir, Path(tmp) / "data")
            self.assertEqual(settings.xianyu_adapter, "stub")

    def test_explicit_dotenv_pointing_at_production_env_is_refused(self):
        os.environ["XIANYU_TEST_DOTENV"] = str(guard.REPO_ROOT / ".env")
        with self.assertRaises(ValueError):
            self._settings()

    def test_non_test_mode_still_loads_explicit_env_path(self):
        os.environ["XIANYU_TEST_ISOLATION"] = ""
        with tempfile.TemporaryDirectory() as tmp:
            explicit = Path(tmp) / "legacy.env"
            explicit.write_text("DATA_DIR=%s\n" % (Path(tmp) / "d2").as_posix(), encoding="utf-8")
            from xianyu_agent.config import Settings

            settings = Settings.from_env(env_path=explicit)
            self.assertEqual(settings.data_dir, Path(tmp) / "d2")


if __name__ == "__main__":
    unittest.main()
