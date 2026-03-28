# """
# Copy this file to a new module (e.g. `gas_price.py`), then:
# 1. Subclass `BaseWorker`, set a unique `worker_id` and `interval_seconds`.
# 2. In `tick`: read last state from `self._store.get_worker_payload(worker_id)`;
#    fetch remote data with httpx; if changed, `await self._notify({...})` with the
#    payload keys expected by `MonitorBot.build_notification_embed_from_payload`:
#    title, subtitle, link, mode, event_index, source_name, event_id, occurred_at (datetime or ISO str).
# 3. Persist with `self._store.set_worker_payload(worker_id, json.dumps(...))`.
# 4. Register the worker in `workers/registry.py` (and add its id to `WORKER_IDS`).
# 5. Add any new env vars to `.env.example` and deployment docs.
#
# Do not import `discord` here.
# """

from __future__ import annotations

# import json
# from datetime import datetime, timezone
#
# import httpx
#
# from state.store import StateStore
# from workers.base import BaseWorker, NotifyFn
#
#
# class ExampleWorker(BaseWorker):
#     def __init__(self, store: StateStore, notify: NotifyFn) -> None:
#         super().__init__(
#             worker_id="example",
#             interval_seconds=300,
#             store=store,
#             notify=notify,
#         )
#
#     async def tick(self) -> None:
#         # prev = self._store.get_worker_payload(self.worker_id)
#         # async with httpx.AsyncClient(timeout=30.0) as client:
#         #     r = await client.get("https://example.com/api")
#         #     r.raise_for_status()
#         # data = r.text
#         # if data != prev:
#         #     await self._notify({...})
#         #     self._store.set_worker_payload(self.worker_id, json.dumps({"snapshot": data}))
#         raise NotImplementedError
