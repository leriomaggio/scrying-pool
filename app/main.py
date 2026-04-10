"""
The Scrying Pool: FastAPI backend.

Three views, one game engine, one WebSocket hub.

Run locally:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Env vars (see README):
    HOST_PASSWORD   : password for the host dashboard (default: "pyconde2026")
    STORY_FILE      : path to the story.json (default: app/data/story.json)
    ROUND_DURATION  : seconds per voting round (default: 30)
    SNAPSHOT_FILE   : path for state snapshot persistence (default: /tmp/scrying_snapshot.json)
    PUBLIC_URL      : public URL for QR code on the big screen (default: http://localhost:8000)
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request, Form, Cookie, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .game import GameEngine, Strategy, GamePhase


# ------------------------------------------------------------------ config

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_STORY = BASE_DIR / "data" / "story.json"

HOST_PASSWORD = os.environ.get("HOST_PASSWORD", "pyconde2026")
STORY_FILE = Path(os.environ.get("STORY_FILE", str(DEFAULT_STORY)))
ROUND_DURATION = int(os.environ.get("ROUND_DURATION", "30"))
SNAPSHOT_FILE = Path(os.environ.get("SNAPSHOT_FILE", "/tmp/scrying_snapshot.json"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000")

# Session tokens for host auth (in-memory; one process, OK for a single-host game)
_host_tokens: set[str] = set()


# ------------------------------------------------------------------ engine + hub

engine = GameEngine(story_path=STORY_FILE, round_duration_s=ROUND_DURATION)


BROADCAST_INTERVAL_S = 0.25  # coalesce state updates to at most 4 Hz during voting


class ConnectionHub:
    """Tracks WebSocket connections by role and broadcasts state updates.

    Broadcasts are *coalesced*: callers flag the state as dirty via
    ``mark_dirty()``, and the background flusher in ``run_flusher()`` fans
    out at most once every ``BROADCAST_INTERVAL_S`` seconds. Phase
    transitions call ``broadcast_now()`` for instant updates, so the host's
    Start/Reveal/Next buttons still feel snappy.

    Each broadcast serialises the payload *once* (via ``json.dumps``) and
    sends the same bytes to every client, instead of calling
    ``ws.send_json``, which would re-serialise per client. With 1000
    audience members this is the difference between 1 and 1000 JSON
    encodings per broadcast.
    """

    def __init__(self) -> None:
        self.audience: set[WebSocket] = set()
        self.screen: set[WebSocket] = set()
        self.host: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._dirty = asyncio.Event()

    async def connect(self, ws: WebSocket, role: str) -> None:
        await ws.accept()
        async with self._lock:
            getattr(self, role).add(ws)

    async def disconnect(self, ws: WebSocket, role: str) -> None:
        async with self._lock:
            getattr(self, role).discard(ws)

    def mark_dirty(self) -> None:
        """Flag that the state changed; the flusher will broadcast soon."""
        self._dirty.set()

    async def broadcast_now(self) -> None:
        """Skip the coalescing delay; broadcast immediately. Use for phase
        transitions so the host controls feel instant."""
        self._dirty.clear()
        public_bytes = json.dumps({"type": "state", "data": engine.public_state()})
        host_bytes = json.dumps({"type": "state", "data": engine.host_state()})
        await asyncio.gather(
            self._send_to_set(self.audience, public_bytes),
            self._send_to_set(self.screen, public_bytes),
            self._send_to_set(self.host, host_bytes),
        )

    async def run_flusher(self) -> None:
        """Background task: whenever dirty, broadcast at most every
        BROADCAST_INTERVAL_S. Runs for the lifetime of the app."""
        while True:
            try:
                await self._dirty.wait()
                await asyncio.sleep(BROADCAST_INTERVAL_S)
                await self.broadcast_now()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[flusher] {e}")

    async def _send_to_set(self, clients: set[WebSocket], payload_text: str) -> None:
        # Snapshot the set to avoid mutation during iteration.
        targets = list(clients)
        if not targets:
            return
        # Fan out in parallel; one slow client won't block the others.
        results = await asyncio.gather(
            *(ws.send_text(payload_text) for ws in targets),
            return_exceptions=True,
        )
        for ws, result in zip(targets, results):
            if isinstance(result, Exception):
                clients.discard(ws)


hub = ConnectionHub()


# ------------------------------------------------------------------ snapshot persistence

def save_snapshot() -> None:
    try:
        SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with SNAPSHOT_FILE.open("w", encoding="utf-8") as f:
            json.dump(engine.snapshot(), f)
    except Exception as e:
        print(f"[snapshot] save failed: {e}")


def load_snapshot() -> None:
    if not SNAPSHOT_FILE.exists():
        return
    try:
        with SNAPSHOT_FILE.open("r", encoding="utf-8") as f:
            snap = json.load(f)
        engine.restore(snap)
        print(f"[snapshot] restored from {SNAPSHOT_FILE} (round {engine.current_round_index}, phase {engine.phase.value})")
    except Exception as e:
        print(f"[snapshot] load failed: {e}")


# ------------------------------------------------------------------ countdown ticker

async def voting_watchdog() -> None:
    """Nudges the broadcast flusher once per second during voting so
    audience countdown timers stay in sync even when no new votes arrive.
    The flusher coalesces this with any vote-triggered dirty flags."""
    while True:
        try:
            await asyncio.sleep(1)
            if engine.phase == GamePhase.VOTING:
                hub.mark_dirty()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[watchdog] {e}")


# ------------------------------------------------------------------ lifespan

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_snapshot()
    watchdog_task = asyncio.create_task(voting_watchdog())
    flusher_task = asyncio.create_task(hub.run_flusher())
    yield
    watchdog_task.cancel()
    flusher_task.cancel()
    for t in (watchdog_task, flusher_task):
        try:
            await t
        except asyncio.CancelledError:
            pass
    save_snapshot()


app = FastAPI(title="The Scrying Pool", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ------------------------------------------------------------------ auth helpers

def require_host(scrying_host: str | None = Cookie(default=None)) -> None:
    if scrying_host is None or scrying_host not in _host_tokens:
        raise HTTPException(status_code=401, detail="Not authenticated")


# ------------------------------------------------------------------ HTTP routes

@app.get("/", response_class=HTMLResponse)
async def audience_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "audience.html").read_text(encoding="utf-8"))


@app.get("/screen", response_class=HTMLResponse)
async def screen_page() -> HTMLResponse:
    html = (STATIC_DIR / "screen.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("{{PUBLIC_URL}}", PUBLIC_URL))


@app.get("/host", response_class=HTMLResponse)
async def host_page(scrying_host: str | None = Cookie(default=None)) -> HTMLResponse:
    if scrying_host is None or scrying_host not in _host_tokens:
        return HTMLResponse((STATIC_DIR / "host_login.html").read_text(encoding="utf-8"))
    return HTMLResponse((STATIC_DIR / "host.html").read_text(encoding="utf-8"))


@app.post("/host/login")
async def host_login(password: str = Form(...)) -> Response:
    if password != HOST_PASSWORD:
        return HTMLResponse(
            (STATIC_DIR / "host_login.html").read_text(encoding="utf-8").replace(
                "<!--ERROR-->", "<p class='err'>Wrong password. The pool rejects you.</p>"
            ),
            status_code=401,
        )
    token = secrets.token_urlsafe(24)
    _host_tokens.add(token)
    resp = RedirectResponse(url="/host", status_code=303)
    resp.set_cookie("scrying_host", token, httponly=True, samesite="lax", max_age=60 * 60 * 8)
    return resp


@app.post("/host/logout")
async def host_logout(scrying_host: str | None = Cookie(default=None)) -> Response:
    if scrying_host:
        _host_tokens.discard(scrying_host)
    resp = RedirectResponse(url="/host", status_code=303)
    resp.delete_cookie("scrying_host")
    return resp


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True, "phase": engine.phase.value, "round": engine.current_round_index, "version": engine.version}


# ------------------------------------------------------------------ WebSocket endpoints

@app.websocket("/ws/audience")
async def ws_audience(ws: WebSocket) -> None:
    await hub.connect(ws, "audience")
    client_id = ws.headers.get("sec-websocket-key") or secrets.token_hex(8)
    try:
        # Send initial state only to *this* client so it renders immediately.
        await ws.send_text(json.dumps({"type": "state", "data": engine.public_state()}))
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "vote":
                option_index = int(msg.get("option_index", -1))
                # Allow the audience to provide their own stable client id (localStorage)
                cid = str(msg.get("client_id") or client_id)
                if engine.record_vote(cid, option_index):
                    # Coalesced broadcast: the flusher fans out at most every
                    # BROADCAST_INTERVAL_S, not once per individual vote.
                    # Snapshots are written on phase transitions, not per-vote,
                    # since in-flight votes are ephemeral anyway.
                    hub.mark_dirty()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(ws, "audience")


@app.websocket("/ws/screen")
async def ws_screen(ws: WebSocket) -> None:
    await hub.connect(ws, "screen")
    try:
        await ws.send_text(json.dumps({"type": "state", "data": engine.public_state()}))
        while True:
            # Screen view is read-only; just keep the socket alive
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(ws, "screen")


@app.websocket("/ws/host")
async def ws_host(ws: WebSocket) -> None:
    # Authenticate via cookie sent during WS handshake
    token = None
    cookie_header = ws.headers.get("cookie", "")
    for part in cookie_header.split(";"):
        if "scrying_host=" in part:
            token = part.strip().split("=", 1)[1]
            break
    if token not in _host_tokens:
        await ws.close(code=1008)  # policy violation
        return

    await hub.connect(ws, "host")
    try:
        await ws.send_text(json.dumps({"type": "state", "data": engine.host_state()}))
        while True:
            msg = await ws.receive_json()
            cmd = msg.get("cmd")
            if cmd == "start_round":
                engine.start_round()
            elif cmd == "end_voting":
                engine.end_voting()
            elif cmd == "reveal":
                engine.reveal()
            elif cmd == "next_round":
                engine.next_round()
            elif cmd == "show_final":
                engine.show_final()
            elif cmd == "reset":
                engine.reset()
            elif cmd == "override_word":
                idx = msg.get("option_index")
                engine.set_host_override(int(idx) if idx is not None else None)
            elif cmd == "override_strategy":
                strat = msg.get("strategy")
                if strat:
                    try:
                        engine.override_strategy(Strategy(strat))
                    except ValueError:
                        pass
            # Host commands are phase transitions → broadcast immediately
            # (no coalescing delay) and persist to disk so a mid-session
            # restart resumes in the right place.
            await hub.broadcast_now()
            save_snapshot()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(ws, "host")
