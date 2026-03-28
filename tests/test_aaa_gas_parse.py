"""Unit tests for AAA national gas HTML parsing and poll-interval merge."""

from __future__ import annotations

import asyncio
import json
import os

from config import Settings
from state.store import StateStore
from workers.aaa_national_gas import (
    AAA_NATIONAL_GAS_WORKER_ID,
    apply_aaa_snapshot,
    load_worker_state_dict,
    merge_poll_interval_into_stored_state,
    parse_aaa_national_snapshot,
)


def _test_settings() -> Settings:
    return Settings(
        discord_token="x",
        alert_channel_id=1,
        monitor_guild_id=1,
        state_db_path="data/state.db",
    )


async def _run_apply_snapshot(
    store: StateStore,
    *,
    price: str,
    as_of: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    notified: list[dict[str, object]] = []

    async def notify(p: dict) -> None:
        notified.append(p)

    r = await apply_aaa_snapshot(
        store,
        notify,
        settings=_test_settings(),
        price=price,
        as_of=as_of,
    )
    return r, notified

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "aaa_gas_sample.html")


def test_parse_aaa_national_snapshot_happy_path() -> None:
    with open(_FIXTURE, encoding="utf-8") as f:
        html = f.read()
    out = parse_aaa_national_snapshot(html)
    assert out == ("3.976", "3/28/26")


def test_parse_aaa_national_snapshot_missing_markup() -> None:
    assert parse_aaa_national_snapshot("<html><body></body></html>") is None


def test_load_worker_state_dict_empty_and_invalid() -> None:
    assert load_worker_state_dict(None) == {"settings": {}, "snapshot": {}}
    assert load_worker_state_dict("") == {"settings": {}, "snapshot": {}}
    assert load_worker_state_dict("not json") == {"settings": {}, "snapshot": {}}


def test_merge_poll_interval_into_stored_state_roundtrip(tmp_path: object) -> None:
    db = tmp_path / "state.db"
    store = StateStore(str(db))
    prev, new = merge_poll_interval_into_stored_state(store, 120)
    assert prev is None
    assert new == 120
    prev2, new2 = merge_poll_interval_into_stored_state(store, 300)
    assert prev2 == 120
    assert new2 == 300
    raw = store.get_worker_payload("aaa-national-gas")
    assert raw is not None
    data = load_worker_state_dict(raw)
    assert data["settings"]["poll_interval_seconds"] == 300


def test_merge_poll_interval_clamps(tmp_path: object) -> None:
    db = tmp_path / "state.db"
    store = StateStore(str(db))
    _, new = merge_poll_interval_into_stored_state(store, 30)
    assert new == 60
    _, new2 = merge_poll_interval_into_stored_state(store, 999999)
    assert new2 == 86400


def test_apply_aaa_snapshot_baseline(tmp_path: object) -> None:
    db = tmp_path / "state.db"
    store = StateStore(str(db))
    r, notified = asyncio.run(
        _run_apply_snapshot(store, price="3.0", as_of="1/1/26"),
    )
    assert r == {"outcome": "baseline", "alert_sent": False}
    assert notified == []


def test_apply_aaa_snapshot_unchanged(tmp_path: object) -> None:
    db = tmp_path / "state.db"
    store = StateStore(str(db))
    store.set_worker_payload(
        AAA_NATIONAL_GAS_WORKER_ID,
        json.dumps(
            {
                "settings": {"poll_interval_seconds": 300},
                "snapshot": {"price": "3.0", "as_of": "1/1/26"},
            }
        ),
    )
    r, notified = asyncio.run(
        _run_apply_snapshot(store, price="3.0", as_of="1/1/26"),
    )
    assert r == {"outcome": "unchanged", "alert_sent": False}
    assert notified == []


def test_apply_aaa_snapshot_changed_alerts(tmp_path: object) -> None:
    db = tmp_path / "state.db"
    store = StateStore(str(db))
    store.set_worker_payload(
        AAA_NATIONAL_GAS_WORKER_ID,
        json.dumps(
            {
                "settings": {"poll_interval_seconds": 300},
                "snapshot": {"price": "2.0", "as_of": "1/1/25"},
            }
        ),
    )
    r, notified = asyncio.run(
        _run_apply_snapshot(store, price="3.0", as_of="1/1/26"),
    )
    assert r == {"outcome": "changed", "alert_sent": True}
    assert len(notified) == 1
    assert notified[0].get("title") == "AAA national average updated"
