"""Unit tests for Bonbast JSON parsing, poll-interval merge, and apply_snapshot."""

from __future__ import annotations

import asyncio
import json

from config import Settings
from state.store import StateStore
from workers.bonbast_rates import (
    BONBAST_WORKER_ID,
    apply_bonbast_snapshot,
    extract_token_from_home_html,
    load_bonbast_worker_state_dict,
    merge_bonbast_poll_interval_into_stored_state,
    parse_sell_buy_from_json,
)


def _test_settings() -> Settings:
    return Settings(
        discord_token="x",
        alert_channel_id=1,
        monitor_guild_id=1,
        state_db_path="data/state.db",
    )


async def _run_apply_bonbast(
    store: StateStore,
    *,
    sell: int,
    buy: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    notified: list[dict[str, object]] = []

    async def notify(p: dict) -> None:
        notified.append(p)

    r = await apply_bonbast_snapshot(
        store,
        notify,
        settings=_test_settings(),
        sell=sell,
        buy=buy,
    )
    return r, notified


def test_parse_sell_buy_from_json_usd() -> None:
    data = {"usd1": 157_550, "usd2": 157_450, "eur1": 1}
    assert parse_sell_buy_from_json(data, "usd") == (157_550, 157_450)


def test_parse_sell_buy_from_json_missing_key() -> None:
    assert parse_sell_buy_from_json({"usd1": 100}, "usd") is None


def test_parse_sell_buy_from_json_non_int() -> None:
    assert parse_sell_buy_from_json({"usd1": "x", "usd2": 1}, "usd") is None


def test_extract_token_from_home_html() -> None:
    html = '<script> var param = "abc,token123"; </script>'
    assert extract_token_from_home_html(html) == "abc,token123"


def test_load_bonbast_worker_state_dict_empty_and_invalid() -> None:
    assert load_bonbast_worker_state_dict(None) == {"settings": {}, "snapshot": {}}
    assert load_bonbast_worker_state_dict("") == {"settings": {}, "snapshot": {}}
    assert load_bonbast_worker_state_dict("not json") == {"settings": {}, "snapshot": {}}


def test_merge_bonbast_poll_interval_roundtrip(tmp_path: object) -> None:
    db = tmp_path / "state.db"
    store = StateStore(str(db))
    prev, new = merge_bonbast_poll_interval_into_stored_state(store, 120)
    assert prev is None
    assert new == 120
    prev2, new2 = merge_bonbast_poll_interval_into_stored_state(store, 300)
    assert prev2 == 120
    assert new2 == 300
    raw = store.get_worker_payload("bonbast-usd")
    assert raw is not None
    data = load_bonbast_worker_state_dict(raw)
    assert data["settings"]["poll_interval_seconds"] == 300


def test_merge_bonbast_poll_clamps(tmp_path: object) -> None:
    db = tmp_path / "state.db"
    store = StateStore(str(db))
    _, new = merge_bonbast_poll_interval_into_stored_state(store, 30)
    assert new == 60


def test_apply_bonbast_snapshot_baseline(tmp_path: object) -> None:
    db = tmp_path / "state.db"
    store = StateStore(str(db))
    r, notified = asyncio.run(
        _run_apply_bonbast(store, sell=100, buy=99),
    )
    assert r == {"outcome": "baseline", "alert_sent": False}
    assert notified == []


def test_apply_bonbast_snapshot_unchanged(tmp_path: object) -> None:
    db = tmp_path / "state.db"
    store = StateStore(str(db))
    store.set_worker_payload(
        BONBAST_WORKER_ID,
        json.dumps(
            {
                "settings": {"poll_interval_seconds": 300},
                "snapshot": {"sell": 100, "buy": 99},
            }
        ),
    )
    r, notified = asyncio.run(
        _run_apply_bonbast(store, sell=100, buy=99),
    )
    assert r == {"outcome": "unchanged", "alert_sent": False}
    assert notified == []


def test_apply_bonbast_snapshot_changed_alerts(tmp_path: object) -> None:
    db = tmp_path / "state.db"
    store = StateStore(str(db))
    store.set_worker_payload(
        BONBAST_WORKER_ID,
        json.dumps(
            {
                "settings": {"poll_interval_seconds": 300},
                "snapshot": {"sell": 100, "buy": 99},
            }
        ),
    )
    r, notified = asyncio.run(
        _run_apply_bonbast(store, sell=101, buy=99),
    )
    assert r == {"outcome": "changed", "alert_sent": True}
    assert len(notified) == 1
    assert notified[0].get("title") == "Bonbast USD updated"
