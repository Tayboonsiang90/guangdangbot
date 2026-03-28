"""Run async polling loops for workers alongside the Discord client."""

from __future__ import annotations

import asyncio
import logging

from bot import channel_setup
from bot.client import MonitorBot
from state.store import StateStore
from workers.base import BaseWorker

LOGGER = logging.getLogger(__name__)


async def _worker_loop(worker: BaseWorker) -> None:
    while True:
        try:
            await worker.tick()
        except Exception:
            LOGGER.exception("Worker %s tick failed", worker.worker_id)
        await asyncio.sleep(worker.interval_seconds)


async def run_scheduler(
    bot: MonitorBot,
    workers: list[BaseWorker],
    store: StateStore,
    *,
    guild_id: int,
    category_id: int | None,
    worker_ids: list[str],
) -> None:
    await bot.wait_until_ready()
    await channel_setup.ensure_worker_channels(
        bot,
        store,
        guild_id=guild_id,
        category_id=category_id,
        worker_ids=worker_ids,
    )
    await asyncio.gather(*(_worker_loop(w) for w in workers))
