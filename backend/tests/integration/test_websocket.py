#!/usr/bin/env python3.12
"""WebSocket tests for /ws/odds endpoint."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
from models import OddsDelta, OddsEntry
from orchestrator import EVICT_SENTINEL


class _SequenceQueue:
    def __init__(self, items):
        self._items = list(items)

    async def get(self):
        if self._items:
            return self._items.pop(0)
        return EVICT_SENTINEL


class _FakeOrchestrator:
    def __init__(self, current, queue_items, *, allow_subscribe: bool = True):
        self._current = current
        self._queue_items = queue_items
        self._allow_subscribe = allow_subscribe
        self._subscribers = []

    def get_current(self):
        return list(self._current)

    def subscribe(self):
        if not self._allow_subscribe:
            return None
        q = _SequenceQueue(self._queue_items)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, subscriber):
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)


def _entry() -> OddsEntry:
    return OddsEntry(
        game_id="a_vs_b_MATCH_ODDS_home",
        game_time="01-03 20:00",
        game_name="A v B",
        market="MATCH_ODDS",
        selection="Home",
        bookmaker="novibet",
        back_odds=2.3,
        lay_odds=2.1,
        lay_available=120.0,
        ls1=2.1,
        ls2=2.12,
        ls3=2.15,
        diff=0.2,
        is_value=True,
        updated_at=datetime.now(timezone.utc),
    )


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.websocket("/ws/odds")(main.ws_odds)
    return app


def test_websocket_receives_full_state_and_delta(monkeypatch) -> None:
    entry = _entry()
    delta = OddsDelta(
        type="delta",
        changed=[entry],
        removed=[],
        timestamp=datetime.now(timezone.utc),
    )
    fake = _FakeOrchestrator([entry], [delta, EVICT_SENTINEL])
    monkeypatch.setattr(main, "orchestrator", fake)

    with TestClient(_build_test_app()) as client:
        with client.websocket_connect("/ws/odds") as ws:
            full = ws.receive_json()
            assert full["type"] == "full"
            assert len(full["odds"]) == 1

            delta_msg = ws.receive_json()
            assert delta_msg["type"] == "delta"
            assert len(delta_msg["changed"]) == 1
            assert delta_msg["changed"][0]["game_id"] == entry.game_id


def test_websocket_returns_error_when_orchestrator_not_ready(monkeypatch) -> None:
    monkeypatch.setattr(main, "orchestrator", None)

    with TestClient(_build_test_app()) as client:
        with client.websocket_connect("/ws/odds") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "orchestrator not ready" in msg["message"]


def test_websocket_returns_error_when_subscriber_limit_hit(monkeypatch) -> None:
    fake = _FakeOrchestrator([], [], allow_subscribe=False)
    monkeypatch.setattr(main, "orchestrator", fake)

    with TestClient(_build_test_app()) as client:
        with client.websocket_connect("/ws/odds") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "subscriber limit reached" in msg["message"]
