import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    discord_token: str
    alert_channel_id: int
    test_guild_id: int | None = None
    bot_owner_user_id: int | None = None



def _get_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(
            f"Missing required environment variable: {name}. "
            "Set it in the environment or in a .env file (see .env.example)."
        )
    return value



def _get_optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer") from exc



def load_settings() -> Settings:
    return Settings(
        discord_token=_get_required("DISCORD_TOKEN"),
        alert_channel_id=int(_get_required("ALERT_CHANNEL_ID")),
        test_guild_id=_get_optional_int("TEST_GUILD_ID"),
        bot_owner_user_id=_get_optional_int("BOT_OWNER_USER_ID"),
    )
