# Discord monitor bot

Python Discord bot that relays alert-style notifications to a channel.

## What is implemented

- Discord relayer (`discord.py`) with one configured alert channel.
- Styled embed notifications (title, subtitle, link, metadata fields, timestamp).
- Slash command **`/testalert`** — sends a sample embed to the alert channel.
- [`render.yaml`](render.yaml) for Render, [`runtime.txt`](runtime.txt) for Python version on Render.

## Project layout

| File | Role |
|------|------|
| [`main.py`](main.py) | Entrypoint, logging, graceful shutdown (`async with bot`) |
| [`config.py`](config.py) | Environment variables |
| [`bot/client.py`](bot/client.py) | Bot client, embed builder, `/testalert` |
| [`requirements.txt`](requirements.txt) | Dependencies |
| [`docs/DEPLOYMENT_RENDER_GITHUB.md`](docs/DEPLOYMENT_RENDER_GITHUB.md) | **Hosting on Render (step-by-step)** |

## Local setup

1. Create a Discord application and bot in the [Developer Portal](https://discord.com/developers/applications).
2. Invite the bot with scopes **`bot`** + **`applications.commands`** and permissions **Send Messages**, **Embed Links**.
3. Copy [`.env.example`](.env.example) to `.env` and set:
   - `DISCORD_TOKEN`
   - `ALERT_CHANNEL_ID`
   - Optional: `TEST_GUILD_ID`, `BOT_OWNER_USER_ID`
4. Install and run:

   ```bash
   pip install -r requirements.txt
   python main.py
   ```

5. In Discord, run **`/testalert`** and confirm the embed in your alert channel.

### Troubleshooting local / slash commands

- **`403 Forbidden (50001): Missing Access`** when using `TEST_GUILD_ID`: wrong server ID, bot not in server, or invite missing `applications.commands`. See section below.
- **`/testalert` slow to appear:** omit `TEST_GUILD_ID` and wait for global sync (up to ~1 hour), or set `TEST_GUILD_ID` to your server for instant guild sync.

### Error: `403 Forbidden (50001): Missing Access` on startup

If `TEST_GUILD_ID` is set but guild command sync fails: wrong guild ID, bot not in that server, or invite lacked **`applications.commands`**. Remove `TEST_GUILD_ID` to use global sync, or fix the invite. The bot falls back to global sync and logs a warning instead of crashing.

## Host on Render

Full checklist: **[docs/DEPLOYMENT_RENDER_GITHUB.md](docs/DEPLOYMENT_RENDER_GITHUB.md)**.

Summary:

1. Push this repo to GitHub.
2. Render → **New** → **Background Worker** → connect repo, branch `main`.
3. **Build:** `pip install -r requirements.txt` — **Start:** `python main.py`
4. Add environment variables (**no** `.env` file on Render): `DISCORD_TOKEN`, `ALERT_CHANNEL_ID`, plus optional vars from `.env.example`.
5. Deploy and check **Logs**, then test **`/testalert`** in Discord.

Confirm your Render **plan** supports 24/7 workers if you need always-on uptime.

## Next phase

Add polling workers under `workers/` and call into the bot layer’s `send_alert` (to be wired when workers exist).

## Cursor rules

See [`.cursor/rules/`](.cursor/rules/) — [Cursor Rules](https://cursor.com/docs/rules).
