# Deployment — Render + GitHub

Host the bot on [Render](https://render.com) as a **Background Worker** (long-running process, no HTTP port). Connect the service to **GitHub** so deploys track commits and rollbacks are easy.

## Prerequisites

- This repository pushed to **GitHub** (private repo is fine).
- A Discord bot token and channel ID (same as local `.env`).
- Render account at [dashboard.render.com](https://dashboard.render.com).

## One-time: connect GitHub to Render

1. In Render: **Account Settings** → **Connected Accounts** → connect **GitHub**.
2. Grant access to the repository (all repos or only this one).

## Create the worker (dashboard)

1. **New +** → **Background Worker**.
2. **Connect** your repository; pick branch **`main`** (or your default).
3. Configure:
   - **Name:** e.g. `discord-monitor-bot`
   - **Region:** closest to you
   - **Branch:** `main`
   - **Root directory:** leave empty if `main.py` is at repo root
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `python main.py`
4. **Instance type:** choose a plan that stays online 24/7 if you need always-on (verify current Render **pricing**; free tiers change over time).
5. **Environment** → **Add environment variable**:

   | Key | Value | Secret? |
   |-----|--------|---------|
   | `DISCORD_TOKEN` | Bot token from Discord Developer Portal | Yes |
   | `ALERT_CHANNEL_ID` | Numeric channel ID for alerts | Often no |
   | `TEST_GUILD_ID` | Optional: server ID for fast slash-command sync | No |
   | `BOT_OWNER_USER_ID` | Optional: restrict `/testalert` to your user | No |

   Mark **`DISCORD_TOKEN`** as **secret** if Render offers that toggle.

6. **Create Background Worker**. Wait for the first build and deploy.

## Deploy from `render.yaml` (optional)

If you use **Blueprint**, you can link this repo and Render will read [`render.yaml`](../render.yaml). You still add **secrets** in the dashboard (or linked secret store)—do not commit tokens.

## Python version

[`runtime.txt`](../runtime.txt) pins the Python version for Render’s Python environment. Adjust if Render’s supported runtimes change (see Render docs).

## After deploy: verify

1. Open the service → **Logs**. You should see login lines (no traceback on startup).
2. In Discord, run **`/testalert`** in your server.
3. Confirm the embed appears in the alert channel.

## Updating the bot

1. Commit and **push** to the connected branch (e.g. `main`).
2. Render **auto-deploys** if enabled (default for many setups).
3. Watch **Logs** for the new deploy.

## Rollback

1. Service → **Events** / **Deploys**.
2. Pick a **previous successful deploy** and **Redeploy** (wording may vary).

Or revert the commit on GitHub and push again.

## Troubleshooting

| Symptom | What to check |
|--------|----------------|
| Build fails | Logs → install errors; Python version in `runtime.txt` vs Render support |
| Crash on start | Missing `DISCORD_TOKEN` / `ALERT_CHANNEL_ID` in Render env |
| Slash commands missing | Set `TEST_GUILD_ID` or wait for global sync; re-invite with `applications.commands` |
| 403 / Missing Access | See [README.md](../README.md) section on `TEST_GUILD_ID` |

## Related

- [ARCHITECTURE.md](ARCHITECTURE.md) — process model
- [README.md](../README.md) — local setup and env reference
