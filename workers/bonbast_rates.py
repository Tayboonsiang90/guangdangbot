"""Bonbast.com live IRR rates — token + /json; httpx only; no Discord imports.

Respect https://bonbast.com terms; use a moderate poll interval and a clear User-Agent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from config import Settings
from state.store import StateStore
from workers.base import BaseWorker, NotifyFn

LOGGER = logging.getLogger(__name__)

BONBAST_WORKER_ID = "bonbast-usd"

DEFAULT_BASE_URL = "https://bonbast.com"

MIN_POLL_INTERVAL_SECONDS = 60
MAX_POLL_INTERVAL_SECONDS = 86400

# Aligned with open-source bonbast CLI (mobile Chrome).
DEFAULT_HTTP_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)

_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_MAX_FETCH_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 2.0

_TOKEN_PATTERN = re.compile(r'param\s*[=:]\s*"([^"]+)"', re.MULTILINE)

# API: sell suffix "1", buy suffix "2" (see bonbast.com/json response keys).
SELL_SUFFIX = "1"
BUY_SUFFIX = "2"


def clamp_poll_interval_seconds(raw: int) -> int:
    return max(MIN_POLL_INTERVAL_SECONDS, min(MAX_POLL_INTERVAL_SECONDS, raw))


def load_bonbast_worker_state_dict(raw: str | None) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {"settings": {}, "snapshot": {}}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        LOGGER.warning("Invalid JSON in worker_state for %s; resetting shape", BONBAST_WORKER_ID)
        return {"settings": {}, "snapshot": {}}
    if not isinstance(data, dict):
        return {"settings": {}, "snapshot": {}}
    settings = data.get("settings")
    snapshot = data.get("snapshot")
    return {
        "settings": dict(settings) if isinstance(settings, dict) else {},
        "snapshot": dict(snapshot) if isinstance(snapshot, dict) else {},
    }


def base_url_from_settings(settings: Settings) -> str:
    u = settings.bonbast_base_url.strip().rstrip("/")
    return u if u else DEFAULT_BASE_URL


def user_agent_from_settings(settings: Settings) -> str:
    raw = (settings.bonbast_http_user_agent or "").strip()
    return raw if raw else DEFAULT_HTTP_USER_AGENT


def currency_code_normalized(settings: Settings) -> str:
    return settings.bonbast_currency_code.strip().lower() or "usd"


def public_page_url(settings: Settings) -> str:
    return base_url_from_settings(settings) + "/"


def extract_token_from_home_html(html: str) -> str | None:
    m = _TOKEN_PATTERN.search(html)
    if m and m.group(1):
        return m.group(1).strip()
    return None


def parse_sell_buy_from_json(data: dict[str, Any], currency_code: str) -> tuple[int, int] | None:
    """Return (sell, buy) for keys like ``usd1`` / ``usd2``."""
    code = currency_code.strip().lower()
    if not code:
        return None
    k_sell = f"{code}{SELL_SUFFIX}"
    k_buy = f"{code}{BUY_SUFFIX}"
    if k_sell not in data or k_buy not in data:
        LOGGER.warning("Bonbast: missing keys %s / %s in JSON response", k_sell, k_buy)
        return None
    try:
        sell = int(data[k_sell])
        buy = int(data[k_buy])
    except (TypeError, ValueError):
        LOGGER.warning("Bonbast: non-integer values for %s / %s", k_sell, k_buy)
        return None
    if sell <= 0 or buy <= 0:
        LOGGER.warning("Bonbast: unexpected non-positive rates sell=%s buy=%s", sell, buy)
        return None
    return sell, buy


def build_bonbast_notification_payload(
    sell: int,
    buy: int,
    *,
    currency_label: str,
    link: str,
) -> dict[str, Any]:
    event_id_src = f"{sell}|{buy}|{currency_label}"
    event_id = hashlib.sha256(event_id_src.encode()).hexdigest()[:20]
    now = datetime.now(timezone.utc)
    subtitle = (
        f"**Sell:** {sell:,}\n"
        f"**Buy:** {buy:,}\n"
        f"_IRR per unit ({currency_label.upper()})._"
    )
    return {
        "title": f"Bonbast {currency_label.upper()} updated",
        "subtitle": subtitle,
        "link": link,
        "mode": "api",
        "event_index": str(sell),
        "source_name": "Bonbast (live)",
        "event_id": event_id,
        "occurred_at": now,
    }


def _browser_headers_for_get(page_url: str, user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en-GB;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": page_url,
        "Upgrade-Insecure-Requests": "1",
    }


def _headers_for_json_post(origin: str, user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en-GB;q=0.9,en;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": origin,
        "Referer": origin + "/",
        "X-Requested-With": "XMLHttpRequest",
    }


def _default_cookies() -> dict[str, str]:
    return {
        "cookieconsent_status": "true",
        "st_bb": "0",
    }


def _snapshot_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def fetch_bonbast_live(settings: Settings) -> tuple[tuple[int, int] | None, list[str]]:
    """GET homepage token + POST /json; on ``reset``, one immediate re-GET and re-POST."""
    base = base_url_from_settings(settings)
    parsed = urlparse(base)
    if not parsed.scheme or not parsed.netloc:
        return None, [f"Invalid BONBAST_BASE_URL: {base!r}"]

    origin = f"{parsed.scheme}://{parsed.netloc}"
    page_url = public_page_url(settings)
    json_url = urljoin(origin + "/", "json")
    user_agent = user_agent_from_settings(settings)
    currency_code = currency_code_normalized(settings)

    diagnostics: list[str] = [
        f"Base: {base}",
        f"JSON URL: {json_url}",
        f"Currency: {currency_code}",
    ]
    ua_preview = user_agent if len(user_agent) <= 96 else user_agent[:93] + "…"
    diagnostics.append(f"User-Agent: {ua_preview}")

    last_exc: BaseException | None = None

    async def fetch_session_once(client: httpx.AsyncClient) -> tuple[int, int] | None:
        """Up to two GET/POST cycles if the API returns ``reset``."""
        for cycle in range(1, 3):
            label = f"cycle {cycle}/2"
            diagnostics.append(f"{label}: GET homepage …")
            try:
                home = await client.get(
                    page_url,
                    headers=_browser_headers_for_get(page_url, user_agent),
                )
            except (httpx.HTTPError, OSError) as exc:
                diagnostics.append(f"GET error: {type(exc).__name__}: {exc}")
                LOGGER.warning("Bonbast: homepage GET failed: %s", exc)
                return None

            diagnostics.append(f"GET homepage HTTP {home.status_code}")
            if home.status_code >= 400:
                return None

            token = extract_token_from_home_html(home.text)
            if not token:
                diagnostics.append("Token not found in homepage HTML")
                LOGGER.warning("Bonbast: token regex found no match")
                return None
            diagnostics.append(f"Token length: {len(token)} chars")

            post_headers = _headers_for_json_post(origin, user_agent)
            diagnostics.append(f"{label}: POST /json …")

            try:
                resp = await client.post(
                    json_url,
                    headers=post_headers,
                    data={"param": token},
                )
            except (httpx.HTTPError, OSError) as exc:
                diagnostics.append(f"POST /json error: {type(exc).__name__}: {exc}")
                LOGGER.warning("Bonbast: POST /json failed: %s", exc)
                return None

            diagnostics.append(f"POST /json HTTP {resp.status_code}")
            if resp.status_code >= 400:
                return None

            try:
                payload = resp.json()
            except json.JSONDecodeError as exc:
                diagnostics.append(f"JSON decode error: {exc}")
                return None

            if not isinstance(payload, dict):
                diagnostics.append("Response JSON is not an object")
                return None

            if "reset" in payload:
                diagnostics.append("API returned reset (token expired); refetching …")
                continue

            rates = parse_sell_buy_from_json(payload, currency_code)
            if rates is None:
                diagnostics.append("Could not parse sell/buy for configured currency")
                return None
            sell, buy = rates
            diagnostics.append(f"Parsed sell={sell} buy={buy}")
            return sell, buy

        diagnostics.append("Still no rates after reset retry")
        return None

    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            cookies=_default_cookies(),
            follow_redirects=True,
        ) as client:
            for attempt in range(1, _MAX_FETCH_ATTEMPTS + 1):
                diagnostics.append(f"Attempt {attempt}/{_MAX_FETCH_ATTEMPTS}")
                result = await fetch_session_once(client)
                if result is not None:
                    return result, diagnostics
                if attempt < _MAX_FETCH_ATTEMPTS:
                    await asyncio.sleep(_BACKOFF_BASE_SECONDS ** (attempt - 1))
    except (httpx.HTTPError, OSError) as exc:
        last_exc = exc
        diagnostics.append(f"Client error: {type(exc).__name__}: {exc}")
        LOGGER.warning("Bonbast: client fatal: %s", exc)

    if last_exc is not None:
        diagnostics.append(f"Failed after attempts: {last_exc}")
    return None, diagnostics


async def apply_bonbast_snapshot(
    store: StateStore,
    notify: NotifyFn,
    *,
    settings: Settings,
    sell: int,
    buy: int,
) -> dict[str, Any]:
    worker_id = BONBAST_WORKER_ID
    link = public_page_url(settings)
    default_poll = clamp_poll_interval_seconds(settings.bonbast_poll_interval_seconds)

    raw_state = store.get_worker_payload(worker_id)
    data = load_bonbast_worker_state_dict(raw_state)

    if not data["settings"].get("poll_interval_seconds"):
        data.setdefault("settings", {})["poll_interval_seconds"] = default_poll

    prev_snap = data.get("snapshot") or {}
    prev_sell = _snapshot_int(prev_snap.get("sell")) if isinstance(prev_snap, dict) else None
    prev_buy = _snapshot_int(prev_snap.get("buy")) if isinstance(prev_snap, dict) else None

    new_snap = {"sell": sell, "buy": buy}
    is_first_baseline = prev_sell is None and prev_buy is None

    if not is_first_baseline and prev_sell == sell and prev_buy == buy:
        return {"outcome": "unchanged", "alert_sent": False}

    if is_first_baseline:
        data["snapshot"] = new_snap
        store.set_worker_payload(worker_id, json.dumps(data, sort_keys=True))
        LOGGER.info(
            "Bonbast: baseline snapshot stored (no alert): sell=%s buy=%s",
            sell,
            buy,
        )
        return {"outcome": "baseline", "alert_sent": False}

    currency_label = currency_code_normalized(settings)
    payload = build_bonbast_notification_payload(
        sell,
        buy,
        currency_label=currency_label,
        link=link,
    )
    await notify(payload)
    data["snapshot"] = new_snap
    store.set_worker_payload(worker_id, json.dumps(data, sort_keys=True))
    return {"outcome": "changed", "alert_sent": True}


def merge_bonbast_poll_interval_into_stored_state(
    store: StateStore,
    seconds: int,
) -> tuple[int | None, int]:
    clamped = clamp_poll_interval_seconds(seconds)
    raw = store.get_worker_payload(BONBAST_WORKER_ID)
    data = load_bonbast_worker_state_dict(raw)
    prev = data["settings"].get("poll_interval_seconds")
    prev_int: int | None
    if isinstance(prev, int) and prev > 0:
        prev_int = prev
    else:
        prev_int = None
    data.setdefault("settings", {})["poll_interval_seconds"] = clamped
    store.set_worker_payload(BONBAST_WORKER_ID, json.dumps(data, sort_keys=True))
    return prev_int, clamped


class BonbastWorker(BaseWorker):
    def __init__(
        self,
        store: StateStore,
        notify: NotifyFn,
        *,
        settings: Settings,
    ) -> None:
        self._settings = settings
        self._default_poll_interval_seconds = clamp_poll_interval_seconds(
            settings.bonbast_poll_interval_seconds
        )
        super().__init__(
            worker_id=BONBAST_WORKER_ID,
            interval_seconds=self._default_poll_interval_seconds,
            store=store,
            notify=notify,
        )

    def get_interval_seconds(self) -> int:
        raw = self._store.get_worker_payload(self.worker_id)
        data = load_bonbast_worker_state_dict(raw)
        poll = data.get("settings", {}).get("poll_interval_seconds")
        if isinstance(poll, int) and poll > 0:
            return clamp_poll_interval_seconds(poll)
        return self._default_poll_interval_seconds

    async def tick(self) -> None:
        rates, _diagnostics = await fetch_bonbast_live(self._settings)
        if rates is None:
            return
        sell, buy = rates
        await apply_bonbast_snapshot(
            self._store,
            self._notify,
            settings=self._settings,
            sell=sell,
            buy=buy,
        )
