import logging
from datetime import datetime, timezone

import discord
from discord import app_commands


LOGGER = logging.getLogger(__name__)


class MonitorBot(discord.Client):
    def __init__(
        self,
        *,
        alert_channel_id: int,
        test_guild_id: int | None = None,
        bot_owner_user_id: int | None = None,
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True

        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.alert_channel_id = alert_channel_id
        self.test_guild_id = test_guild_id
        self.bot_owner_user_id = bot_owner_user_id

    async def setup_hook(self) -> None:
        self._register_commands()
        if self.test_guild_id:
            guild = discord.Object(id=self.test_guild_id)
            self.tree.copy_global_to(guild=guild)
            try:
                synced = await self.tree.sync(guild=guild)
                LOGGER.info(
                    "Synced %s slash commands to test guild %s",
                    len(synced),
                    self.test_guild_id,
                )
            except discord.Forbidden as exc:
                # 50001 Missing Access: bot not in guild, wrong guild id, or invite lacked
                # applications.commands scope.
                LOGGER.warning(
                    "Guild slash-command sync failed for TEST_GUILD_ID=%s: %s. "
                    "Fix: invite the bot to that server with the applications.commands scope, "
                    "or clear TEST_GUILD_ID to use slower global sync. "
                    "Falling back to global sync now.",
                    self.test_guild_id,
                    exc,
                )
                synced = await self.tree.sync()
                LOGGER.info(
                    "Synced %s global slash commands (fallback; may take up to ~1 hour to appear)",
                    len(synced),
                )
        else:
            synced = await self.tree.sync()
            LOGGER.info("Synced %s global slash commands", len(synced))

    def _register_commands(self) -> None:
        @self.tree.command(name="testalert", description="Send a test monitor alert embed")
        async def testalert(interaction: discord.Interaction) -> None:
            if self.bot_owner_user_id and interaction.user.id != self.bot_owner_user_id:
                await interaction.response.send_message(
                    "You are not allowed to use this command.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                await self.send_test_alert()
            except discord.HTTPException as exc:
                LOGGER.exception("Failed to send test alert")
                await interaction.followup.send(
                    f"Could not send the alert embed: {exc}",
                    ephemeral=True,
                )
                return
            await interaction.followup.send("Test alert sent.", ephemeral=True)

    async def on_ready(self) -> None:
        if self.user:
            LOGGER.info("Logged in as %s (%s)", self.user.name, self.user.id)
        LOGGER.info("Alert channel ID: %s", self.alert_channel_id)

    async def _resolve_alert_channel(self) -> discord.abc.Messageable:
        await self.wait_until_ready()

        channel = self.get_channel(self.alert_channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.alert_channel_id)

        if not isinstance(channel, discord.abc.Messageable):
            raise RuntimeError(f"Channel {self.alert_channel_id} is not messageable")

        return channel

    async def send_alert(self, embed: discord.Embed) -> None:
        channel = await self._resolve_alert_channel()
        await channel.send(embed=embed)

    def build_notification_embed(
        self,
        *,
        title: str,
        subtitle: str,
        link: str,
        mode: str,
        event_index: str,
        source_name: str,
        event_id: str,
        occurred_at: datetime,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            description=subtitle,
            color=discord.Color.dark_teal(),
            timestamp=occurred_at.astimezone(timezone.utc),
        )
        embed.add_field(name="Source", value=source_name, inline=False)
        embed.add_field(name="Link", value=link, inline=False)
        embed.add_field(name="Mode", value=mode, inline=True)
        embed.add_field(name="Event Index", value=event_index, inline=True)
        embed.add_field(name="Event ID", value=f"`{event_id}`", inline=False)
        embed.set_footer(text="Discord Monitor Bot")
        return embed

    async def send_test_alert(self) -> None:
        embed = self.build_notification_embed(
            title="Will sample event trigger in next hour?",
            subtitle="A new monitor link was created.",
            link="https://example.com/monitor/event/123",
            mode="live",
            event_index="585",
            source_name="Test Source",
            event_id="0xabc123def4567890abc123def4567890abc123def4567890abc123def4567890",
            occurred_at=datetime.now(timezone.utc),
        )
        await self.send_alert(embed)
