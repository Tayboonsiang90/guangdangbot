"""AAA national average gas price — httpx + BeautifulSoup; no Discord imports.

Personal monitoring only: respect https://gasprices.aaa.com/ terms and robots.txt;
use a moderate poll interval and a clear User-Agent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from config import Settings
from state.store import StateStore
from workers.base import BaseWorker, NotifyFn

LOGGER = logging.getLogger(__name__)

AAA_NATIONAL_GAS_WORKER_ID = "aaa-national-gas"

DEFAULT_PAGE_URL = "https://gasprices.aaa.com/"

# Aligned with scheduler clamp (workers/scheduler.py).
MIN_POLL_INTERVAL_SECONDS = 60
MAX_POLL_INTERVAL_SECONDS = 86400

DEFAULT_HTTP_USER_AGENT = (
    "DiscordMonitorBot/1.0 (AAA national gas worker; +https://github.com/)"
)

_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_MAX_FETCH_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 2.0

# Primary: map badges per user hint; fall back to any .map-badges on page.
_PRICE_SELECTORS = (
    "#maincontent .map-box .map-badges p.numb",
    ".map-box .map-badges p.numb",
    ".map-badges p.numb",
)

# US-style date near "Price as of" or standalone.
_DATE_IN_TEXT = re.compile(
    r"(?:Price\s+as\s+of\s*)?(\d{1,2}/\d{1,2}/\d{2,4})",
    re.IGNORECASE,
)


def clamp_poll_interval_seconds(raw: int) -> int:
    return max(MIN_POLL_INTERVAL_SECONDS, min(MAX_POLL_INTERVAL_SECONDS, raw))


def load_worker_state_dict(raw: str | None) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {"settings": {}, "snapshot": {}}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        LOGGER.warning("Invalid JSON in worker_state for %s; resetting shape", AAA_NATIONAL_GAS_WORKER_ID)
        return {"settings": {}, "snapshot": {}}
    if not isinstance(data, dict):
        return {"settings": {}, "snapshot": {}}
    settings = data.get("settings")
    snapshot = data.get("snapshot")
    return {
        "settings": dict(settings) if isinstance(settings, dict) else {},
        "snapshot": dict(snapshot) if isinstance(snapshot, dict) else {},
    }


def _normalize_price_text(text: str) -> str | None:
    cleaned = text.strip().replace("$", "").replace(",", "").strip()
    m = re.search(r"[\d.]+", cleaned)
    if not m:
        return None
    return m.group(0)


def _normalize_as_of_text(text: str) -> str | None:
    t = text.strip()
    m = _DATE_IN_TEXT.search(t)
    if m:
        return m.group(1)
    m2 = re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", t)
    return m2.group(0) if m2 else None


def parse_aaa_national_snapshot(html: str) -> tuple[str, str] | None:
    """Return (price, as_of) normalized strings, or None if the page shape is unexpected."""
    soup = BeautifulSoup(html, "lxml")

    price_el = None
    for sel in _PRICE_SELECTORS:
        price_el = soup.select_one(sel)
        if price_el:
            break

    if price_el is None:
        LOGGER.warning("AAA gas: no price element matched selectors")
        return None

    price_norm = _normalize_price_text(price_el.get_text(" ", strip=True))
    if not price_norm:
        LOGGER.warning("AAA gas: could not normalize price text")
        return None

    container = price_el.find_parent("div") or price_el.parent
    as_of_norm: str | None = None
    if container:
        for p in container.find_all("p"):
            raw = p.get_text(" ", strip=True)
            if not raw or p is price_el:
                continue
            as_of_norm = _normalize_as_of_text(raw)
            if as_of_norm:
                break
        if not as_of_norm:
            block_text = container.get_text(" ", strip=True)
            as_of_norm = _normalize_as_of_text(block_text)

    if not as_of_norm:
        badges = soup.select_one(".map-badges") or soup.select_one("#maincontent .map-box")
        if badges:
            as_of_norm = _normalize_as_of_text(badges.get_text(" ", strip=True))

    if not as_of_norm:
        LOGGER.warning("AAA gas: could not find as-of date near national average")
        return None

    return price_norm, as_of_norm


def merge_poll_interval_into_stored_state(
    store: StateStore,
    seconds: int,
) -> tuple[int | None, int]:
    """Read-modify-write settings.poll_interval_seconds; return (previous, clamped new)."""
    clamped = clamp_poll_interval_seconds(seconds)
    raw = store.get_worker_payload(AAA_NATIONAL_GAS_WORKER_ID)
    data = load_worker_state_dict(raw)
    prev = data["settings"].get("poll_interval_seconds")
    prev_int: int | None
    if isinstance(prev, int) and prev > 0:
        prev_int = prev
    else:
        prev_int = None
    data.setdefault("settings", {})["poll_interval_seconds"] = clamped
    store.set_worker_payload(AAA_NATIONAL_GAS_WORKER_ID, json.dumps(data, sort_keys=True))
    return prev_int, clamped


class AaaNationalGasWorker(BaseWorker):
    def __init__(
        self,
        store: StateStore,
        notify: NotifyFn,
        *,
        settings: Settings,
    ) -> None:
        self._settings = settings
        self._page_url = settings.aaa_gas_page_url.strip() or DEFAULT_PAGE_URL
        self._default_poll_interval_seconds = clamp_poll_interval_seconds(
            settings.aaa_gas_poll_interval_seconds
        )
        self._user_agent = settings.aaa_gas_http_user_agent or DEFAULT_HTTP_USER_AGENT
        super().__init__(
            worker_id=AAA_NATIONAL_GAS_WORKER_ID,
            interval_seconds=self._default_poll_interval_seconds,
            store=store,
            notify=notify,
        )

    def get_interval_seconds(self) -> int:
        raw = self._store.get_worker_payload(self.worker_id)
        data = load_worker_state_dict(raw)
        poll = data.get("settings", {}).get("poll_interval_seconds")
        if isinstance(poll, int) and poll > 0:
            return clamp_poll_interval_seconds(poll)
        return self._default_poll_interval_seconds

    async def tick(self) -> None:
        html = await self._fetch_html()
        if html is None:
            return

        parsed = parse_aaa_national_snapshot(html)
        if parsed is None:
            return

        price, as_of = parsed
        raw_state = self._store.get_worker_payload(self.worker_id)
        data = load_worker_state_dict(raw_state)

        if not data["settings"].get("poll_interval_seconds"):
            data.setdefault("settings", {})["poll_interval_seconds"] = self._default_poll_interval_seconds

        prev_snap = data.get("snapshot") or {}
        prev_price = prev_snap.get("price") if isinstance(prev_snap, dict) else None
        prev_as_of = prev_snap.get("as_of") if isinstance(prev_snap, dict) else None

        new_snap = {"price": price, "as_of": as_of}
        is_first_baseline = prev_price is None and prev_as_of is None

        if not is_first_baseline and prev_price == price and prev_as_of == as_of:
            return

        if is_first_baseline:
            data["snapshot"] = new_snap
            self._store.set_worker_payload(self.worker_id, json.dumps(data, sort_keys=True))
            LOGGER.info(
                "AAA gas: baseline snapshot stored (no alert): price=%s as_of=%s",
                price,
                as_of,
            )
            return

        payload = self._build_payload(price, as_of)
        await self._notify(payload)
        data["snapshot"] = new_snap
        self._store.set_worker_payload(self.worker_id, json.dumps(data, sort_keys=True))

    def _build_payload(self, price: str, as_of: str) -> dict[str, Any]:
        event_id_src = f"{price}|{as_of}"
        event_id = hashlib.sha256(event_id_src.encode()).hexdigest()[:20]
        now = datetime.now(timezone.utc)
        subtitle = (
            f"**National average:** ${price}\n"
            f"**Price as of:** {as_of}\n"
            f"_Source page updates daily (OPIS/AAA)._"
        )
        return {
            "title": "AAA national average updated",
            "subtitle": subtitle,
            "link": self._page_url,
            "mode": "scrape",
            "event_index": as_of,
            "source_name": "AAA Gas Prices (national)",
            "event_id": event_id,
            "occurred_at": now,
        }

    async def _fetch_html(self) -> str | None:
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": DEFAULT_PAGE_URL,
        }
        last_exc: BaseException | None = None
        for attempt in range(1, _MAX_FETCH_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=headers, follow_redirects=True) as client:
                    response = await client.get(self._page_url)
                if response.status_code >= 400:
                    LOGGER.warning(
                        "AAA gas: HTTP %s (attempt %s/%s)",
                        response.status_code,
                        attempt,
                        _MAX_FETCH_ATTEMPTS,
                    )
                    if attempt < _MAX_FETCH_ATTEMPTS:
                        await asyncio.sleep(_BACKOFF_BASE_SECONDS ** (attempt - 1))
                    continue
                return response.text
            except (httpx.HTTPError, OSError) as exc:
                last_exc = exc
                LOGGER.warning(
                    "AAA gas: fetch failed (attempt %s/%s): %s",
                    attempt,
                    _MAX_FETCH_ATTEMPTS,
                    exc,
                )
                if attempt < _MAX_FETCH_ATTEMPTS:
                    await asyncio.sleep(_BACKOFF_BASE_SECONDS ** (attempt - 1))
        if last_exc is not None:
            LOGGER.error("AAA gas: fetch exhausted retries: %s", last_exc)
        else:
            LOGGER.error(
                "AAA gas: could not fetch page after %s attempts (HTTP errors)",
                _MAX_FETCH_ATTEMPTS,
            )
        return None
