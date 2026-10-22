"""Application factory and lifecycle container (taskbook R02).

``AppRuntime`` owns the FastAPI app instance and every long-running
handle of one writer process:

* construction is side-effect-light — importing/building the runtime
  starts no threads and no async loops (the former import-time
  ``pi_task_runner.start()`` and shadow-observer thread moved here);
* ``start``/``start_async`` move the runtime through boot exactly once
  (repeat calls are no-ops, so a redeployed app cannot double-loop);
* ``shutdown`` cancels every asyncio task handle, stops the managed
  threads and runs the registered stop hooks in reverse order, once.

The composition root (``xianyu_agent.server``) registers its pieces on
the runtime; the runtime never imports business modules itself so it
stays testable in isolation.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ManagedThread:
    """A named start/stop pair the runtime starts at most once."""

    def __init__(self, name: str, start: Callable[[], Any], stop: Callable[[], Any]) -> None:
        self.name = name
        self._start_fn = start
        self._stop_fn = stop
        self.started = False

    def start(self) -> bool:
        if self.started:
            return False
        self._start_fn()
        self.started = True
        logger.info("managed thread started: %s", self.name)
        return True

    def stop(self) -> bool:
        if not self.started:
            return False
        try:
            self._stop_fn()
        except Exception:  # noqa: BLE001 - shutdown must keep unwinding
            logger.exception("managed thread stop hook failed: %s", self.name)
        finally:
            self.started = False
        logger.info("managed thread stopped: %s", self.name)
        return True


class AppRuntime:
    """Lifecycle container around one FastAPI application instance."""

    def __init__(
        self,
        *,
        settings: Any,
        state_dir: Optional[Path] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        from fastapi import FastAPI

        self.settings = settings
        self.state_dir = Path(state_dir) if state_dir is not None else Path(settings.data_dir)
        self.clock = clock or time.time
        self.app = FastAPI(title="Xianyu Pi Commerce Agent", version="0.7.0")
        self._managed_threads: List[ManagedThread] = []
        self._boot_hooks: List[Callable[[], Any]] = []
        self._stop_hooks: List[Callable[[], Any]] = []
        # idempotent heals (lease reconcile, orphan-lock recovery) that run
        # on EVERY startup event, not just the first one — a redeployed
        # lifespan re-entering the same runtime still gets its recovery
        self._reconcile_hooks: List[Callable[[], Any]] = []
        self._loops: List[Tuple[str, Callable[[], Any]]] = []
        self._tasks: List[asyncio.Task] = []
        self._started = False
        self._stopped = False
        self._lock = threading.RLock()

    # ---- registration (composition time) --------------------------------

    def manage_thread(self, name: str, start: Callable[[], Any], stop: Callable[[], Any]) -> None:
        with self._lock:
            self._managed_threads.append(ManagedThread(name, start, stop))

    def on_boot(self, hook: Callable[[], Any]) -> None:
        with self._lock:
            self._boot_hooks.append(hook)

    def on_stop(self, hook: Callable[[], Any]) -> None:
        with self._lock:
            self._stop_hooks.append(hook)

    def on_reconcile(self, hook: Callable[[], Any]) -> None:
        """Register an idempotent heal that runs on every startup event."""
        with self._lock:
            self._reconcile_hooks.append(hook)

    def add_loop(self, name: str, factory: Callable[[], Any]) -> None:
        """Register an async loop factory; started once on start_async."""
        with self._lock:
            self._loops.append((name, factory))

    # ---- lifecycle --------------------------------------------------------

    def start(self) -> bool:
        """Synchronous boot: hooks then managed threads. Idempotent.

        A runtime that was shut down may boot again (a fresh lifecycle
        for re-entered ASGI lifespans); within one active lifecycle a
        repeat start is a no-op, so loops never double.
        """
        with self._lock:
            if self._started and not self._stopped:
                return False
            self._started = True
            self._stopped = False
        for hook in list(self._boot_hooks):
            hook()
        for managed in list(self._managed_threads):
            managed.start()
        logger.info("app runtime started (threads=%d)", len(self._managed_threads))
        return True

    async def start_async(self) -> bool:
        """Boot + spawn registered loops, keeping every task handle."""
        started = self.start()
        # heals are idempotent by contract: they run on EVERY startup
        # event, including a re-entered lifespan on an already-started
        # runtime (a crashed process's recovery must not be skipped just
        # because the runtime object survived)
        for hook in list(self._reconcile_hooks):
            hook()
        for name, factory in list(self._loops):
            task = asyncio.ensure_future(factory())
            task.set_name("runtime-loop:%s" % name)  # type: ignore[attr-defined]
            self._tasks.append(task)
            logger.info("async loop started: %s", name)
        return started

    async def shutdown(self) -> bool:
        """Cancel loops, stop threads, run stop hooks (reverse order)."""
        with self._lock:
            if self._stopped:
                return False
            self._stopped = True
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for managed in reversed(list(self._managed_threads)):
            managed.stop()
        for hook in reversed(list(self._stop_hooks)):
            try:
                hook()
            except Exception:  # noqa: BLE001 - shutdown must keep unwinding
                logger.exception("stop hook failed during shutdown")
        logger.info("app runtime shut down")
        return True

    # ---- introspection ----------------------------------------------------

    @property
    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "started": self._started,
                "stopped": self._stopped,
                "managed_threads": [t.name for t in self._managed_threads],
                "loops": [name for name, _ in self._loops],
                "live_tasks": sum(1 for task in self._tasks if not task.done()),
                "state_dir": str(self.state_dir),
            }
