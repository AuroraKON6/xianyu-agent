#!/usr/bin/env python
"""Offline test/check entry for taskbook R01.

Proves isolation BEFORE any business module is imported:

1. reads the git HEAD via the filesystem (no subprocess);
2. fingerprints the entire live ``data/`` tree (before);
3. scrubs credential-style env vars, points DATA_DIR/DB_PATH at an
   explicit sandbox, and enables config test-isolation mode
   (``XIANYU_TEST_ISOLATION=1`` -> Settings.from_env never falls back
   to the repository .env);
4. installs the socket/subprocess guard (loopback + current
   interpreter only);
5. discovers and runs the unittest suite;
6. fingerprints ``data/`` again (after) and fails on any change.

Exit code 0 only when every test passes AND the data tree is
byte-identical. The sandbox is kept on failure for debugging (path is
printed); it is removed on success.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))  # isolation_guard (stdlib only)
sys.path.insert(0, str(REPO_ROOT))  # xianyu_agent

import isolation_guard as guard  # noqa: E402


def read_git_head() -> str:
    git_dir = REPO_ROOT / ".git"
    if git_dir.is_file():  # worktree link file
        content = git_dir.read_text(encoding="utf-8", errors="replace").strip()
        pointer = content.split("gitdir:", 1)[-1].strip()
        git_dir = Path(pointer)
    head_file = git_dir / "HEAD"
    if not head_file.is_file():
        return "unknown"
    head = head_file.read_text(encoding="utf-8", errors="replace").strip()
    if head.startswith("ref:"):
        ref_path = git_dir / head.split("ref:", 1)[1].strip()
        if ref_path.is_file():
            return ref_path.read_text(encoding="utf-8", errors="replace").strip()
    return head


def fingerprint_tree(root: Path):
    entries = []
    if not root.exists():
        return entries
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append(
                (
                    path.relative_to(root).as_posix(),
                    digest,
                    path.stat().st_size,
                )
            )
    return entries


def _offline_subprocess_allowlist():
    """Self-hosted interpreters the suite legitimately shells out to.

    - the project venv python (db boundary / drill tests spawn children)
    - node, used by the resale UI behavioural tests to render web/app.js
      against temp JSON files (residual limit: a node child runs outside
      the Python socket guard; it only executes project-owned web/*.js)
    """
    import shutil

    allow = [sys.executable]
    node = shutil.which("node")
    if node:
        allow.append(node)
    return tuple(allow)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-sandbox",
        action="store_true",
        help="keep the sandbox directory even when everything passes",
    )
    parser.add_argument(
        "--start-dir",
        default=str(REPO_ROOT / "tests"),
        help="unittest discovery start directory (default: tests/)",
    )
    args = parser.parse_args()

    started = time.time()
    print("[offline] repo:", REPO_ROOT)
    print("[offline] HEAD:", read_git_head())
    print("[offline] python:", sys.version.split()[0], sys.executable)

    data_root = REPO_ROOT / "data"
    data_before = fingerprint_tree(data_root)
    print("[offline] data/ files before: %d" % len(data_before))

    sandbox = Path(tempfile.mkdtemp(prefix="xianyu-offline-"))
    (sandbox / "data").mkdir(parents=True, exist_ok=True)

    removed = guard.scrub_environment(os.environ)
    print(
        "[offline] scrubbed credential env keys (%d): %s"
        % (len(removed), ", ".join(removed) if removed else "none")
    )

    os.environ["DATA_DIR"] = str(sandbox / "data")
    os.environ["DB_PATH"] = str(sandbox / "xianyu-tests.db")
    os.environ["XIANYU_ADAPTER"] = "stub"
    os.environ["XIANYU_TEST_ISOLATION"] = "1"
    os.environ["XIANYU_TEST_SANDBOX"] = str(sandbox)
    os.environ.pop("XIANYU_TEST_DOTENV", None)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        guard.assert_safe_paths(os.environ.get("DATA_DIR"), os.environ.get("DB_PATH"))
    except guard.IsolationError as exc:
        print("[offline] REFUSED before business import:", exc)
        shutil.rmtree(sandbox, ignore_errors=True)
        return 2

    print("[offline] sandbox:", sandbox)
    guard.install(
        allow_subprocess=_offline_subprocess_allowlist(),
    )
    print(
        "[offline] guard installed: loopback-only network, subprocess limited to %s"
        % (", ".join(_offline_subprocess_allowlist()),)
    )

    try:
        suite = unittest.defaultTestLoader.discover(args.start_dir)
        runner = unittest.TextTestRunner(verbosity=1)
        result = runner.run(suite)
    finally:
        guard.uninstall()

    data_after = fingerprint_tree(data_root)
    data_unchanged = data_before == data_after
    print("[offline] data/ files after: %d" % len(data_after))
    print("[offline] data/ unchanged:", data_unchanged)
    if not data_unchanged:
        before_map = {name: digest for name, digest, _ in data_before}
        after_map = {name: digest for name, digest, _ in data_after}
        changed = [
            name
            for name in sorted(set(before_map) | set(after_map))
            if before_map.get(name) != after_map.get(name)
        ]
        print("[offline] CHANGED ENTRIES:", json.dumps(changed[:50], ensure_ascii=False))

    tests_ok = result.wasSuccessful()
    print(
        "[offline] tests: run=%d failures=%d errors=%d skipped=%d"
        % (
            result.testsRun,
            len(result.failures),
            len(result.errors),
            len(result.skipped),
        )
    )
    print("[offline] elapsed: %.1fs" % (time.time() - started))

    if tests_ok and data_unchanged and not args.keep_sandbox:
        shutil.rmtree(sandbox, ignore_errors=True)
        print("[offline] sandbox removed:", sandbox)
    else:
        print("[offline] sandbox kept for inspection:", sandbox)

    return 0 if (tests_ok and data_unchanged) else 1


if __name__ == "__main__":
    sys.exit(main())
