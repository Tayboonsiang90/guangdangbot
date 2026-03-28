"""Run async polling loops for workers alongside the Discord client."""

from __future__ import annotations

import asyncio
import logging

from bot import channel_setup
from bot.client import MonitorBot
from state.store import StateStore
from workers.base import BaseWorker

LOGGER = logging.getLogger(__name__)

# Match workers that persist poll_interval_seconds (e.g. AAA gas worker).
_MIN_SLEEP_SECONDS = 60
_MAX_SLEEP_SECONDS = 86400


def _clamp_sleep_seconds(raw: int) -> int:
    return max(_MIN_SLEEP_SECONDS, min(_MAX_SLEEP_SECONDS, raw))


async def _worker_loop(worker: BaseWorker) -> None:
    while True:
        try:
            await worker.tick()
        except Exception:
            LOGGER.exception("Worker %s tick failed", worker.worker_id)
        await asyncio.sleep(_clamp_sleep_seconds(worker.get_interval_seconds()))


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
