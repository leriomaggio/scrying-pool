"""
Full end-to-end simulation: spins up the server, connects host + audience
WebSocket clients, and drives through both stories automatically.

Tests the real HTTP/WS stack, not just the engine in isolation.
"""
from __future__ import annotations

import asyncio
import json
import random
import subprocess
import sys
import time
import uuid

try:
    import websockets
except ImportError:
    sys.exit("Need websockets: pip install websockets")

try:
    import httpx
except ImportError:
    httpx = None  # we'll use urllib instead

PORT = 18765  # high port to avoid clashes
BASE = f"http://localhost:{PORT}"
WS_BASE = f"ws://localhost:{PORT}"
HOST_PASSWORD = "e2e-test-pass"

# ---- helpers ----

def banner(msg: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {msg}")
    print(f"{'=' * 60}")


def ok(msg: str) -> None:
    print(f"  \u2713 {msg}")


def fail(msg: str) -> None:
    print(f"  \u2717 FAIL: {msg}")
    sys.exit(1)


async def wait_for_server(timeout: float = 10.0) -> None:
    """Poll until the server responds on /health or /."""
    import urllib.request
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/", timeout=2)
            return
        except Exception:
            await asyncio.sleep(0.3)
    fail(f"Server did not start within {timeout}s")


async def host_login() -> str:
    """Log in as host via HTTP POST /host/login, return the session cookie."""
    import urllib.request
    import urllib.parse
    data = urllib.parse.urlencode({"password": HOST_PASSWORD}).encode()
    req = urllib.request.Request(
        f"{BASE}/host/login", data=data, method="POST",
    )
    # Disable redirect following so we can grab the cookie
    import http.client
    conn = http.client.HTTPConnection("localhost", PORT)
    conn.request("POST", "/host/login", body=data,
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    cookie = resp.getheader("Set-Cookie")
    conn.close()
    if not cookie:
        fail("No session cookie from /host/login")
    # Extract just the cookie value
    return cookie.split(";")[0]


# ---- audience voter ----

async def audience_voter(
    voter_id: int,
    ready: asyncio.Event,
    stop: asyncio.Event,
    results: dict,
) -> None:
    """One fake audience member that votes whenever a VOTING round appears."""
    client_id = f"sim-{voter_id}-{uuid.uuid4().hex[:6]}"
    url = f"{WS_BASE}/ws/audience"
    await ready.wait()
    try:
        async with websockets.connect(url, open_timeout=10) as ws:
            last_round_id = None
            async for raw in ws:
                if stop.is_set():
                    break
                msg = json.loads(raw)
                if msg.get("type") != "state":
                    continue
                state = msg["data"]
                rnd = state.get("round") or {}
                phase = state.get("phase")
                rid = rnd.get("id")
                options = rnd.get("options", [])

                if phase == "final":
                    # Record the final story for verification
                    results["final_story"] = state.get("final_story")
                    break

                if phase != "voting" or not options or rid == last_round_id:
                    continue

                last_round_id = rid
                await asyncio.sleep(random.uniform(0.05, 0.4))
                choice = random.randrange(len(options))
                await ws.send(json.dumps({
                    "type": "vote",
                    "option_index": choice,
                    "client_id": client_id,
                }))
                results.setdefault("votes_cast", 0)
                results["votes_cast"] = results.get("votes_cast", 0) + 1
    except Exception as e:
        results.setdefault("errors", []).append(str(e))


# ---- host driver ----

async def host_driver(
    cookie: str,
    story_key: str,
    num_rounds: int,
    num_voters: int,
    ready: asyncio.Event,
    stop: asyncio.Event,
    results: dict,
) -> None:
    """Connect as host, select story, drive all rounds, then show final."""
    url = f"{WS_BASE}/ws/host"
    additional_headers = {"Cookie": cookie}

    async with websockets.connect(url, open_timeout=10, additional_headers=additional_headers) as ws:
        # Wait for initial state
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        state = json.loads(raw)["data"]
        ok(f"Host connected, initial phase: {state['phase']}")
        results["initial_story"] = state.get("story_key")

        # Select story if needed
        if state.get("story_key") != story_key:
            await ws.send(json.dumps({"cmd": "select_story", "story_key": story_key}))
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            state = json.loads(raw)["data"]
            ok(f"Selected story: {state.get('story_key')}")

        # Set a fast duration for the simulation
        await ws.send(json.dumps({"cmd": "set_duration", "seconds": 5}))
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        state = json.loads(raw)["data"]
        ok(f"Round duration set to {state.get('round_duration_s')}s")

        # Signal audience voters to connect
        ready.set()
        await asyncio.sleep(0.5)  # let voters connect

        strategies_seen = []

        for round_idx in range(num_rounds):
            # Start round
            await ws.send(json.dumps({"cmd": "start_round"}))
            state = await _wait_for_phase(ws, "voting", timeout=5)
            rnd = state["round"]
            strategies_seen.append(rnd.get("strategy"))
            question = rnd["poll_question"]
            ok(f"Round {round_idx + 1}/{num_rounds}: \"{question}\" "
               f"[slot={rnd['story_slot']}, strategy={rnd['strategy']}]")

            # Let voters vote for a bit
            await asyncio.sleep(1.5)

            # End voting
            await ws.send(json.dumps({"cmd": "end_voting"}))
            await asyncio.sleep(0.3)

            # Reveal
            await ws.send(json.dumps({"cmd": "reveal"}))
            state = await _wait_for_phase(ws, "revealed", timeout=5)
            rnd = state["round"]
            winner = rnd.get("winning_word", "???")
            votes = rnd.get("total_votes", 0)
            ok(f"  Winner: \"{winner}\" ({votes} votes)")

            if votes == 0:
                results.setdefault("warnings", []).append(
                    f"Round {round_idx + 1} had 0 votes"
                )

            # Next round (or final)
            if round_idx < num_rounds - 1:
                await ws.send(json.dumps({"cmd": "next_round"}))
                state = await _wait_for_phase(ws, "waiting", timeout=5)
            else:
                await ws.send(json.dumps({"cmd": "next_round"}))
                state = await _wait_for_phase(ws, "final", timeout=5)

        results["strategies"] = strategies_seen
        results["final_state"] = state

        # Show final story
        final_story = state.get("final_story")
        if final_story:
            results["final_story"] = final_story
            ok(f"Final story: {len(final_story)} paragraphs")
        else:
            fail("No final story rendered!")

        # Check for unfilled slots
        import re
        full_text = "\n".join(final_story)
        unfilled = re.findall(r"\{[A-Z_]+\}", full_text)
        if unfilled:
            fail(f"Unfilled slots in final story: {unfilled}")
        else:
            ok("All story slots filled")

        # Signal voters to stop
        stop.set()


async def _wait_for_phase(ws, target_phase: str, timeout: float = 5) -> dict:
    """Read WS messages until we see the target phase."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            break
        msg = json.loads(raw)
        if msg.get("type") == "state" and msg["data"].get("phase") == target_phase:
            return msg["data"]
    fail(f"Timed out waiting for phase '{target_phase}'")


# ---- run one story ----

async def run_story_simulation(
    cookie: str,
    story_key: str,
    num_rounds: int,
    num_voters: int = 20,
) -> dict:
    """Run a full game for one story with simulated audience."""
    ready = asyncio.Event()
    stop = asyncio.Event()
    results: dict = {}

    # Spawn audience voters
    voter_tasks = [
        asyncio.create_task(audience_voter(i, ready, stop, results))
        for i in range(num_voters)
    ]

    # Drive the host
    host_task = asyncio.create_task(
        host_driver(cookie, story_key, num_rounds, num_voters, ready, stop, results)
    )

    await host_task
    # Give voters a moment to see final and disconnect
    await asyncio.sleep(1)
    stop.set()
    # Cancel any lingering voters
    for t in voter_tasks:
        t.cancel()
    await asyncio.gather(*voter_tasks, return_exceptions=True)

    return results


# ---- main ----

async def main() -> None:
    banner("Starting server")
    server_proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "app.main:app",
            "--port", str(PORT),
            "--log-level", "warning",
        ],
        env={
            **__import__("os").environ,
            "HOST_PASSWORD": HOST_PASSWORD,
            "SNAPSHOT_PATH": "/tmp/e2e_snapshot.json",  # isolated snapshot
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    print(f"  Server PID: {server_proc.pid}")

    try:
        await wait_for_server()
        ok("Server is up")

        cookie = await host_login()
        ok("Host logged in")

        # ---------- Story 1: Day 1 (5 rounds, all most_popular) ----------
        banner("STORY 1: The Curse of the Missing Wi-Fi (5 rounds, 20 voters)")
        r1 = await run_story_simulation(cookie, "story1", num_rounds=5, num_voters=20)

        # Verify story1 strategies are all most_popular
        for i, s in enumerate(r1.get("strategies", [])):
            if s != "most_popular":
                fail(f"Story1 round {i+1} strategy was {s}, expected most_popular")
        ok("All 5 rounds used most_popular strategy")
        ok(f"Total votes cast by audience: {r1.get('votes_cast', 0)}")

        if r1.get("warnings"):
            for w in r1["warnings"]:
                print(f"  \u26a0 {w}")

        # Print the final story
        print("\n  --- STORY 1 FINAL ---")
        for p in r1.get("final_story", []):
            print(f"  {p}")
        print("  --- END ---")

        # Reset: need a fresh server state for story2
        # Reconnect as host and reset
        additional_headers = {"Cookie": cookie}
        async with websockets.connect(f"{WS_BASE}/ws/host", open_timeout=10, additional_headers=additional_headers) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            await ws.send(json.dumps({"cmd": "reset"}))
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            ok("Game reset for story 2")

        # ---------- Story 2: Day 2 (10 rounds, mixed strategies) ----------
        banner("STORY 2: The Quest for the Lost Talk (10 rounds, 30 voters)")
        r2 = await run_story_simulation(cookie, "story2", num_rounds=10, num_voters=30)

        # Verify story2 uses mixed strategies
        expected_s2 = [
            "most_popular", "least_popular", "random", "second_place",
            "host_choice", "inverse_momentum", "most_popular", "random",
            "least_popular", "second_place",
        ]
        for i, (got, exp) in enumerate(zip(r2.get("strategies", []), expected_s2)):
            if got != exp:
                fail(f"Story2 round {i+1} strategy was {got}, expected {exp}")
        ok("All 10 rounds used correct strategy rotation")
        ok(f"Total votes cast by audience: {r2.get('votes_cast', 0)}")

        if r2.get("warnings"):
            for w in r2["warnings"]:
                print(f"  \u26a0 {w}")

        # Print the final story
        print("\n  --- STORY 2 FINAL ---")
        for p in r2.get("final_story", []):
            print(f"  {p}")
        print("  --- END ---")

        if r2.get("errors"):
            print(f"  Voter errors: {r2['errors'][:5]}")

        # ---------- Summary ----------
        banner("SIMULATION COMPLETE")
        ok(f"Story 1: 5 rounds, all most_popular, {r1.get('votes_cast', 0)} votes")
        ok(f"Story 2: 10 rounds, 6-strategy rotation, {r2.get('votes_cast', 0)} votes")
        ok("Both stories rendered fully with no unfilled slots")
        print("\n\u2726 All e2e simulations passed! \u2726\n")

    finally:
        server_proc.terminate()
        server_proc.wait(timeout=5)
        ok("Server stopped")


if __name__ == "__main__":
    asyncio.run(main())
