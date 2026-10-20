"""Test-environment guard for offline checks (taskbook R01).

Stdlib-only on purpose: this module must be importable BEFORE any
``xianyu_agent`` module so business imports can neither load the
production ``.env``, resolve against live data paths, nor reach the
network or spawn unvetted subprocesses.

Usage (see scripts/run_offline_checks.py)::

    import isolation_guard as guard
    guard.scrub_environment(os.environ)
    guard.assert_safe_paths(os.environ.get("DATA_DIR"), os.environ.get("DB_PATH"))
    guard.install(allow_subprocess=(sys.executable,))
    ...
    guard.uninstall()

The guard is deliberately independent from ``xianyu_agent`` so a broken
business import cannot disable it.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent


class IsolationError(RuntimeError):
    """Raised when a configuration value would break offline isolation."""


class GuardedActionError(RuntimeError):
    """Raised when a blocked action (network/subprocess) is attempted."""


# Keys matching these patterns are scrubbed from the environment before
# business code is imported: tests must never inherit ambient credentials
# and business code must not silently fall back to them.
_CREDENTIAL_KEY_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r".*_API_KEY$",
        r".*_TOKEN$",
        r".*_SECRET$",
        r".*_PASSWORD$",
        r"^XIANYU_COOKIES$",
        r".*_WEBHOOK_URL$",
    )
)


def is_credential_key(name: str) -> bool:
    return any(pattern.match(name) for pattern in _CREDENTIAL_KEY_PATTERNS)


def scrub_environment(env: dict) -> List[str]:
    """Remove credential-style keys from ``env`` in place; return their names."""
    removed = sorted(key for key in list(env) if is_credential_key(key))
    for key in removed:
        del env[key]
    return removed


def _normalized(path) -> Path:
    text = str(path)
    if not os.path.isabs(text):
        text = os.path.abspath(text)
    return Path(os.path.normpath(os.path.realpath(text) if os.path.exists(text) else text))


def is_forbidden_path(path) -> bool:
    """True when ``path`` is the production .env or lives under the live ``data/`` tree."""
    try:
        resolved = _normalized(path)
    except (TypeError, ValueError):
        return True
    repo = REPO_ROOT.resolve()
    if resolved == repo / ".env":
        return True
    data_root = repo / "data"
    if resolved == data_root or data_root in resolved.parents:
        return True
    return False


def assert_safe_paths(*paths) -> None:
    """Refuse data directories / DB paths that point into live storage."""
    for raw in paths:
        if raw is None or str(raw).strip() == "":
            continue
        if is_forbidden_path(raw):
            raise IsolationError(
                "path %r resolves into live project storage (data/ or .env); "
                "offline checks must use an explicit temporary state directory" % (str(raw),)
            )


def assert_safe_postgres_dsn(dsn: Optional[str], allowlist: Sequence[str]) -> None:
    """Fail-closed check for future PG integration tests (taskbook R10 rule).

    Membership must be an exact match against an explicitly provided
    allowlist; a database merely *named* like a test database is never
    sufficient, and a missing/empty allowlist rejects everything.
    """
    if dsn is None or str(dsn).strip() == "":
        return
    if not allowlist:
        raise IsolationError("postgres DSN supplied but no explicit test allowlist is configured")
    if str(dsn) not in set(allowlist):
        raise IsolationError(
            "postgres DSN is not in the explicit test allowlist; refusing to connect"
        )


_DEFAULT_ALLOW_HOSTS = ("127.0.0.1", "::1", "localhost")

# Parameters of the currently installed guard (empty when not installed).
# Tests inspect these instead of stacking a second installation on top of
# the offline runner's guard.
_ACTIVE_HOSTS: Tuple[str, ...] = ()
_ACTIVE_EXES: frozenset = frozenset()


def _install_marker() -> bool:
    return getattr(socket, "_xianyu_isolation_installed", False)


def is_installed() -> bool:
    return _install_marker()


def active_allow_hosts() -> Tuple[str, ...]:
    return _ACTIVE_HOSTS


def subprocess_allowed(executable) -> bool:
    if not str(executable).strip():
        return False
    try:
        # bare names like "node" must resolve through PATH so they match
        # the allowlist entries (which are absolute interpreter paths)
        import shutil

        resolved = shutil.which(str(executable)) or str(executable)
        normalized = _normalized(resolved).as_posix().lower()
    except (TypeError, ValueError, OSError):
        return False
    return normalized in _ACTIVE_EXES


def connection_allowed(host, allow_hosts: Iterable[str] = _DEFAULT_ALLOW_HOSTS) -> bool:
    if not isinstance(host, str):
        return False
    return host.lower() in {h.lower() for h in allow_hosts}


def install(
    allow_hosts: Iterable[str] = _DEFAULT_ALLOW_HOSTS,
    allow_subprocess: Iterable[str] = (),
    block_network: bool = True,
) -> None:
    """Patch socket + subprocess to fail closed for non-allowlisted targets.

    Loopback hosts stay reachable (in-process test doubles); every other
    host, DNS lookup and subprocess executable raises GuardedActionError
    before any real syscall is made.
    """
    if _install_marker():
        return
    hosts = tuple(allow_hosts)
    exes = frozenset(
        _normalized(exe).as_posix().lower() for exe in allow_subprocess if str(exe).strip()
    )

    real_connect = socket.socket.connect
    real_create = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo
    real_popen = subprocess.Popen

    def _guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if block_network and not connection_allowed(host, hosts):
            raise GuardedActionError("network connect blocked in offline mode: %r" % (host,))
        return real_connect(self, address)

    def _guarded_create_connection(address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if block_network and not connection_allowed(host, hosts):
            raise GuardedActionError("network connect blocked in offline mode: %r" % (host,))
        return real_create(address, *args, **kwargs)

    def _guarded_getaddrinfo(host, *args, **kwargs):
        if block_network and not connection_allowed(host, hosts):
            raise GuardedActionError("DNS resolution blocked in offline mode: %r" % (host,))
        return real_getaddrinfo(host, *args, **kwargs)

    class _GuardedPopen(real_popen):  # type: ignore[misc, valid-type]
        def __init__(self, args, *popen_args, **popen_kwargs):
            executable = args if isinstance(args, str) else (args[0] if args else "")
            if not subprocess_allowed(executable):
                raise GuardedActionError(
                    "subprocess blocked in offline mode: %r is not in the allowlist"
                    % (str(executable),)
                )
            real_popen.__init__(self, args, *popen_args, **popen_kwargs)

    global _ACTIVE_HOSTS, _ACTIVE_EXES
    _ACTIVE_HOSTS = hosts
    _ACTIVE_EXES = exes

    socket.socket.connect = _guarded_connect
    socket.create_connection = _guarded_create_connection
    socket.getaddrinfo = _guarded_getaddrinfo
    subprocess.Popen = _GuardedPopen

    socket._xianyu_isolation_installed = True  # type: ignore[attr-defined]
    socket._xianyu_isolation_state = (  # type: ignore[attr-defined]
        real_connect,
        real_create,
        real_getaddrinfo,
        real_popen,
    )


def uninstall() -> None:
    """Restore the original socket/subprocess implementations."""
    if not _install_marker():
        return
    real_connect, real_create, real_getaddrinfo, real_popen = socket._xianyu_isolation_state
    global _ACTIVE_HOSTS, _ACTIVE_EXES
    _ACTIVE_HOSTS = ()
    _ACTIVE_EXES = frozenset()
    socket.socket.connect = real_connect
    socket.create_connection = real_create
    socket.getaddrinfo = real_getaddrinfo
    subprocess.Popen = real_popen
    del socket._xianyu_isolation_installed  # type: ignore[attr-defined]
    del socket._xianyu_isolation_state  # type: ignore[attr-defined]
