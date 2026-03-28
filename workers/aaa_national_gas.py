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
from bs4.element import Tag

from config import Settings
from state.store import StateStore
from workers.base import BaseWorker, NotifyFn

LOGGER = logging.getLogger(__name__)

AAA_NATIONAL_GAS_WORKER_ID = "aaa-national-gas"

DEFAULT_PAGE_URL = "https://gasprices.aaa.com/"

# Aligned with scheduler clamp (workers/scheduler.py).
MIN_POLL_INTERVAL_SECONDS = 60
MAX_POLL_INTERVAL_SECONDS = 86400

# Many CDNs block non-browser User-Agents. Default mimics current Chrome on Windows.
# Override with AAA_GAS_HTTP_USER_AGENT if needed (e.g. copy from your desktop browser).
DEFAULT_HTTP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
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


def _as_of_from_maincontent(soup: BeautifulSoup) -> str | None:
    mc = soup.select_one("#maincontent")
    if mc:
        return _normalize_as_of_text(mc.get_text(" ", strip=True))
    return None


def _as_of_from_map_badges_block(soup: BeautifulSoup) -> str | None:
    badges = soup.select_one(".map-badges") or soup.select_one("#maincontent .map-box")
    if badges:
        return _normalize_as_of_text(badges.get_text(" ", strip=True))
    return None


def _as_of_bounded_after_table(soup: BeautifulSoup, table: Tag) -> str | None:
    """Prefer #maincontent, then map badges; avoids footers outside main."""
    cap = table.find("caption")
    if cap:
        d = _normalize_as_of_text(cap.get_text(" ", strip=True))
        if d:
            return d
    parent = table.parent
    if parent is not None:
        d = _normalize_as_of_text(parent.get_text(" ", strip=True))
        if d:
            return d
    d = _as_of_from_maincontent(soup)
    if d:
        return d
    return _as_of_from_map_badges_block(soup)


def _parse_aaa_national_from_table_soup(soup: BeautifulSoup, table_grade: str) -> tuple[str | None, str | None]:
    """Table parse using an existing soup (single parse per HTML string)."""
    grade_lc = table_grade.strip().lower()
    if not grade_lc:
        return None, None

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        header_idx: int | None = None
        header_cells: list[str] = []
        for i, tr in enumerate(rows):
            ths = tr.find_all("th")
            if len(ths) < 2:
                continue
            if any(th.has_attr("colspan") or th.has_attr("rowspan") for th in ths):
                continue
            header_cells = [th.get_text(strip=True) for th in ths]
            if not any((h or "").strip().lower() == grade_lc for h in header_cells):
                continue
            header_idx = i
            break

        if header_idx is None or not header_cells:
            continue

        for tr in rows[header_idx + 1 :]:
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            if any(td.has_attr("colspan") or td.has_attr("rowspan") for td in tds):
                continue
            values = [td.get_text(strip=True) for td in tds]
            if len(values) < len(header_cells):
                continue
            for hi, hname in enumerate(header_cells):
                if not (hname or "").strip():
                    continue
                if hname.strip().lower() != grade_lc:
                    continue
                if hi >= len(values):
                    continue
                price_norm = _normalize_price_text(values[hi])
                if not price_norm:
                    LOGGER.warning("AAA gas: table cell for %s did not normalize to a price", table_grade)
                    return None, None
                as_of = _as_of_bounded_after_table(soup, table)
                return price_norm, as_of

    LOGGER.warning("AAA gas: no table with header column %r", table_grade)
    return None, None


def parse_aaa_national_from_table(html: str, table_grade: str) -> tuple[str | None, str | None]:
    """Parse national price from first table whose header row includes ``table_grade``."""
    return _parse_aaa_national_from_table_soup(BeautifulSoup(html, "lxml"), table_grade)


def _parse_map_badge_snapshot(soup: BeautifulSoup) -> tuple[str, str] | None:
    """Original map-box / ``p.numb`` + date path."""
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
        as_of_norm = _as_of_from_map_badges_block(soup)

    if not as_of_norm:
        LOGGER.warning("AAA gas: could not find as-of date near national average")
        return None

    return price_norm, as_of_norm


def parse_aaa_national_snapshot(html: str, *, table_grade: str = "Regular") -> tuple[str, str] | None:
    """Return (price, as_of) normalized strings, or None if the page shape is unexpected.

    Tries **table** parse first (column header matches ``table_grade``), then **map-badge** CSS path.
    """
    soup = BeautifulSoup(html, "lxml")

    t_price, t_as_of = _parse_aaa_national_from_table_soup(soup, table_grade)
    if t_price:
        as_of = t_as_of
        if not as_of:
            as_of = _as_of_from_map_badges_block(soup)
        if not as_of:
            as_of = _as_of_from_maincontent(soup)
        if as_of:
            return t_price, as_of
        LOGGER.warning("AAA gas: table price but no as-of; falling back to map-badge path")

    return _parse_map_badge_snapshot(soup)


def page_url_from_settings(settings: Settings) -> str:
    return settings.aaa_gas_page_url.strip() or DEFAULT_PAGE_URL


def user_agent_from_settings(settings: Settings) -> str:
    raw = (settings.aaa_gas_http_user_agent or "").strip()
    return raw if raw else DEFAULT_HTTP_USER_AGENT


def _aaa_browser_headers(page_url: str, user_agent: str) -> dict[str, str]:
    """Headers typical of a desktop Chrome navigation request (reduces HTTP 403 from edge/WAF)."""
    return {
        "User-Agent": user_agent,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Referer": page_url,
    }


def build_aaa_notification_payload(price: str, as_of: str, page_url: str) -> dict[str, Any]:
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
        "link": page_url,
        "mode": "scrape",
        "event_index": as_of,
        "source_name": "AAA Gas Prices (national)",
        "event_id": event_id,
        "occurred_at": now,
    }


async def fetch_aaa_page_html(settings: Settings) -> tuple[str | None, list[str]]:
    """GET the configured AAA page; return HTML or None and human-readable diagnostic lines."""
    page_url = page_url_from_settings(settings)
    user_agent = user_agent_from_settings(settings)
    headers = _aaa_browser_headers(page_url, user_agent)
    diagnostics: list[str] = [
        f"URL: {page_url}",
        "Using browser-style headers + single session (cookies kept between retries).",
    ]
    ua_preview = user_agent if len(user_agent) <= 96 else user_agent[:93] + "…"
    diagnostics.append(f"User-Agent: {ua_preview}")
    last_exc: BaseException | None = None
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            headers=headers,
            follow_redirects=True,
        ) as client:
            for attempt in range(1, _MAX_FETCH_ATTEMPTS + 1):
                diagnostics.append(f"Attempt {attempt}/{_MAX_FETCH_ATTEMPTS}: GET …")
                try:
                    response = await client.get(page_url)
                except (httpx.HTTPError, OSError) as exc:
                    last_exc = exc
                    diagnostics.append(f"Error: {type(exc).__name__}: {exc}")
                    LOGGER.warning(
                        "AAA gas: fetch failed (attempt %s/%s): %s",
                        attempt,
                        _MAX_FETCH_ATTEMPTS,
                        exc,
                    )
                    if attempt < _MAX_FETCH_ATTEMPTS:
                        await asyncio.sleep(_BACKOFF_BASE_SECONDS ** (attempt - 1))
                    continue
                code = response.status_code
                if code >= 400:
                    diagnostics.append(f"HTTP {code} (failure)")
                    # Surface WAF hints (e.g. Cloudflare ray id) for logs / support.
                    server = response.headers.get("server") or response.headers.get("Server")
                    cf_ray = response.headers.get("cf-ray") or response.headers.get("CF-RAY")
                    if server:
                        diagnostics.append(f"Server header: {server}")
                    if cf_ray:
                        diagnostics.append(f"CF-Ray: {cf_ray}")
                    LOGGER.warning(
                        "AAA gas: HTTP %s (attempt %s/%s)",
                        code,
                        attempt,
                        _MAX_FETCH_ATTEMPTS,
                    )
                    if attempt < _MAX_FETCH_ATTEMPTS:
                        await asyncio.sleep(_BACKOFF_BASE_SECONDS ** (attempt - 1))
                    continue
                text = response.text
                n_chars = len(text)
                n_bytes = len(response.content)
                diagnostics.append(f"HTTP {code} OK")
                diagnostics.append(f"Body: ~{n_chars} characters (~{n_bytes} bytes)")
                return text, diagnostics
    except (httpx.HTTPError, OSError) as exc:
        last_exc = exc
        diagnostics.append(f"Client error: {type(exc).__name__}: {exc}")
        LOGGER.warning("AAA gas: client setup or fatal: %s", exc)
    if last_exc is not None:
        diagnostics.append(f"Failed after {_MAX_FETCH_ATTEMPTS} attempts: {last_exc}")
        LOGGER.error("AAA gas: fetch exhausted retries: %s", last_exc)
    else:
        diagnostics.append(f"Failed after {_MAX_FETCH_ATTEMPTS} attempts (HTTP errors)")
        diagnostics.append(
            "_Tip: persistent 403 often means the host blocks datacenter IPs (e.g. cloud workers). "
            "Set `AAA_GAS_HTTP_USER_AGENT` to your desktop Chrome UA, use a proxy/residential IP, "
            "or run the bot locally._"
        )
        LOGGER.error(
            "AAA gas: could not fetch page after %s attempts (HTTP errors)",
            _MAX_FETCH_ATTEMPTS,
        )
    return None, diagnostics


async def apply_aaa_snapshot(
    store: StateStore,
    notify: NotifyFn,
    *,
    settings: Settings,
    price: str,
    as_of: str,
) -> dict[str, Any]:
    """Persist snapshot and optionally notify; same rules as the scheduled worker tick."""
    worker_id = AAA_NATIONAL_GAS_WORKER_ID
    page_url = page_url_from_settings(settings)
    default_poll = clamp_poll_interval_seconds(settings.aaa_gas_poll_interval_seconds)

    raw_state = store.get_worker_payload(worker_id)
    data = load_worker_state_dict(raw_state)

    if not data["settings"].get("poll_interval_seconds"):
        data.setdefault("settings", {})["poll_interval_seconds"] = default_poll

    prev_snap = data.get("snapshot") or {}
    prev_price = prev_snap.get("price") if isinstance(prev_snap, dict) else None
    prev_as_of = prev_snap.get("as_of") if isinstance(prev_snap, dict) else None

    new_snap = {"price": price, "as_of": as_of}
    is_first_baseline = prev_price is None and prev_as_of is None

    if not is_first_baseline and prev_price == price and prev_as_of == as_of:
        return {"outcome": "unchanged", "alert_sent": False}

    if is_first_baseline:
        data["snapshot"] = new_snap
        store.set_worker_payload(worker_id, json.dumps(data, sort_keys=True))
        LOGGER.info(
            "AAA gas: baseline snapshot stored (no alert): price=%s as_of=%s",
            price,
            as_of,
        )
        return {"outcome": "baseline", "alert_sent": False}

    payload = build_aaa_notification_payload(price, as_of, page_url)
    await notify(payload)
    data["snapshot"] = new_snap
    store.set_worker_payload(worker_id, json.dumps(data, sort_keys=True))
    return {"outcome": "changed", "alert_sent": True}


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
        self._default_poll_interval_seconds = clamp_poll_interval_seconds(
            settings.aaa_gas_poll_interval_seconds
        )
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
        html, _diagnostics = await fetch_aaa_page_html(self._settings)
        if html is None:
            return

        parsed = parse_aaa_national_snapshot(
            html,
            table_grade=self._settings.aaa_gas_table_grade,
        )
        if parsed is None:
            return

        price, as_of = parsed
        await apply_aaa_snapshot(
            self._store,
            self._notify,
            settings=self._settings,
            price=price,
            as_of=as_of,
        )
