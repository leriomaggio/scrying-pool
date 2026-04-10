# ✦ The Scrying Pool ✦

An audience engagement game for the PyConDE 2026 Lightning Talks.

## Architecture

Single process FastAPI app with WebSocket broadcast fan-out. Three views are served by the same backend:

* `/` is the audience view. Mobile first. Shows the current poll, a 30 second countdown, and a live updating bar chart of votes.
* `/screen` is the big-screen / projector view. Shows the same poll at large scale plus a QR code to join, the dramatic winning word reveal, and the final illustrated story.
* `/host` is a password-protected dashboard with the current round's *secret* story slot, the *secret* active strategy, a manual override for both the strategy and the winning word, a live preview of the final story as it fills in round by round, and controls to advance the game.

State lives in-memory in a `GameEngine` instance. On every phase transition the engine writes a small JSON snapshot to disk, so a container restart mid-session can resume where it left off. No database, no Redis, no queue.

The rounds, the poll questions, the word options, and the quest template all live in a single editable file: `app/data/story.json`. You can drop in a completely different story without touching any code.

## Project layout

```
scrying-pool/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, WebSocket hub, routes, auth
│   ├── game.py              # GameEngine, Round, strategies, state machine
│   ├── data/
│   │   └── story.json       # The quest, the rounds, the rotation
│   └── static/
│       ├── audience.html    # Mobile view
│       ├── screen.html      # Projector view (with QR)
│       ├── host.html        # Host dashboard
│       ├── host_login.html
│       └── shared.css       # PyConDE26 theme
├── docs/
│   ├── Performance.md       # Architecture, scale notes, stress-test results
│   └── Testing.md           # Local testing, simulator usage, dress rehearsal
├── simulate_audience.py     # Headless load generator / rehearsal tool
├── smoke_test.py            # Engine-level smoke test (no server)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## Run locally (dev)

```bash
cd scrying-pool
pip install -r requirements.txt
HOST_PASSWORD=letmein uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open three browser windows:

* `http://localhost:8000/` for the audience (shrink to phone width to test mobile layout)
* `http://localhost:8000/screen` for the projector view
* `http://localhost:8000/host` for the host dashboard; enter `letmein` as the password

## Run with Docker

```bash
cp .env.example .env
# Edit .env. At minimum set HOST_PASSWORD and PUBLIC_URL.
docker compose up --build
```

## Deploying to `scrying.pycon.de`

The app is reverse-proxy friendly. The Dockerfile launches uvicorn with `--proxy-headers` and `--forwarded-allow-ips *` so it trusts `X-Forwarded-*` headers from the proxy.

### Required: WebSocket upgrade support

Whatever proxies the subdomain (nginx, Caddy, Traefik, Cloudflare) **must** forward the WebSocket upgrade headers to `/ws/*`. Common gotcha: a default nginx config will strip them silently, and the game will appear to work but votes never reach the server. Example nginx block:

```nginx
location /ws/ {
    proxy_pass http://scrying-backend:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 3600s;
}

location / {
    proxy_pass http://scrying-backend:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Caddy does this automatically. Cloudflare requires Pro tier or above for WebSocket support on free plans (double check before the conference).

### Environment variables

| Variable | Default | Notes |
|---|---|---|
| `HOST_PASSWORD` | `letmein` | **Change this.** Password for `/host`. |
| `STORY_FILE` | `app/data/story.json` | Swap to a different template if you want. |
| `ROUND_DURATION` | `30` | Seconds per voting round. |
| `SNAPSHOT_FILE` | `/srv/snapshot.json` | Written on phase transitions for restart resume. |
| `PUBLIC_URL` | `http://localhost:8000` | URL rendered in the QR code on the projector. Set to `https://scrying.pycon.de` for prod. |

### Health check

`GET /healthz` returns `{"ok": true, ...}`. Wire this to your platform's liveness probe.

## Running the game: host cheat sheet

1. **Before the session:** open `/host`, log in. You will see the full round summary on the right with the hidden story slots (`MOUNT`, `MONSTER`, `MAGIC_SPELL`, …) and the rotated strategy for each round. The audience sees none of this.
2. **Between talks:** press **Start Round**. The audience view switches to the current poll, the 30s countdown starts, and votes begin flowing into the live bar chart on all three views.
3. **Mid-voting, optional:** if a round is "Host's Wild Card" strategy, pick any option from the **Manual Override** buttons. You can also override the strategy itself from the dropdown if you want to tune the comedy on the fly.
4. **When time is up (or early):** press **Reveal Winner**. The big screen does the dramatic reveal. The winning word appears in gold, the strategy is named ("The Underdog"), and the crowd gets the punchline of "wait, the *least* popular one won?"
5. **Advance:** press **Next Round →**. Repeat for all 12 rounds.
6. **Grand finale:** after the last reveal, press **Show Final Story**. The big screen switches to the full rendered quest, ready for you to read aloud in your most dramatic wizard voice. Every word the audience filled in is shown in blue and bold, every recurring fantasy character is shown in blue and italic, so the climax is easy to read across the room.

## Testing without deploying

For multi-user testing, stress tests, and dress rehearsals, see [`docs/Testing.md`](docs/Testing.md). It covers the quick incognito-window smoke test, LAN-based phone testing, and the headless `simulate_audience.py` load generator which can spin up anywhere from 25 to 1000 fake voters against a running instance.

## Performance & scale

For deployment-facing questions ("will this handle a thousand people?", "what does the server need?", "why WebSockets?", "what's the Wi-Fi story?"), see [`docs/Performance.md`](docs/Performance.md). It explains the coalesced-broadcast architecture, documents the measured performance at 500 and 1000 concurrent voters, and lists the deployment gotchas (WebSocket upgrade headers, single uvicorn worker, snapshot volume persistence).

## Customising the story

Everything is in `app/data/story.json`:

* `rounds[]`: each round has a `format` (`standard` or `misleading_poll`), the `poll_question` shown to the audience, the secret `story_slot` it fills, the `category_label` shown as a tag ("NOUN", "HOT TAKE"), and 5 or 6 `options`.
* `strategy_rotation[]`: one strategy per round, cycles if shorter than rounds. Available: `most_popular`, `least_popular`, `random`, `second_place`, `host_choice`, `inverse_momentum`.
* `story_template[]`: array of paragraphs. Use `{SLOT_NAME}` placeholders that match the `story_slot` values in the rounds.
* `highlighted_characters[]`: list of recurring fantasy character names that should be emphasised in blue italic on the final story screen.

Keep round count between 10 and 12. Fewer feels thin; more and the audience attention drifts.
