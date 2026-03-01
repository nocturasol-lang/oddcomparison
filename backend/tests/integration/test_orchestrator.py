#!/usr/bin/env python3.12
"""Integration-style tests for orchestrator internals."""

from __future__ import annotations

import asyncio

import pytest

import orchestrator as orchestrator_module
from orchestrator import (
    CIRCUIT_OPEN,
    CIRCUIT_OPEN_SEC,
    OddsOrchestrator,
    SubscriberWrapper,
    _run_scraper_with_timing,
)


@pytest.mark.asyncio
async def test_run_scraper_with_timing_success(sample_scraper_result) -> None:
    result = await _run_scraper_with_timing("novibet", asyncio.sleep(0, result=sample_scraper_result))
    name, payload, success, elapsed_ms = result
    assert name == "novibet"
    assert payload is sample_scraper_result
    assert success is True
    assert elapsed_ms >= 0


@pytest.mark.asyncio
async def test_run_scraper_with_timing_failure() -> None:
    async def _boom():
        raise RuntimeError("scraper failed")

    name, payload, success, elapsed_ms = await _run_scraper_with_timing("bad", _boom())
    assert name == "bad"
    assert payload is None
    assert success is False
    assert elapsed_ms >= 0


@pytest.mark.asyncio
async def test_subscriber_wrapper_tracks_consumption() -> None:
    wrapper = SubscriberWrapper()
    wrapper.put_nowait("hello")
    assert wrapper.last_sent > 0
    data = await wrapper.get()
    assert data == "hello"
    assert wrapper.last_consumed > 0


def test_orchestrator_subscribe_unsubscribe_and_get_current(sample_odds_entry) -> None:
    orch = OddsOrchestrator()
    subscriber = orch.subscribe()
    assert subscriber is not None
    assert len(orch._subscribers) == 1
    orch.current_odds = [sample_odds_entry]
    out = orch.get_current()
    assert out == [sample_odds_entry]
    assert out is not orch.current_odds
    orch.unsubscribe(subscriber)
    assert len(orch._subscribers) == 0


def test_circuit_opens_after_consecutive_failures() -> None:
    orch = OddsOrchestrator()
    name = "novibet"
    for _ in range(orchestrator_module.CIRCUIT_FAILURE_THRESHOLD):
        orch._record_failure(name)
    state = orch._circuit_state(name)
    assert state["state"] == CIRCUIT_OPEN


def test_circuit_half_open_after_cooldown(monkeypatch) -> None:
    orch = OddsOrchestrator()
    name = "novibet"
    state = orch._circuit_state(name)
    state["state"] = CIRCUIT_OPEN
    state["opened_at"] = 10.0

    monkeypatch.setattr(orchestrator_module.time, "monotonic", lambda: 10.0 + CIRCUIT_OPEN_SEC + 0.1)
    should_run, should_restart = orch._should_run_scraper(name)
    assert should_run is True
    assert should_restart is True


@pytest.mark.asyncio
async def test_heartbeat_eviction_removes_stale_subscribers(monkeypatch) -> None:
    orch = OddsOrchestrator()
    stale = orch.subscribe()
    assert stale is not None
    stale.last_consumed = orchestrator_module.time.monotonic() - 100.0

    monkeypatch.setattr(orchestrator_module, "HEARTBEAT_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(orchestrator_module, "STALE_CONSUMER_SEC", 0.01)

    orch._running = True
    task = asyncio.create_task(orch._heartbeat_loop())
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert stale not in orch._subscribers
