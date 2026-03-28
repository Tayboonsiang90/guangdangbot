# Discord monitor bot

Python Discord bot that relays alert-style notifications to Discord channels, with **async polling workers** and **auto-created per-worker channels**.

**Status:** Deployed on [Render](https://render.com) (Background Worker). Core stack: relayer, SQLite state, scheduler, registry, noop worker, `/testalert`, `/setupchannels`.

## What is implemented

- **Discord relayer** (`discord.py`) with styled embeds.
- **`/testalert`** — sample embed to `ALERT_CHANNEL_ID` (testing).
- **Workers** — [`workers/base.py`](workers/base.py), [`scheduler.py`](scheduler.py), [`workers/registry.py`](workers/registry.py); default **noop** worker.
- **SQLite** — worker snapshot state + `worker_id` → channel mapping ([`state/store.py`](state/store.py)).
- **Auto channels** — per-worker text channels in `MONITOR_GUILD_ID` (see [`bot/channel_setup.py`](bot/channel_setup.py)); **`/setupchannels`** to repair.
- [`render.yaml`](render.yaml), [`runtime.txt`](runtime.txt).

## Project layout

| File | Role |
|------|------|
| [`main.py`](main.py) | `asyncio.gather` bot + scheduler |
| [`config.py`](config.py) | Environment variables |
| [`bot/client.py`](bot/client.py) | Bot, embeds, slash commands |
| [`bot/channel_setup.py`](bot/channel_setup.py) | Create/resolve worker channels |
| [`scheduler.py`](scheduler.py) | Worker loops after `wait_until_ready` |
| [`state/store.py`](state/store.py) | SQLite |
| [`workers/registry.py`](workers/registry.py) | Register workers + `WORKER_IDS` |
| [`docs/ADDING_WORKERS.md`](docs/ADDING_WORKERS.md) | **How to add a worker** |
| [`docs/DEPLOYMENT_RENDER_GITHUB.md`](docs/DEPLOYMENT_RENDER_GITHUB.md) | Render hosting |

## Local setup

1. Create a Discord application and bot in the [Developer Portal](https://discord.com/developers/applications).
2. Invite the bot with **`bot`** + **`applications.commands`**. The bot needs **Manage Channels** in the server where monitors run (to create worker channels), plus **Send Messages** / **Embed Links**.
3. Copy [`.env.example`](.env.example) to `.env` and set at least:
   - `DISCORD_TOKEN`
   - `ALERT_CHANNEL_ID` (for `/testalert` and fallback alerts)
   - `MONITOR_GUILD_ID` (server where `monitor-*` channels are created)
   - `STATE_DB_PATH` (default `data/state.db` is fine locally)
   - Optional: `MONITOR_CATEGORY_ID`, `TEST_GUILD_ID`, `BOT_OWNER_USER_ID`
4. Install and run:

   ```bash
   pip install -r requirements.txt
   python main.py
   ```

5. Run **`/testalert`** and/or **`/setupchannels`** in the server; confirm channels appear and embeds work.

### Troubleshooting

- **`403 Forbidden (50001)`** with `TEST_GUILD_ID`: see older README notes; remove `TEST_GUILD_ID` or fix invite (`applications.commands`).
- **Missing slash commands:** guild sync vs global sync (same as before).

## Host on Render

See **[docs/DEPLOYMENT_RENDER_GITHUB.md](docs/DEPLOYMENT_RENDER_GITHUB.md)**. Set the same variables in the dashboard (including **`MONITOR_GUILD_ID`**). Use a **persistent disk** path for `STATE_DB_PATH` if you need SQLite to survive redeploys.

**Do not** run the same bot token locally and on Render at once.

## Adding sources

Follow **[docs/ADDING_WORKERS.md](docs/ADDING_WORKERS.md)**.

## Cursor rules

See [`.cursor/rules/`](.cursor/rules/) — [Cursor Rules](https://cursor.com/docs/rules).
