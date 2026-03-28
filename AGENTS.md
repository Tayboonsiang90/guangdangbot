# Agent instructions — Discord monitor bot

## What this repo is

A **Python** Discord bot (`discord.py`) that runs **one OS process** on **Render** (Background Worker). It includes a **relayer**, **SQLite** state, a **scheduler**, and pluggable **workers** under `workers/`. Workers **must not** import Discord APIs; they call an injected **`notify`** callback with a payload `dict`; the **bot** builds embeds and posts to **per-worker channels** (auto-created) or falls back to `ALERT_CHANNEL_ID`.

## Where to look

| Topic | Document |
|--------|-----------|
| **Adding a new worker (checklist)** | [docs/ADDING_WORKERS.md](docs/ADDING_WORKERS.md) |
| Architecture, diagrams | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Render + GitHub, env vars | [docs/DEPLOYMENT_RENDER_GITHUB.md](docs/DEPLOYMENT_RENDER_GITHUB.md) |
| Cursor rules (scoped) | [.cursor/rules/](.cursor/rules/) |

## Rules for code changes

- **Discord-only code** → `bot/`. **No** `discord` imports under `workers/`.
- **Persistence** → `state/` ([`state/store.py`](state/store.py)); follow [.cursor/rules/sqlite-state.mdc](.cursor/rules/sqlite-state.mdc).
- **Scraping / HTTP polling** → `workers/`; follow [.cursor/rules/scraping-workers.mdc](.cursor/rules/scraping-workers.mdc) and [docs/ADDING_WORKERS.md](docs/ADDING_WORKERS.md).
- **Secrets** → environment variables only; never commit tokens.
- **Register new workers** in [`workers/registry.py`](workers/registry.py) (`WORKER_IDS` + `build_workers`).

## Adding a new alert source

1. Follow [docs/ADDING_WORKERS.md](docs/ADDING_WORKERS.md).
2. Document new env vars in [`.env.example`](.env.example) and [docs/DEPLOYMENT_RENDER_GITHUB.md](docs/DEPLOYMENT_RENDER_GITHUB.md).

## Cursor

Project rules live in `.cursor/rules/*.mdc` with **file globs**. Optional: [Cursor Rules documentation](https://cursor.com/docs/rules).
