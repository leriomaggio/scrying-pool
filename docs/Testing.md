# Testing The Scrying Pool

This document covers every way you can test the game without deploying it. It is aimed at anyone tweaking the story, the strategies, the HTML, or just wanting to do a full dress rehearsal before the session.

## The three levels of local testing

### Multiple browser windows

The quickest smoke test. Start the app with `HOST_PASSWORD=letmein uvicorn app.main:app --reload --port 8000` and open several **incognito or private windows** pointed at `http://localhost:8000/`. Each incognito window gets its own isolated `localStorage`, which is how the audience page assigns a stable `client_id` to each voter, so the server will treat each window as a distinct person.

If you open regular tabs in the same browser profile they will all share one `client_id` and the backend will treat them as **one user changing their mind**. That is still useful because it exercises the vote-replacement path, but it is not a multi-user test.

Keep `/screen` open in its own window and the `/host` dashboard logged in on a second monitor while you click around in the incognito windows. This is enough to verify the three views stay in sync and the bars animate correctly as votes come in.

### Real phones on the same Wi-Fi

If your laptop and one or more phones are on the same wireless network, the app is already bound to all interfaces (because the run command uses `--host 0.0.0.0`). Find your laptop's LAN IP with `ipconfig getifaddr en0` on macOS or `hostname -I` on Linux, then point phones at `http://<laptop-ip>:8000/`.

The projector view's QR code will still point at `localhost` unless you also set `PUBLIC_URL` when launching, so either type the LAN URL on the phone directly or relaunch with something like `PUBLIC_URL=http://192.168.1.42:8000 uvicorn app.main:app --host 0.0.0.0 --port 8000`. This is the closest you can get to a realistic session without actually deploying the app.

### Headless vote simulator

The most useful option when you want to exercise the strategies, stress-test the server, or do a full rehearsal without wrangling a pile of physical devices. The simulator lives at [`simulate_audience.py`](../simulate_audience.py) in the repository root. It opens any number of fake WebSocket clients against a running server, listens for real state updates, and casts votes whenever a voting round opens.

## Running the simulator

First, start the app in one terminal and leave it running:

```bash
HOST_PASSWORD=letmein uvicorn app.main:app --reload --port 8000
```

Install the one extra dependency the simulator needs (it is intentionally not in `requirements.txt` because production does not need it):

```bash
pip install websockets
```

Then in a second terminal run the simulator with whatever shape of crowd you want to model. The defaults give you twenty-five voters with a mild bias toward the first option:

```bash
python simulate_audience.py
```

The simulator does not drive the host. It only listens for phase changes and votes when a new round opens. You still have `/host` open in your browser and press **Start Round** → wait a few seconds for the bars to fill → press **Reveal Winner** → press **Next Round** → repeat. The simulator will automatically cast fresh votes on every new round.

## The knobs that matter

The bias parameter controls how strongly voters lean toward option 0. A value of zero gives perfectly uniform random votes, which is useful for seeing what the Random Draw and Inverse Momentum strategies do on balanced input. A higher bias models a landslide favourite, which is the most interesting case for testing the Least Popular and Runner-Up strategies because the winning word they pick will be visibly different from what the room is cheering for:

```bash
python simulate_audience.py --bias 0      # perfectly uniform votes
python simulate_audience.py --bias 2      # the default; option 0 gets ~50% of votes
python simulate_audience.py --bias 4      # crushing landslide toward option 0
```

The voter count is self-explanatory. Anything up to a hundred runs without changing any system settings. Above that you may need to raise the open-file limit in both the server terminal and the simulator terminal with `ulimit -n 8192`:

```bash
python simulate_audience.py --voters 100
python simulate_audience.py --voters 500
python simulate_audience.py --voters 1000
```

The connect-rate flag controls how fast the simulator opens new WebSocket connections. The default fifty per second is fine for moderate numbers but at five hundred or a thousand voters you want to spread the connection burst out so you are not hammering the server in a single millisecond (which is unrealistic compared to a real phone crowd joining over twenty or thirty seconds anyway):

```bash
python simulate_audience.py --voters 1000 --connect-rate 150
```

The chaos flag makes roughly fifteen percent of voters change their mind once per round, mid-round, with a random delay. This exercises the vote-replacement code path on the server (which relies on the stable `client_id` overwriting the previous vote rather than adding a new one):

```bash
python simulate_audience.py --chaos
```

The report flag prints a one-line status update every second, showing how many sockets are currently connected, how many vote messages have been sent, how many state messages have been received, how many errors have occurred, and the observed latency between the first vote landing and the most recent state update for the current round. Useful for watching a stress test unfold:

```bash
python simulate_audience.py --voters 500 --connect-rate 100 --report
```

## Stress testing

The typical "does this actually scale" dry-run looks like this. In one terminal, bump the file limit and launch the server:

```bash
ulimit -n 8192
HOST_PASSWORD=letmein uvicorn app.main:app --port 8000
```

In a second terminal, also bump the file limit and launch a thousand voters with reporting on:

```bash
ulimit -n 8192
python simulate_audience.py --voters 1000 --connect-rate 150 --bias 2 --report
```

Wait about ten seconds for the ramp-up to finish. The reporter will show the connected count climbing toward one thousand. Then open the host dashboard, log in, and press **Start Round**. Within roughly three seconds you should see the total vote count on the host dashboard and projector view climb to one thousand. If you see it stall at a lower number, or if the simulator starts reporting errors, that is the interesting signal: something is wrong either with the server, the Wi-Fi (if you are testing across a network), or your local file-descriptor limits.

Concrete numbers from a thousand-voter stress test on a single laptop core, with the server and the simulator sharing the same machine, are in [Performance.md](Performance.md). The summary is that the server is nowhere near saturated at a thousand clients and the observed end-to-end vote latency is dominated by the simulator's own deliberate stagger, not by the backend.

## Smoke test script

There is also a standalone `smoke_test.py` in the repository root that exercises the game engine directly (no WebSocket, no server) and verifies every strategy, the full twelve-round flow, the snapshot round-trip, the host-override path, and the reset path. It takes about a second to run and is a good sanity check before any change to `game.py` or `story.json`:

```bash
python smoke_test.py
```

It should print a tidy list of passing assertions and finish with "All smoke tests passed. The pool is ready." If anything fails the script exits with a non-zero status and a clear error.

## Dress rehearsal checklist

A realistic end-to-end rehearsal looks like this. Start the server with a deliberately short round duration (say ten seconds) so you can rip through all twelve rounds quickly: `ROUND_DURATION=10 HOST_PASSWORD=letmein uvicorn app.main:app --port 8000`. Open `/screen` and `/host` in browser windows on two monitors if you have them, log in to the host dashboard, and in a second terminal launch the simulator with a moderate crowd and chaos enabled: `python simulate_audience.py --voters 50 --chaos`. Then run through the full twelve-round sequence (Start Round, watch the bars, Reveal Winner, watch the reveal, Next Round) twelve times, and end with Show Final Story. The whole dress rehearsal takes about four minutes and will expose anything unexpected about round ordering, strategy rotation, story slot mapping, or the final story render.
