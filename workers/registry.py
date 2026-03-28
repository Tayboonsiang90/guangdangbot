"""Register all workers here. Keep WORKER_IDS in sync with instantiated workers."""

from __future__ import annotations

from bot.client import MonitorBot
from state.store import StateStore
from workers.base import BaseWorker
from workers.noop import NoopWorker

# IDs must match worker instances below (used for Discord channels: monitor-<id>).
WORKER_IDS: tuple[str, ...] = ("noop",)


def build_workers(store: StateStore, bot: MonitorBot) -> list[BaseWorker]:
    def notify_for(wid: str):
        async def notify(payload: dict) -> None:
            await bot.send_worker_notification(wid, payload)

        return notify

    return [
        NoopWorker(
            "noop",
            interval_seconds=86400,
            store=store,
            notify=notify_for("noop"),
        ),
    ]
