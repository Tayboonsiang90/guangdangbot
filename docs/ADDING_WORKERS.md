# Adding a new monitor worker

Follow this checklist so each source stays isolated, Discord stays in `bot/`, and channels are provisioned automatically.

## Concepts

- **One worker** = one module under `workers/` + one row in [`workers/registry.py`](../workers/registry.py) and one id in `WORKER_IDS`.
- **No `discord` imports** in `workers/` — call `await self._notify(payload_dict)` only; the bot resolves the channel and builds embeds.
- **Channels:** For each `worker_id`, the bot creates (or reuses) a text channel named `monitor-<sanitized-id>` in `MONITOR_GUILD_ID`, optionally under `MONITOR_CATEGORY_ID`. Mapping is stored in SQLite ([`state/store.py`](../state/store.py)).
- **State:** Store last-seen snapshot in `worker_state` via `get_worker_payload` / `set_worker_payload` (JSON strings) so restarts do not duplicate alerts.

## Steps

1. **Copy** [`workers/_template_worker.py`](../workers/_template_worker.py) to a new file (e.g. `workers/my_source.py`).
2. **Subclass** [`BaseWorker`](../workers/base.py): set a unique **`worker_id`** (stable, used in DB and channel name) and **`interval_seconds`**.
3. **Implement** `async def tick(self)`:
   - Load previous state: `self._store.get_worker_payload(self.worker_id)`.
   - Fetch remote data with **`httpx`** (timeouts on every request). Prefer official APIs over HTML; use **BeautifulSoup** only if needed.
   - If changed, build **`payload`** for the bot (see payload shape below) and `await self._notify(payload)`.
   - Persist: `self._store.set_worker_payload(self.worker_id, json.dumps(...))`.
4. **Register** in [`workers/registry.py`](../workers/registry.py):
   - Append the `worker_id` to **`WORKER_IDS`**.
   - Instantiate your worker in **`build_workers`** with `notify_for("<same worker_id>")` like `NoopWorker`.
5. **Environment:** Add any URLs or API keys to [`.env.example`](../.env.example) and [`docs/DEPLOYMENT_RENDER_GITHUB.md`](DEPLOYMENT_RENDER_GITHUB.md). Never commit secrets.
6. **Test locally** (`python main.py`), then push; Render picks up the same env vars.

## Notification payload shape

`notify` must pass a `dict` compatible with `MonitorBot.build_notification_embed_from_payload`:

| Key | Type |
|-----|------|
| `title` | `str` |
| `subtitle` | `str` |
| `link` | `str` |
| `mode` | `str` |
| `event_index` | `str` |
| `source_name` | `str` |
| `event_id` | `str` |
| `occurred_at` | `datetime` or ISO `str` |

## Slash commands

- **`/setupchannels`** — Re-runs channel provisioning for every registered `worker_id`. Use if someone deleted a channel. Requires **Manage Server** or `BOT_OWNER_USER_ID`, and must be run **in a guild**.

## Permissions (Discord server)

The bot needs, in **`MONITOR_GUILD_ID`**:

- **Manage Channels** (to create worker channels)
- **View Channel**, **Send Messages**, **Embed Links** on those channels (category or inherited role)

If you add **Manage Channels** after the first invite, update the bot’s role or re-invite with the right permissions.

## Do / don’t

- Do use **timeouts**, **retries with backoff**, and a clear **User-Agent** for HTTP.
- Do respect **robots.txt** and site terms.
- Don’t import **`discord`** in `workers/`.
- Don’t post tokens or secrets in code.

## Related

- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — diagrams and data flow
- [`.cursor/rules/scraping-workers.mdc`](../.cursor/rules/scraping-workers.mdc) — HTTP/scraping conventions
