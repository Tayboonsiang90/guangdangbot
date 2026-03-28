import logging
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands

from bot import channel_setup
from bot import deploy_info
from state.store import StateStore


LOGGER = logging.getLogger(__name__)


class MonitorBot(discord.Client):
    def __init__(
        self,
        *,
        alert_channel_id: int,
        state_store: StateStore,
        monitor_guild_id: int,
        monitor_category_id: int | None,
        registered_worker_ids: list[str],
        test_guild_id: int | None = None,
        bot_owner_user_id: int | None = None,
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True

        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.alert_channel_id = alert_channel_id
        self._store = state_store
        self.monitor_guild_id = monitor_guild_id
        self.monitor_category_id = monitor_category_id
        self._registered_worker_ids = registered_worker_ids
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
        @self.tree.command(
            name="testalert",
            description="Send a test monitor alert embed to ALERT_CHANNEL_ID",
        )
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
            short = deploy_info.get_commit_short() or "?"
            branch = deploy_info.get_branch() or "?"
            env_label = "Render" if deploy_info.is_render_runtime() else "local"
            started = deploy_info.PROCESS_STARTED_AT.strftime("%Y-%m-%d %H:%M UTC")
            await interaction.followup.send(
                f"Test alert sent.\n"
                f"**This process:** `{short}` · {branch} · {env_label} · started {started}\n"
                f"_Match this commit to GitHub after deploy; if deploy is still running, wait and re-run._",
                ephemeral=True,
            )

        @self.tree.command(
            name="setupchannels",
            description="Ensure monitor channels exist for all registered workers",
        )
        async def setupchannels(interaction: discord.Interaction) -> None:
            if interaction.guild is None:
                await interaction.response.send_message(
                    "Run this command inside the monitor server.",
                    ephemeral=True,
                )
                return
            if not await self._can_manage_monitor_setup(interaction):
                await interaction.response.send_message(
                    "You do not have permission to run this command.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                await channel_setup.ensure_worker_channels(
                    self,
                    self._store,
                    guild_id=self.monitor_guild_id,
                    category_id=self.monitor_category_id,
                    worker_ids=self._registered_worker_ids,
                )
            except Exception as exc:
                LOGGER.exception("setupchannels failed")
                await interaction.followup.send(f"Setup failed: {exc}", ephemeral=True)
                return
            await interaction.followup.send(
                "Monitor channels checked/created for all registered workers.",
                ephemeral=True,
            )

    async def _can_manage_monitor_setup(self, interaction: discord.Interaction) -> bool:
        if self.bot_owner_user_id and interaction.user.id == self.bot_owner_user_id:
            return True
        if interaction.guild and interaction.user.guild_permissions.manage_guild:
            return True
        return False

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

    def build_notification_embed_from_payload(self, payload: dict[str, Any]) -> discord.Embed:
        raw_time = payload["occurred_at"]
        if isinstance(raw_time, datetime):
            occurred_at = raw_time
        else:
            text = str(raw_time).replace("Z", "+00:00")
            occurred_at = datetime.fromisoformat(text)
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        return self.build_notification_embed(
            title=str(payload["title"]),
            subtitle=str(payload["subtitle"]),
            link=str(payload["link"]),
            mode=str(payload["mode"]),
            event_index=str(payload["event_index"]),
            source_name=str(payload["source_name"]),
            event_id=str(payload["event_id"]),
            occurred_at=occurred_at,
        )

    async def send_worker_notification(self, worker_id: str, payload: dict[str, Any]) -> None:
        embed = self.build_notification_embed_from_payload(payload)
        try:
            cid = await channel_setup.resolve_or_create_worker_channel(
                self,
                self._store,
                guild_id=self.monitor_guild_id,
                category_id=self.monitor_category_id,
                worker_id=worker_id,
            )
            channel = self.get_channel(cid)
            if channel is None:
                channel = await self.fetch_channel(cid)
            if not isinstance(channel, discord.abc.Messageable):
                raise RuntimeError(f"Channel {cid} is not messageable")
            await channel.send(embed=embed)
        except Exception as exc:
            LOGGER.warning(
                "Worker %s notify failed (%s); falling back to ALERT_CHANNEL_ID",
                worker_id,
                exc,
            )
            await self.send_alert(embed)

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
        embed.add_field(
            name="Build / deploy",
            value=deploy_info.format_testalert_build_text(),
            inline=False,
        )
        await self.send_alert(embed)
