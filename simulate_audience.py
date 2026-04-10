"""
Fake audience load simulator for The Scrying Pool.

Spins up N WebSocket clients against a locally running instance and has them
vote on whichever round is currently in VOTING phase. Useful for:

  * watching the live bar chart animate on /screen and /host
  * exercising the vote-replacement path (same client_id, different option)
  * testing strategies that need enough voters to be meaningful (least_popular,
    second_place, inverse_momentum)
  * smoke-testing that the WebSocket hub fans out correctly under load

Usage
-----
First start the app in another terminal:

    HOST_PASSWORD=letmein uvicorn app.main:app --reload --port 8000

Then run the simulator:

    python simulate_audience.py                    # 25 voters, default
    python simulate_audience.py --voters 100       # louder room
    python simulate_audience.py --bias 0           # uniform votes
    python simulate_audience.py --bias 4           # heavy landslide toward option 0
    python simulate_audience.py --chaos            # some voters change their mind mid-round
    python simulate_audience.py --host ws://localhost:8000

For stress testing (hundreds of voters), stagger how fast they connect so
you don't trip local ulimits, and turn on the metrics reporter:

    python simulate_audience.py --voters 500 --connect-rate 100 --report
    python simulate_audience.py --voters 1000 --connect-rate 150 --report

On Linux/macOS you may also need to bump the open-file limit on both
terminals if you go above ~900 voters:

    ulimit -n 8192

The simulator never drives the host; you still advance rounds from /host
yourself. It just listens for phase changes and casts votes whenever a new
voting round begins.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
import uuid
from dataclasses import dataclass, field

try:
    import websockets
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "This script needs `websockets`. Install it with:\n"
        "    pip install websockets"
    ) from exc


@dataclass
class Voter:
    """One fake audience member with a stable client_id across rounds."""

    voter_id: int
    client_id: str = field(default_factory=lambda: f"sim-{uuid.uuid4()}")
    last_round_id: str | None = None
    last_choice: int | None = None

    def pick(self, options: list[str], bias: float) -> int:
        """Pick an option index, optionally biased toward option 0.

        bias=0 means uniform random. Higher bias means voters increasingly
        pile onto option 0 (useful for testing landslide scenarios that
        make the 'least popular' and 'runner-up' strategies visible).
        """
        if bias <= 0:
            return random.randrange(len(options))
        weights = [1.0] * len(options)
        weights[0] = 1.0 + bias
        total = sum(weights)
        r = random.random() * total
        running = 0.0
        for i, w in enumerate(weights):
            running += w
            if r <= running:
                return i
        return len(options) - 1


@dataclass
class Metrics:
    """Aggregated stats across all simulated voters."""

    connected: int = 0
    vote_sends: int = 0
    state_msgs_received: int = 0
    errors: int = 0
    # Per-round: t_first_vote_sent → t_last_state_saw_full_tally
    round_first_vote: dict[str, float] = field(default_factory=dict)
    round_last_update: dict[str, float] = field(default_factory=dict)

    def note_round_vote(self, rid: str, t: float) -> None:
        if rid not in self.round_first_vote:
            self.round_first_vote[rid] = t

    def note_round_update(self, rid: str, t: float) -> None:
        self.round_last_update[rid] = t


async def run_voter(
    voter: Voter,
    url: str,
    bias: float,
    chaos: bool,
    stop: asyncio.Event,
    metrics: Metrics,
    gate: asyncio.Semaphore,
) -> None:
    """One long-lived WebSocket connection that votes whenever a round opens."""
    backoff = 0.5
    # Rate-limit the initial connect so 1000 voters don't open 1000 sockets
    # in the same millisecond (trips local ulimits + makes cold-connect cost
    # unrealistic compared to a real phone crowd joining over 20-30s).
    async with gate:
        pass
    while not stop.is_set():
        try:
            async with websockets.connect(url, open_timeout=15) as ws:
                backoff = 0.5  # reset on successful connect
                metrics.connected += 1
                try:
                    async for raw in ws:
                        if stop.is_set():
                            break
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if msg.get("type") != "state":
                            continue
                        metrics.state_msgs_received += 1
                        state = msg.get("data", {})
                        phase = state.get("phase")
                        rnd = state.get("round") or {}
                        rid = rnd.get("id")
                        options = rnd.get("options") or []

                        if phase != "voting" or not options or not rid:
                            continue

                        metrics.note_round_update(rid, time.monotonic())

                        # New round → cast a fresh vote.
                        if rid != voter.last_round_id:
                            voter.last_round_id = rid
                            voter.last_choice = None
                            # Stagger so the bar chart actually animates
                            # instead of snapping to the final distribution.
                            await asyncio.sleep(random.uniform(0.2, 3.0))
                            choice = voter.pick(options, bias)
                            voter.last_choice = choice
                            await ws.send(
                                json.dumps(
                                    {
                                        "type": "vote",
                                        "option_index": choice,
                                        "client_id": voter.client_id,
                                    }
                                )
                            )
                            metrics.vote_sends += 1
                            metrics.note_round_vote(rid, time.monotonic())
                            continue

                        # Already voted; optionally change our mind once.
                        if (
                            chaos
                            and voter.last_choice is not None
                            and random.random() < 0.15
                        ):
                            new_choice = random.randrange(len(options))
                            if new_choice != voter.last_choice:
                                await asyncio.sleep(random.uniform(1.0, 5.0))
                                voter.last_choice = new_choice
                                await ws.send(
                                    json.dumps(
                                        {
                                            "type": "vote",
                                            "option_index": new_choice,
                                            "client_id": voter.client_id,
                                        }
                                    )
                                )
                                metrics.vote_sends += 1
                finally:
                    metrics.connected -= 1

        except (OSError, websockets.exceptions.WebSocketException):
            metrics.errors += 1
            # Server not up yet, or restarted. Back off and retry.
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 5.0)


async def connect_pacer(
    gate: asyncio.Semaphore,
    total: int,
    rate_per_sec: float,
) -> None:
    """Releases the semaphore at a fixed rate so ``run_voter`` coroutines
    are unblocked gradually instead of all at once."""
    # Gate starts at 0. We hold `total` slots and release them at `rate_per_sec`.
    interval = 1.0 / max(rate_per_sec, 1.0)
    for _ in range(total):
        gate.release()
        await asyncio.sleep(interval)


async def reporter(metrics: Metrics, target_voters: int, stop: asyncio.Event) -> None:
    """Prints a one-line status every second so you can watch the room fill up."""
    while not stop.is_set():
        await asyncio.sleep(1.0)
        # Find any round where first vote has landed → last update seen.
        latencies = []
        for rid, t_first in metrics.round_first_vote.items():
            t_last = metrics.round_last_update.get(rid)
            if t_last:
                latencies.append(t_last - t_first)
        lat_str = (
            f"last_update−first_vote={latencies[-1]:.2f}s"
            if latencies
            else "waiting for round…"
        )
        print(
            f"  connected={metrics.connected}/{target_voters}  "
            f"votes_sent={metrics.vote_sends}  "
            f"state_msgs={metrics.state_msgs_received}  "
            f"errors={metrics.errors}  {lat_str}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default="ws://localhost:8000",
        help="Base WebSocket URL of the running app (default: ws://localhost:8000)",
    )
    parser.add_argument(
        "--voters",
        type=int,
        default=25,
        help="How many fake audience members to simulate (default: 25)",
    )
    parser.add_argument(
        "--bias",
        type=float,
        default=2.0,
        help="Bias toward option 0. 0 = uniform, 4 = strong landslide (default: 2.0)",
    )
    parser.add_argument(
        "--chaos",
        action="store_true",
        help="~15%% of voters change their vote once per round, mid-round",
    )
    parser.add_argument(
        "--connect-rate",
        type=float,
        default=50.0,
        help="How many new WebSocket connections to open per second (default: 50)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print a one-line status every second (useful for stress tests)",
    )
    args = parser.parse_args()

    url = args.host.rstrip("/") + "/ws/audience"
    stop = asyncio.Event()
    metrics = Metrics()
    # Gate starts empty; the pacer releases one slot at a time so each voter
    # coroutine unblocks in the order we spawn them, spread over time.
    gate = asyncio.Semaphore(0)

    print(f"✦ Spinning up {args.voters} voters against {url}")
    print(
        f"  bias={args.bias}  chaos={args.chaos}  "
        f"connect_rate={args.connect_rate}/s"
    )
    print("  Press Ctrl+C to stop.\n")

    voters = [Voter(voter_id=i) for i in range(args.voters)]
    tasks = [
        asyncio.create_task(
            run_voter(v, url, args.bias, args.chaos, stop, metrics, gate)
        )
        for v in voters
    ]
    tasks.append(asyncio.create_task(connect_pacer(gate, args.voters, args.connect_rate)))
    if args.report:
        tasks.append(asyncio.create_task(reporter(metrics, args.voters, stop)))

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n✦ Simulator stopped.")
