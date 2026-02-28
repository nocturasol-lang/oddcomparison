"""
FastAPI app: REST + WebSocket odds API, health, CORS.
Run: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from scraper.orchestrator import OddsOrchestrator
from models import OddsEntry, OddsDelta

# Global orchestrator (set in lifespan)
orchestrator: OddsOrchestrator | None = None
_start_time: float = 0.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator, _start_time
    _start_time = time.monotonic()
    orchestrator = OddsOrchestrator()
    try:
        await orchestrator.start()
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Orchestrator start failed (app still runs): %s", e)
    yield
    orchestrator._running = False
    if orchestrator._poll_task and not orchestrator._poll_task.done():
        orchestrator._poll_task.cancel()
        try:
            await orchestrator._poll_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="ODDSHAWK API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/odds")
async def get_odds():
    """Return current merged odds snapshot."""
    if orchestrator is None:
        return {"odds": [], "count": 0}
    odds = orchestrator.get_current()
    # Pydantic models serialize in response
    return {"odds": odds, "count": len(odds)}


@app.websocket("/ws/odds")
async def ws_odds(websocket: WebSocket):
    """Subscribe to live odds: full state on connect, then deltas."""
    await websocket.accept()

    if orchestrator is None:
        await websocket.send_json({"type": "error", "message": "orchestrator not ready"})
        await websocket.close()
        return

    queue = orchestrator.subscribe()

    try:
        # Send full state immediately
        current = orchestrator.get_current()
        payload = {
            "type": "full",
            "odds": [e.model_dump(mode="json") for e in current],
        }
        await websocket.send_json(payload)

        # Loop: wait for delta from queue, send as JSON
        while True:
            delta: OddsDelta = await queue.get()
            await websocket.send_json(
                {
                    "type": "delta",
                    "changed": [e.model_dump(mode="json") for e in delta.changed],
                    "removed": delta.removed,
                    "timestamp": delta.timestamp.isoformat(),
                }
            )
    except WebSocketDisconnect:
        pass
    finally:
        orchestrator.unsubscribe(queue)


@app.get("/api/test")
async def test():
    """Simple test endpoint."""
    return {"status": "ok"}


@app.get("/api/health")
async def health():
    """Health check with odds count and uptime."""
    if orchestrator is None:
        return {"status": "starting", "odds_count": 0, "uptime": 0.0}
    uptime = time.monotonic() - _start_time
    count = len(orchestrator.get_current())
    return {"status": "ok", "odds_count": count, "uptime": round(uptime, 2)}
