"""Run coordination: single stage-advancer per chain (taskbook R05).

Every scheduled chain declares an owner — ``python`` (this process runs
the loop), ``n8n`` (the external orchestrator advances it; this process
must NOT also run it) or ``manual``/``disabled`` (nobody advances it
automatically). At most one live process may hold a chain at any time:

* claiming writes an exclusive claim file under
  ``<state_dir>/coordination/<chain>.lock`` (pid + heartbeat);
* a claim held by a live pid is never stolen — a redeployed process
  must fail its claim and run the chain disabled until the old holder
  exits or the claim goes stale;
* release on shutdown deletes the claim, and a released/stopped chain
  is not re-claimed by the same (stopped) process — the runtime that
  released it is already torn down.

GitHub re-scanning is structurally research-only: it feeds the
discovery inbox and never creates work orders, so duplicate scans can
never advance the physical (listing/procurement/fulfilment) chain.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

OWNERS = ("python", "n8n", "manual", "disabled")

# chain -> (default owner, kind); kind research chains never touch the
# physical execution pipeline.
CHAIN_SPECS: Dict[str, Dict[str, str]] = {
    "github-discovery": {"default_owner": "python", "kind": "research"},
    "product-discovery": {"default_owner": "python", "kind": "research"},
    "agent-scheduler": {"default_owner": "python", "kind": "research"},
    "message-import": {"default_owner": "python", "kind": "physical"},
    "radar": {"default_owner": "disabled", "kind": "research"},
    "pi-task-runner": {"default_owner": "python", "kind": "research"},
}

_ENV_OWNER_KEY = "XIANYU_CHAIN_OWNER_%s"


class ChainClaimError(RuntimeError):
    """Raised when a chain is already advanced by another live holder."""


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == 259  # STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # non-Windows or probe failure: fail closed
        return True


class ChainCoordination:
    """Owns per-chain claims for exactly one process."""

    def __init__(self, state_dir: Path, *, stale_seconds: float = 900.0, clock=None) -> None:
        self.state_dir = Path(state_dir)
        self.stale_seconds = float(stale_seconds)
        self._clock = clock or time.time
        self._held: Dict[str, Dict[str, Any]] = {}

    # ---- configuration ----------------------------------------------------

    def owner_for(self, chain: str) -> str:
        spec = CHAIN_SPECS.get(chain)
        if spec is None:
            raise KeyError(f"unknown chain {chain!r}")
        env_key = _ENV_OWNER_KEY % chain.upper().replace("-", "_")
        owner = str(os.environ.get(env_key, spec["default_owner"])).strip().lower()
        if owner not in OWNERS:
            raise ValueError(
                f"chain {chain!r} owner {owner!r} invalid; one of {OWNERS} expected"
            )
        return owner

    def kind_for(self, chain: str) -> str:
        return CHAIN_SPECS[chain]["kind"]

    def python_owned_chains(self):
        for chain in CHAIN_SPECS:
            if self.owner_for(chain) == "python":
                yield chain

    # ---- claims -------------------------------------------------------------

    def _lock_path(self, chain: str) -> Path:
        directory = self.state_dir / "coordination"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / (chain + ".lock")

    def _read_claim(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def claim(self, chain: str) -> Dict[str, Any]:
        """Acquire the chain for this process or raise ChainClaimError."""
        if chain in self._held:
            raise ChainClaimError(f"chain {chain!r} already claimed by this process")
        path = self._lock_path(chain)
        existing = self._read_claim(path)
        if existing is not None:
            pid = int(existing.get("pid") or 0)
            heartbeat = float(existing.get("heartbeat_at") or 0)
            fresh = (self._clock() - heartbeat) < self.stale_seconds
            if fresh and _pid_alive(pid):
                raise ChainClaimError(
                    f"chain {chain!r} is held by live pid {pid} "
                    f"(heartbeat {self._clock() - heartbeat:.0f}s ago); "
                    "refusing to double-advance — stop the old holder first"
                )
            logger.warning(
                "reclaiming stale chain %r (pid=%s, heartbeat_age=%.0fs)",
                chain,
                pid,
                self._clock() - heartbeat,
            )
        claim = {
            "chain": chain,
            "pid": os.getpid(),
            "claimed_at": self._clock(),
            "heartbeat_at": self._clock(),
            "owner": "python",
        }
        path.write_text(json.dumps(claim, ensure_ascii=False, indent=2), encoding="utf-8")
        self._held[chain] = claim
        return dict(claim)

    def release(self, chain: str) -> bool:
        """Release our claim (shutdown); idempotent, never re-claims."""
        if chain not in self._held:
            return False
        path = self._lock_path(chain)
        current = self._read_claim(path)
        if current is not None and int(current.get("pid") or 0) == os.getpid():
            try:
                path.unlink()
            except OSError:
                logger.exception("failed to remove claim file for %r", chain)
        del self._held[chain]
        return True

    def release_all(self) -> None:
        for chain in list(self._held):
            self.release(chain)

    def held_chains(self):
        return sorted(self._held)

    def describe(self) -> Dict[str, Any]:
        return {
            "pid": os.getpid(),
            "chains": {
                chain: {
                    "owner": self.owner_for(chain),
                    "kind": self.kind_for(chain),
                    "held": chain in self._held,
                }
                for chain in sorted(CHAIN_SPECS)
            },
        }
