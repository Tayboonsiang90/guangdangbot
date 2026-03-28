"""Placeholder worker that does nothing (keeps channel provisioning and scheduler wiring testable)."""

from __future__ import annotations

from workers.base import BaseWorker


class NoopWorker(BaseWorker):
    async def tick(self) -> None:
        return
