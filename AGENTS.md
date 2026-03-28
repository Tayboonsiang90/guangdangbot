# Agent instructions — Discord monitor bot

## What this repo is

A **Python** Discord bot (`discord.py`) that runs **one OS process** on **Render**: a Discord **relayer** is implemented now; **async polling workers** (gas, weather, APIs, etc.) will be added later. Workers must **not** import Discord APIs; they will call a single injected **`send_alert`** coroutine owned by the bot layer.

## Where to look

| Topic | Document |
|--------|-----------|
| Architecture, worker contract, diagrams | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Render + GitHub, env vars, rollback | [docs/DEPLOYMENT_RENDER_GITHUB.md](docs/DEPLOYMENT_RENDER_GITHUB.md) |
| Cursor rules (scoped) | [.cursor/rules/](.cursor/rules/) |

## Rules for code changes

- **Discord-only code** → `bot/`. **No** `discord` imports under `workers/`.
- **Persistence** → `state/` (SQLite when implemented); follow [.cursor/rules/sqlite-state.mdc](.cursor/rules/sqlite-state.mdc).
- **Scraping / HTTP polling** → `workers/`; prefer APIs over HTML; timeouts and backoff—see [.cursor/rules/scraping-workers.mdc](.cursor/rules/scraping-workers.mdc).
- **Secrets** → environment variables only; never commit tokens.

## Adding a new alert source (future)

1. Add a new module under `workers/` implementing the shared worker pattern (fetch → compare to stored state → `send_alert` on change).
2. Register it in the scheduler / main wiring (when those exist).
3. Document any new env vars in `.env.example` and [docs/DEPLOYMENT_RENDER_GITHUB.md](docs/DEPLOYMENT_RENDER_GITHUB.md).

## Cursor

Project rules live in `.cursor/rules/*.mdc` with **file globs** so only relevant files pull them into context. Optional: [Cursor Rules documentation](https://cursor.com/docs/rules).
