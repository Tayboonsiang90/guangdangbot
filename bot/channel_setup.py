"""Create and resolve per-worker text channels in a guild (Discord API only in this package)."""

from __future__ import annotations

import asyncio
import logging
import re
import discord

from state.store import StateStore

LOGGER = logging.getLogger(__name__)

# Discord channel name: lowercase, alphanumeric, hyphens; max 100 chars.
_MAX_NAME = 100


def sanitize_worker_channel_name(worker_id: str) -> str:
    slug = worker_id.lower().strip()
    slug = re.sub(r"[^a-z0-9\-_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    base = f"monitor-{slug}" if slug else "monitor-worker"
    return base[:_MAX_NAME]


async def resolve_or_create_worker_channel(
    client: discord.Client,
    store: StateStore,
    *,
    guild_id: int,
    category_id: int | None,
    worker_id: str,
) -> int:
    """Return existing or new text channel ID for this worker; persist in store."""
    await client.wait_until_ready()

    stored = store.get_worker_channel_id(worker_id)
    if stored is not None:
        ch = client.get_channel(stored)
        if ch is None:
            try:
                ch = await client.fetch_channel(stored)
            except discord.NotFound:
                ch = None
            except discord.HTTPException as exc:
                LOGGER.warning("Could not fetch channel %s: %s", stored, exc)
                ch = None
        if isinstance(ch, discord.TextChannel):
            return stored
        LOGGER.info("Stored channel %s missing or wrong type; recreating for worker %s", stored, worker_id)
        store.delete_worker_channel_row(worker_id)

    guild = client.get_guild(guild_id)
    if guild is None:
        try:
            guild = await client.fetch_guild(guild_id)
        except discord.NotFound as exc:
            raise RuntimeError(f"Guild {guild_id} not found or bot not in guild") from exc

    name = sanitize_worker_channel_name(worker_id)
    category: discord.CategoryChannel | None = None
    if category_id is not None:
        cat_ch = guild.get_channel(category_id)
        if isinstance(cat_ch, discord.CategoryChannel):
            category = cat_ch
        else:
            try:
                fetched = await guild.fetch_channel(category_id)
                if isinstance(fetched, discord.CategoryChannel):
                    category = fetched
            except (discord.NotFound, discord.HTTPException) as exc:
                LOGGER.warning("MONITOR_CATEGORY_ID %s not usable: %s", category_id, exc)

    channel = await guild.create_text_channel(name, category=category)
    store.set_worker_channel_id(worker_id, channel.id)
    LOGGER.info("Created channel %s (%s) for worker %s", channel.name, channel.id, worker_id)
    return channel.id


async def ensure_worker_channels(
    client: discord.Client,
    store: StateStore,
    *,
    guild_id: int,
    category_id: int | None,
    worker_ids: list[str],
    stagger_seconds: float = 0.5,
) -> None:
    """Idempotently ensure each worker has a channel; stagger creates to reduce rate limits."""
    for wid in worker_ids:
        try:
            await resolve_or_create_worker_channel(
                client,
                store,
                guild_id=guild_id,
                category_id=category_id,
                worker_id=wid,
            )
        except Exception:
            LOGGER.exception("Failed to ensure channel for worker %s", wid)
        await asyncio.sleep(stagger_seconds)
