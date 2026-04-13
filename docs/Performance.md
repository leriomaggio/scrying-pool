# Performance & Scale Notes

This document exists for the person deploying **The Scrying Pool** to `scrying.pycon.de`. It explains why the app is built the way it is, what its expected load looks like at the conference, and what the operator needs to know to keep it healthy through a full Lightning Talks session.

## Expected load

A PyConDE Lightning Talks session fills the main auditorium. A realistic optimistic ceiling is around one thousand concurrent audience members on their phones, and a conservative baseline is five hundred. Everyone who joins holds one WebSocket connection open for the entire session (roughly forty-five minutes) and casts one vote per round across five to ten rounds depending on the story. The two other clients are the projector view (one or two browser windows on the beamer laptop) and the host dashboard (one browser window on the host's laptop). So in aggregate: one thousand long-lived audience sockets, one or two screen sockets, one host socket, ten to twenty state-transition events (depending on the story), and somewhere around five to ten thousand vote messages over forty-five minutes.

None of that is large by web-app standards. What *is* interesting is that every vote wants to become a real-time update visible to every other phone in the room. That is the thing the architecture has to get right.

## Why WebSockets

The audience view is genuinely bidirectional. Each phone needs to push votes to the server and receive live bar-chart updates from the server. Plain HTTP polling would miss the animation, and Server-Sent Events would cover the read side but would need a separate POST channel for votes, which adds complexity for no real win at this scale. WebSockets give us one persistent connection per phone with low per-message overhead in both directions.

Holding a thousand idle WebSockets open is not expensive. Each one is a Python coroutine in `asyncio`, roughly ten to twenty kilobytes of process memory. The FastAPI/Starlette/uvicorn stack handles this comfortably on a single process and a single CPU core. The real cost is not the connection count but the **fan-out cost per broadcast**: whenever the game state changes, the server has to send an update to every connected client. The naive implementation (broadcast on every single vote, serialise the payload per-client) does not scale past a couple hundred users.

## What the code actually does

Broadcasts are coalesced. Inside `ConnectionHub` there is a background `run_flusher()` task that waits on a dirty flag, sleeps for two hundred and fifty milliseconds, and then fans out a single state update to every audience and screen socket. Individual vote messages set the dirty flag via `mark_dirty()` but do not trigger an immediate broadcast. This caps the outbound update rate at four per second during voting, which is more than fast enough for smooth bar-chart animation and far slower than the per-vote firehose the old code used to generate.

Phase transitions bypass the coalescing delay. When the host presses Start Round, Reveal Winner, Next Round, or any override command, the code calls `broadcast_now()` instead of `mark_dirty()`, so host controls feel instant.

Broadcasts serialise the state payload exactly once per broadcast, not once per client. The old code called `ws.send_json()` for every client, which re-ran `json.dumps` on every send: a thousand JSON encodings per broadcast for a thousand-person room. The new code calls `json.dumps` once and then fans the resulting string out via `ws.send_text()` to every client using `asyncio.gather`, so one slow client cannot head-of-line-block the others.

Snapshot persistence only writes to disk on phase transitions (start, reveal, next, reset, override), not on every vote. The snapshot also records the active story key and round duration so a restart resumes with the correct story selected. In-flight votes inside a single round are ephemeral anyway: if the process crashes mid-voting the round just restarts cleanly with the same question and options. This drops the disk write rate from roughly thirty-three writes per second during a busy round down to about ten to twenty writes across an entire game.

There is also a `voting_watchdog` task that nudges the dirty flag once per second while a round is voting, so audience countdown timers stay in sync even during quiet moments when no new votes are arriving. The flusher coalesces this nudge with any vote-triggered dirty flags, so it does not add extra broadcasts.

## Measured performance

The repository ships with `simulate_audience.py`, a headless load-generator that opens any number of fake WebSocket clients against a running server and casts votes in response to real state updates. Full usage is documented in [Testing.md](Testing.md). The numbers below were produced on a single laptop core, with the simulator and the server sharing the same machine so there is zero network latency. Realistic network latency in a venue will add tens to hundreds of milliseconds per message but will not change the shape of the result.

With one thousand simulated voters, a 150-connections-per-second ramp-up, and the host pressing Start Round once everyone has connected, the host dashboard observed the following timings:

| Milestone | Time after Start Round |
|---|---|
| First vote counted | 0.44 s |
| 50 votes counted | 0.44 s |
| 500 votes counted | 1.93 s |
| 900 votes counted | 3.15 s |
| 1000 votes counted | 3.15 s |

Server resident memory stabilised at roughly two hundred and fifty megabytes for a thousand concurrent WebSockets, CPU usage peaked around seven percent of one core during the vote burst, and no sockets errored out or were dropped. The reason all thousand votes land in about three seconds and not instantly is that the simulator deliberately staggers each voter's own send by a random 0.2 to 3.0 second delay, modelling the fact that real humans do not tap their phone in perfect unison. The server itself was nowhere near saturated at any point during the test.

Five hundred voters run through the same scenario land all votes in under three and a half seconds, with identical memory and CPU profiles scaled proportionally. The architecture has comfortable headroom at the target scale.

## Bandwidth and client-side cost

A public state payload is small: roughly five to eight hundred bytes of JSON containing the current round, the options, the tally array, the total vote count, and the phase flags. With coalesced broadcasting at four hertz and a thousand connected clients, the upstream bandwidth from the server is on the order of three megabytes per second during active voting. Any commodity VPS can move that. Between rounds, during the `waiting` and `revealed` phases, the broadcast rate drops essentially to zero because the watchdog only fires during voting.

Each phone receives roughly four small JSON messages per second during voting. A modern mobile browser decodes and re-renders the bar chart for payloads this small without noticeable load.

## The real bottleneck is Wi-Fi, not the server

Conference venue Wi-Fi under a thousand simultaneously active phones is historically the component that breaks live audience games, not the backend. The Wi-Fi access points get saturated at the association-layer well before any HTTP stack does. There are two things the deployment can do about this. The first is to tell people in the announcement slide that mobile data works just as well. Most modern phones will auto-failover to cellular if they detect the venue Wi-Fi stalling, but only if they are allowed to. The second is to make sure the TLS certificate on `scrying.pycon.de` is reachable over the public internet and not pinned to any conference-internal network, so cellular traffic actually works.

## Deployment recommendations

Run a single uvicorn worker, not multiple. The game engine lives in-memory in one Python process, so multiple workers would each maintain their own independent copy of the state and votes would split across them unpredictably. One worker, one core, no coordination needed. The app is small enough that a single container on a t3.small or equivalent is more than sufficient.

Set `PUBLIC_URL` to `https://scrying.pycon.de` so the QR code on the projector view renders with the real URL instead of `http://localhost:8000`.

Pick a non-default `HOST_PASSWORD` and keep it out of version control. The `.env.example` file shows the shape.

Make sure the reverse proxy in front of the container forwards WebSocket upgrade headers on the `/ws/*` paths. A default nginx config will silently strip them and the game will appear to work (pages load, login works) but votes will never reach the server. The README shows a working nginx block. Caddy handles this automatically. Cloudflare free-tier supports WebSockets but double-check the plan before relying on it.

Wire `/healthz` into whatever platform liveness probe you have. It returns a small JSON document with the current phase and round index and does not need authentication.

The `SNAPSHOT_PATH` path (default `/tmp/scrying_snapshot.json`) should live on a volume that survives container restarts. If the container restarts mid-session (say because the host runs `docker compose restart` to re-read `.env`) the game will resume on the round it was in before, with votes cleared for the in-progress round and all previously-revealed rounds intact. Losing the snapshot file during a live session would reset the game to round one, which you do not want.

## When this guidance stops being true

The numbers and design above are right for one backend process serving up to a couple of thousand concurrent clients through a single reverse proxy. If future editions of the game ever need to scale past that (for instance, running the same game simultaneously across multiple rooms and needing the projector displays to stay in sync) the in-memory single-process design becomes the bottleneck, and the right next step is a shared state store (Redis pub/sub is the classic fit) plus multiple uvicorn workers that subscribe to the same channel. That is a significant rewrite rather than a config change, and it is not needed for a single-room Lightning Talks session.
