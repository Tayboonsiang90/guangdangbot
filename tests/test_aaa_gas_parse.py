"""Unit tests for AAA national gas HTML parsing and poll-interval merge."""

from __future__ import annotations

import os

from state.store import StateStore
from workers.aaa_national_gas import (
    load_worker_state_dict,
    merge_poll_interval_into_stored_state,
    parse_aaa_national_snapshot,
)

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
