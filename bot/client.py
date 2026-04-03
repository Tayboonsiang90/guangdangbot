import logging
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands

from bot import channel_setup
from bot import deploy_info
from config import Settings
from state.store import StateStore
from workers.aaa_national_gas import (
    AAA_NATIONAL_GAS_WORKER_ID,
    apply_aaa_snapshot,
    fetch_aaa_page_html,
    load_worker_state_dict,
    merge_poll_interval_into_stored_state,
    page_url_from_settings,
    parse_aaa_national_snapshot,
)
from workers.bonbast_rates import (
    BONBAST_WORKER_ID,
    apply_bonbast_snapshot,
    fetch_bonbast_live,
    load_bonbast_worker_state_dict,
    merge_bonbast_poll_interval_into_stored_state,
    public_page_url,
)


LOGGER = logging.getLogger(__name__)

_DISCORD_MSG_CAP = 1800


def _cap_discord_text(text: str, max_len: int = _DISCORD_MSG_CAP) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 24].rstrip() + "\n… _truncated_"


def _format_diag_lines(lines: list[str]) -> str:
    return "\n".join(lines) if lines else "(no lines)"


class MonitorBot(discord.Client):
    def __init__(
        self,
        *,
        alert_channel_id: int,
        state_store: StateStore,
        monitor_guild_id: int,
        monitor_category_id: int | None,
        registered_worker_ids: list[str],
        settings: Settings,
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
        self._settings = settings
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
            started = deploy_info.discord_timestamp_markdown(
                deploy_info.PROCESS_STARTED_AT, "F"
            )
            started_rel = deploy_info.discord_timestamp_markdown(
                deploy_info.PROCESS_STARTED_AT, "R"
            )
            await interaction.followup.send(
                f"Test alert sent.\n"
                f"**This process:** `{short}` · {branch} · {env_label}\n"
                f"**Process started:** {started} ({started_rel})\n"
                f"_Times use Discord dynamic timestamps — shown in your local timezone._\n"
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

        @self.tree.command(
            name="aaagaspoll",
            description="Set poll interval for the AAA national gas worker (saved in SQLite)",
        )
        @app_commands.describe(
            minutes="Minutes between checks (1–1440). Takes effect after the current sleep.",
        )
        async def aaagaspoll(
            interaction: discord.Interaction,
            minutes: app_commands.Range[int, 1, 1440],
        ) -> None:
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
            seconds = int(minutes) * 60
            prev_sec, new_sec = merge_poll_interval_into_stored_state(self._store, seconds)
            prev_part = (
                f"{prev_sec // 60} min ({prev_sec}s)"
                if prev_sec is not None
                else "not set (env default until stored)"
            )
            await interaction.response.send_message(
                f"Worker `{AAA_NATIONAL_GAS_WORKER_ID}` poll interval: "
                f"{prev_part} → **{new_sec // 60} min** ({new_sec}s).",
                ephemeral=True,
            )

        @self.tree.command(
            name="aaagas",
            description="Show last stored AAA national average price and as-of date",
        )
        async def aaagas(interaction: discord.Interaction) -> None:
            if interaction.guild is None:
                await interaction.response.send_message(
                    "Run this command inside a server.",
                    ephemeral=True,
                )
                return
            raw = self._store.get_worker_payload(AAA_NATIONAL_GAS_WORKER_ID)
            data = load_worker_state_dict(raw)
            snap = data.get("snapshot") or {}
            price = snap.get("price")
            as_of = snap.get("as_of")
            poll = data.get("settings", {}).get("poll_interval_seconds")
            if price is None or as_of is None:
                await interaction.response.send_message(
                    "No AAA gas snapshot stored yet. Wait for the worker to finish a successful poll "
                    "(or check logs if this never updates).",
                    ephemeral=True,
                )
                return
            poll_line = ""
            if isinstance(poll, int) and poll > 0:
                poll_line = f"\n**Poll interval:** {poll // 60} min ({poll}s)"
            await interaction.response.send_message(
                f"**AAA national average (last stored):** `${price}`\n"
                f"**Price as of:** {as_of}"
                f"{poll_line}\n"
                f"_From the last successful scrape in this bot, not a live page fetch._",
            )

        @self.tree.command(
            name="aaagasrefresh",
            description="Live-fetch AAA national gas page, update SQLite, show diagnostics (Manage Server)",
        )
        async def aaagasrefresh(interaction: discord.Interaction) -> None:
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

            start_url = page_url_from_settings(self._settings)
            try:
                await interaction.edit_original_response(
                    content=_cap_discord_text(
                        "**AAA live fetch**\n"
                        "Connecting…\n"
                        f"`{start_url}`"
                    )
                )

                html, fetch_diags = await fetch_aaa_page_html(self._settings)
                await interaction.edit_original_response(
                    content=_cap_discord_text(
                        "**AAA live fetch — HTTP complete**\n"
                        + _format_diag_lines(fetch_diags)
                    )
                )

                if html is None:
                    await interaction.edit_original_response(
                        content=_cap_discord_text(
                            "**AAA live fetch failed**\n"
                            "No HTML returned after retries.\n\n"
                            + _format_diag_lines(fetch_diags)
                        )
                    )
                    return

                await interaction.edit_original_response(
                    content=_cap_discord_text(
                        "**AAA live fetch — parsing**\n"
                        f"HTML length: **{len(html)}** characters\n"
                        "Extracting national average and as-of date…"
                    )
                )

                parsed = parse_aaa_national_snapshot(
                    html,
                    table_grade=self._settings.aaa_gas_table_grade,
                )
                if parsed is None:
                    await interaction.edit_original_response(
                        content=_cap_discord_text(
                            "**AAA live fetch — parse failed**\n"
                            "The page loaded, but selectors did not find a national average "
                            "and date (layout may have changed).\n\n"
                            + _format_diag_lines(fetch_diags)
                        )
                    )
                    return

                price, as_of = parsed

                async def notify_fn(payload: dict[str, Any]) -> None:
                    await self.send_worker_notification(AAA_NATIONAL_GAS_WORKER_ID, payload)

                result = await apply_aaa_snapshot(
                    self._store,
                    notify_fn,
                    settings=self._settings,
                    price=price,
                    as_of=as_of,
                )
                outcome = str(result.get("outcome", "?"))
                alert_sent = bool(result.get("alert_sent"))

                diag_tail = fetch_diags[-8:] if len(fetch_diags) > 8 else fetch_diags
                final = (
                    "**AAA live fetch — done**\n"
                    f"**National average:** `${price}`\n"
                    f"**Price as of:** {as_of}\n"
                    f"**SQLite outcome:** `{outcome}` "
                    "(baseline = first store only; unchanged = same as DB; changed = updated)\n"
                    f"**Monitor channel alert sent:** **{'yes' if alert_sent else 'no'}**\n"
                    "\n**Fetch diagnostics:**\n"
                    + _format_diag_lines(diag_tail)
                )
                await interaction.edit_original_response(content=_cap_discord_text(final))
            except discord.HTTPException:
                LOGGER.exception("aaagasrefresh Discord HTTP error")
                raise
            except Exception as exc:
                LOGGER.exception("aaagasrefresh failed")
                try:
                    await interaction.edit_original_response(
                        content=_cap_discord_text(f"**AAA live fetch error:** `{exc}`")
                    )
                except discord.HTTPException:
                    await interaction.followup.send(
                        f"Could not update message: {exc}",
                        ephemeral=True,
                    )

        @self.tree.command(
            name="bonbastpoll",
            description="Set poll interval for the Bonbast worker (saved in SQLite)",
        )
        @app_commands.describe(
            minutes="Minutes between checks (1–1440). Takes effect after the current sleep.",
        )
        async def bonbastpoll(
            interaction: discord.Interaction,
            minutes: app_commands.Range[int, 1, 1440],
        ) -> None:
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
            seconds = int(minutes) * 60
            prev_sec, new_sec = merge_bonbast_poll_interval_into_stored_state(self._store, seconds)
            prev_part = (
                f"{prev_sec // 60} min ({prev_sec}s)"
                if prev_sec is not None
                else "not set (env default until stored)"
            )
            await interaction.response.send_message(
                f"Worker `{BONBAST_WORKER_ID}` poll interval: "
                f"{prev_part} → **{new_sec // 60} min** ({new_sec}s).",
                ephemeral=True,
            )

        @self.tree.command(
            name="bonbast",
            description="Show last stored Bonbast sell/buy (IRR) for the configured currency",
        )
        async def bonbast(interaction: discord.Interaction) -> None:
            if interaction.guild is None:
                await interaction.response.send_message(
                    "Run this command inside a server.",
                    ephemeral=True,
                )
                return
            raw = self._store.get_worker_payload(BONBAST_WORKER_ID)
            data = load_bonbast_worker_state_dict(raw)
            snap = data.get("snapshot") or {}
            sell = snap.get("sell")
            buy = snap.get("buy")
            poll = data.get("settings", {}).get("poll_interval_seconds")
            if sell is None or buy is None:
                await interaction.response.send_message(
                    "No Bonbast snapshot stored yet. Wait for the worker to finish a successful poll "
                    "(or check logs if this never updates).",
                    ephemeral=True,
                )
                return
            poll_line = ""
            if isinstance(poll, int) and poll > 0:
                poll_line = f"\n**Poll interval:** {poll // 60} min ({poll}s)"
            cc = self._settings.bonbast_currency_code.strip().upper() or "USD"
            await interaction.response.send_message(
                f"**Bonbast {cc} (last stored)**\n"
                f"**Sell:** {sell:,}\n"
                f"**Buy:** {buy:,}"
                f"{poll_line}\n"
                f"_From the last successful fetch in this bot, not a live request._",
            )

        @self.tree.command(
            name="bonbastrefresh",
            description="Live-fetch Bonbast token+json rates, update SQLite, show diagnostics (Manage Server)",
        )
        async def bonbastrefresh(interaction: discord.Interaction) -> None:
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

            start_url = public_page_url(self._settings)
            try:
                await interaction.edit_original_response(
                    content=_cap_discord_text(
                        "**Bonbast live fetch**\n"
                        "Connecting…\n"
                        f"`{start_url}`"
                    )
                )

                rates, fetch_diags = await fetch_bonbast_live(self._settings)
                await interaction.edit_original_response(
                    content=_cap_discord_text(
                        "**Bonbast live fetch — HTTP complete**\n"
                        + _format_diag_lines(fetch_diags)
                    )
                )

                if rates is None:
                    await interaction.edit_original_response(
                        content=_cap_discord_text(
                            "**Bonbast live fetch failed**\n"
                            "No rates returned after retries.\n\n"
                            + _format_diag_lines(fetch_diags)
                        )
                    )
                    return

                sell, buy = rates

                await interaction.edit_original_response(
                    content=_cap_discord_text(
                        "**Bonbast live fetch — parsing**\n"
                        f"**Sell:** {sell:,} · **Buy:** {buy:,}\n"
                        "Applying to SQLite…"
                    )
                )

                async def notify_fn(payload: dict[str, Any]) -> None:
                    await self.send_worker_notification(BONBAST_WORKER_ID, payload)

                result = await apply_bonbast_snapshot(
                    self._store,
                    notify_fn,
                    settings=self._settings,
                    sell=sell,
                    buy=buy,
                )
                outcome = str(result.get("outcome", "?"))
                alert_sent = bool(result.get("alert_sent"))

                diag_tail = fetch_diags[-8:] if len(fetch_diags) > 8 else fetch_diags
                cc = self._settings.bonbast_currency_code.strip().upper() or "USD"
                final = (
                    "**Bonbast live fetch — done**\n"
                    f"**{cc}** — **Sell:** {sell:,} · **Buy:** {buy:,}\n"
                    f"**SQLite outcome:** `{outcome}` "
                    "(baseline = first store only; unchanged = same as DB; changed = updated)\n"
                    f"**Monitor channel alert sent:** **{'yes' if alert_sent else 'no'}**\n"
                    "\n**Fetch diagnostics:**\n"
                    + _format_diag_lines(diag_tail)
                )
                await interaction.edit_original_response(content=_cap_discord_text(final))
            except discord.HTTPException:
                LOGGER.exception("bonbastrefresh Discord HTTP error")
                raise
            except Exception as exc:
                LOGGER.exception("bonbastrefresh failed")
                try:
                    await interaction.edit_original_response(
                        content=_cap_discord_text(f"**Bonbast live fetch error:** `{exc}`")
                    )
                except discord.HTTPException:
                    await interaction.followup.send(
                        f"Could not update message: {exc}",
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
