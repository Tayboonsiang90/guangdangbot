# Deployment — Render + GitHub

Host the bot on [Render](https://render.com) as a **Background Worker** (long-running process, no HTTP port). Connect the service to **GitHub** so deploys track commits and rollbacks are easy.

## Prerequisites

- This repository pushed to **GitHub** (private repo is fine).
- A Discord bot token, alert channel ID, and **monitor guild** ID (same vars as local `.env`).
- Bot role in that guild must allow **Manage Channels** (for per-worker channels) plus **Send Messages** / **Embed Links**.
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
     Render may suggest `gunicorn ...` for Python—that is **wrong** for this bot. **Always** use `python main.py`.
4. **Instance type:** choose a plan that stays online 24/7 if you need always-on (verify current Render **pricing**; free tiers change over time).
5. **Environment** → **Add environment variable**:

   | Key | Value | Secret? |
   |-----|--------|---------|
   | `DISCORD_TOKEN` | Bot token from Discord Developer Portal | Yes |
   | `ALERT_CHANNEL_ID` | Numeric channel ID for `/testalert` and fallback alerts | Often no |
   | `MONITOR_GUILD_ID` | Server ID where `monitor-*` worker channels are created | No |
   | `STATE_DB_PATH` | SQLite path (e.g. `/data/state.db` on a Render persistent disk) | No |
   | `MONITOR_CATEGORY_ID` | Optional: category ID under which to create worker channels | No |
   | `TEST_GUILD_ID` | Optional: server ID for fast slash-command sync | No |
   | `BOT_OWNER_USER_ID` | Optional: restrict `/testalert`; co-owns `/setupchannels` and `/aaagaspoll` with Manage Server | No |
   | `AAA_GAS_POLL_INTERVAL_SECONDS` | Optional: default poll interval for the AAA national gas worker (seconds; clamped 60–86400; default 300) | No |
   | `AAA_GAS_PAGE_URL` | Optional: URL scraped for national average (default `https://gasprices.aaa.com/`) | No |
   | `AAA_GAS_HTTP_USER_AGENT` | Optional: override HTTP User-Agent for that worker | No |

   Mark **`DISCORD_TOKEN`** as **secret** if Render offers that toggle.

6. **Create Background Worker**. Wait for the first build and deploy.

## Deploy from `render.yaml` (optional)

If you use **Blueprint**, you can link this repo and Render will read [`render.yaml`](../render.yaml). You still add **secrets** in the dashboard (or linked secret store)—do not commit tokens.

## Python version

[`runtime.txt`](../runtime.txt) pins the Python version for Render’s Python environment. If the dashboard shows a different Python (e.g. 3.14), set the service’s **Python version** to match a [supported](https://render.com/docs) release you use locally (e.g. **3.12.x**) so behavior stays predictable. Adjust `runtime.txt` if Render’s supported runtimes change.

## Persistent disk (recommended)

Attach a **persistent disk** to the worker and set `STATE_DB_PATH` to a file on that mount (e.g. `/data/state.db`) so **worker snapshot state** and **channel ID mappings** survive redeploys. Without it, the DB may reset and channels may be recreated.

## After deploy: verify

1. Open the service → **Logs**. You should see login lines (no traceback on startup).
2. In Discord, run **`/setupchannels`** (in the monitor guild) or wait for the scheduler’s first ensure pass.
3. Confirm **`monitor-noop`** and **`monitor-aaa-national-gas`** (and any other workers) exist under the guild or category.
4. Run **`/testalert`** — embed should appear in `ALERT_CHANNEL_ID`. The embed and ephemeral reply include **git commit** (from `RENDER_GIT_COMMIT` on Render), **branch**, and **process start time** so you can confirm the running instance matches GitHub after deploy. Optional: set **`GITHUB_REPO=owner/repo`** for a “View commit on GitHub” link.
5. Optional: **`/aaagaspoll`** (Manage Server or `BOT_OWNER_USER_ID`) sets the **AAA national gas** poll interval in SQLite (minutes); it applies after the current sleep cycle.

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
| Crash on start | Missing `DISCORD_TOKEN`, `ALERT_CHANNEL_ID`, or `MONITOR_GUILD_ID` in Render env |
| Cannot create channels | Bot lacks **Manage Channels**; re-invite or fix role permissions |
| Worker DB reset every deploy | Set `STATE_DB_PATH` on a persistent disk |
| Slash commands missing | Set `TEST_GUILD_ID` or wait for global sync; re-invite with `applications.commands` |
| 403 / Missing Access | See [README.md](../README.md) section on `TEST_GUILD_ID` |
| `429` / HTML from Cloudflare / “Error 1015” / “rate limited” on login | Discord’s edge (Cloudflare) sometimes limits **datacenter IPs** (e.g. Render’s outbound IP). **Not** a code bug. **Mitigations:** wait 15–60 minutes; avoid crash loops (fix env so the process is not restarting constantly); try another **Render region** to get a different IP; if it persists, try another host or region. OAuth redirect URIs (`localhost` vs `127.0.0.1`) do **not** affect token login from Render. |
| Bot online locally but “offline” on Render | Wrong token in Render env, process crashed (check logs), or **same token** still used by a local `python main.py` (stop local). |

## Related

- [ARCHITECTURE.md](ARCHITECTURE.md) — process model
- [ADDING_WORKERS.md](ADDING_WORKERS.md) — adding sources
- [README.md](../README.md) — local setup and env reference
