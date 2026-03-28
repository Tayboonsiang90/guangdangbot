"""Abstract worker: polling logic only; no Discord imports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from state.store import StateStore

NotifyFn = Callable[[dict[str, Any]], Awaitable[None]]


class BaseWorker(ABC):
    """One worker per external source. Implement `tick` only."""

    def __init__(
        self,
        worker_id: str,
        interval_seconds: int,
        store: StateStore,
        notify: NotifyFn,
    ) -> None:
        self.worker_id = worker_id
        self.interval_seconds = interval_seconds
        self._store = store
        self._notify = notify

    def get_interval_seconds(self) -> int:
        """Sleep duration after each tick; override for dynamic intervals from persisted state."""
        return self.interval_seconds

    @abstractmethod
    async def tick(self) -> None:
        """One poll cycle: fetch, compare to store, notify on change, persist."""
