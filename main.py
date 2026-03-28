import asyncio
import logging

from bot.client import MonitorBot
from config import load_settings


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    # Default discord logging is very chatty at INFO (gateway heartbeats).
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)


async def async_main() -> None:
    settings = load_settings()
    bot = MonitorBot(
        alert_channel_id=settings.alert_channel_id,
        test_guild_id=settings.test_guild_id,
        bot_owner_user_id=settings.bot_owner_user_id,
    )
    async with bot:
        await bot.start(settings.discord_token)


def main() -> None:
    configure_logging()
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Shutdown requested (KeyboardInterrupt)")


if __name__ == "__main__":
    main()
