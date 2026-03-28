import asyncio
import logging

from bot.client import MonitorBot
from config import load_settings
from scheduler import run_scheduler
from state.store import StateStore
from workers.registry import WORKER_IDS, build_workers


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)


async def async_main() -> None:
    settings = load_settings()
    store = StateStore(settings.state_db_path)
    bot = MonitorBot(
        alert_channel_id=settings.alert_channel_id,
        state_store=store,
        monitor_guild_id=settings.monitor_guild_id,
        monitor_category_id=settings.monitor_category_id,
        registered_worker_ids=list(WORKER_IDS),
        settings=settings,
        test_guild_id=settings.test_guild_id,
        bot_owner_user_id=settings.bot_owner_user_id,
    )
    workers = build_workers(store, bot, settings)

    async with bot:
        await asyncio.gather(
            bot.start(settings.discord_token),
            run_scheduler(
                bot,
                workers,
                store,
                guild_id=settings.monitor_guild_id,
                category_id=settings.monitor_category_id,
                worker_ids=list(WORKER_IDS),
            ),
        )


def main() -> None:
    configure_logging()
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Shutdown requested (KeyboardInterrupt)")


if __name__ == "__main__":
    main()
