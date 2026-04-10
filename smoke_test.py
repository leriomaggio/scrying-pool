"""End-to-end smoke test for the Scrying Pool game engine.

Tests:
- story.json loads cleanly and all 12 rounds parse
- Each of the 6 selection strategies returns a valid winner
- Full game flow: start -> vote -> reveal -> next_round x12 -> final
- Final story renders with all slots substituted
- Snapshot round-trips correctly
- Host override actually overrides the strategy result
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.game import GameEngine, Strategy, GamePhase

STORY = Path(__file__).parent / "app" / "data" / "story.json"


def banner(msg):
    print(f"\n=== {msg} ===")


def assert_eq(a, b, msg):
    assert a == b, f"FAIL {msg}: expected {b!r}, got {a!r}"
    print(f"  ✓ {msg}")


def assert_true(cond, msg):
    assert cond, f"FAIL {msg}"
    print(f"  ✓ {msg}")


def test_loading():
    banner("Loading story.json")
    eng = GameEngine(STORY, round_duration_s=30)
    assert_eq(len(eng.rounds), 12, "loaded 12 rounds")
    assert_eq(eng.phase, GamePhase.WAITING, "initial phase is WAITING")
    for i, r in enumerate(eng.rounds):
        assert_true(len(r.options) >= 4, f"round {i+1} has >=4 options")
        assert_true(r.format in ("standard", "misleading_poll"), f"round {i+1} has valid format")
        assert_true(r.story_slot.isupper() or "_" in r.story_slot, f"round {i+1} has a story slot")
    return eng


def test_all_strategies():
    banner("All 6 strategies return valid winners")
    eng = GameEngine(STORY, round_duration_s=30)
    for strat in Strategy:
        eng.reset()
        r = eng.rounds[0]
        r.strategy = strat
        eng.start_round()
        # Simulate votes: biased toward option 2, with option 4 surging late
        import time
        for i in range(10):
            eng.record_vote(f"c{i}", 2)
        for i in range(3):
            eng.record_vote(f"c{10+i}", 0)
        for i in range(5):
            # Simulate late surge for option 4
            r.vote_history.append((time.time() + 100, 4))
            eng.record_vote(f"c{13+i}", 4)
        eng.reveal()
        w = r.winning_index
        assert_true(w is not None and 0 <= w < len(r.options), f"{strat.value} picked a valid index (got {w} = {r.options[w]!r})")
        eng.reset()


def test_full_game_flow():
    banner("Full game: 12 rounds start→vote→reveal→next")
    eng = GameEngine(STORY, round_duration_s=30)
    for i in range(12):
        assert_eq(eng.current_round_index, i, f"on round {i+1}")
        eng.start_round()
        assert_eq(eng.phase, GamePhase.VOTING, f"round {i+1} is VOTING")
        # 5 people vote for option 0, 3 for option 1
        for v in range(5):
            eng.record_vote(f"voter{v}", 0)
        for v in range(3):
            eng.record_vote(f"voter{5+v}", 1)
        assert_eq(eng.current_round().total_votes(), 8, f"round {i+1} has 8 votes")
        eng.reveal()
        assert_eq(eng.phase, GamePhase.REVEALED, f"round {i+1} is REVEALED")
        assert_true(eng.current_round().winning_index is not None, f"round {i+1} has a winner")
        if i < 11:
            eng.next_round()
    eng.next_round()  # past last round
    assert_eq(eng.phase, GamePhase.FINAL, "game ends in FINAL after round 12")


def test_final_story_rendering():
    banner("Final story renders with all slots filled")
    eng = GameEngine(STORY, round_duration_s=30)
    for i in range(12):
        eng.start_round()
        # Everyone votes for option 0
        for v in range(5):
            eng.record_vote(f"v{v}", 0)
        eng.reveal()
        if i < 11:
            eng.next_round()
    eng.next_round()
    paragraphs = eng.rendered_story()
    assert_true(len(paragraphs) > 0, "story has paragraphs")
    full = "\n".join(paragraphs)
    # None of the original placeholders should remain
    import re
    unfilled = re.findall(r"\{[A-Z_]+\}", full)
    assert_eq(unfilled, [], "no unfilled placeholders")
    # All 12 winning words should appear in the story, not option[0],
    # because the strategy rotation means the most-voted isn't always the winner
    # (that's the whole point of the game).
    for r in eng.rounds:
        assert_true(r.winning_index is not None, f"round {r.id} has a winner")
        winning_word = r.options[r.winning_index]
        assert_true(winning_word in full, f"winning word '{winning_word}' ({r.story_slot}) appears in final story")
    print("\n  --- FINAL STORY PREVIEW ---")
    for p in paragraphs:
        print(f"  {p}")
    print("  --- END PREVIEW ---")


def test_snapshot_roundtrip():
    banner("Snapshot persistence round-trip")
    eng1 = GameEngine(STORY, round_duration_s=30)
    eng1.start_round()
    eng1.record_vote("alice", 2)
    eng1.record_vote("bob", 1)
    eng1.reveal()
    snap = eng1.snapshot()

    eng2 = GameEngine(STORY, round_duration_s=30)
    eng2.restore(snap)
    assert_eq(eng2.phase, GamePhase.REVEALED, "restored phase")
    assert_eq(eng2.current_round_index, 0, "restored round index")
    assert_eq(eng2.current_round().total_votes(), 2, "restored vote count")
    assert_eq(eng2.current_round().winning_index, eng1.current_round().winning_index, "restored winner")


def test_host_override():
    banner("Host override picks a different word than strategy would")
    eng = GameEngine(STORY, round_duration_s=30)
    eng.rounds[0].strategy = Strategy.MOST_POPULAR
    eng.start_round()
    for i in range(10):
        eng.record_vote(f"c{i}", 2)  # option 2 is runaway popular
    eng.set_host_override(4)  # host picks option 4 instead
    eng.reveal()
    assert_eq(eng.current_round().winning_index, 4, "host override beat most-popular strategy")


def test_late_votes_rejected():
    banner("Votes arriving after closes_at are rejected")
    import time
    # Short round duration so we don't have to wait 30s
    eng = GameEngine(STORY, round_duration_s=1)
    eng.start_round()
    r = eng.current_round()

    # On-time vote: should succeed
    ok_early = eng.record_vote("early-bird", 0)
    assert_true(ok_early, "vote before closes_at is accepted")

    # Force the deadline into the past (beyond the 0.5s grace window)
    r.closes_at = time.time() - 2.0

    # Late vote from a new client: should be rejected without mutating state
    votes_before = r.total_votes()
    ok_late = eng.record_vote("late-arrival", 3)
    assert_true(not ok_late, "vote after closes_at + grace is rejected")
    assert_eq(r.total_votes(), votes_before, "rejected vote does not mutate tally")
    assert_true("late-arrival" not in r.votes, "rejected client_id is not stored")

    # Late re-vote from the early-bird client: should ALSO be rejected
    # (prevents sneaking a vote change after time is up)
    ok_revote = eng.record_vote("early-bird", 2)
    assert_true(not ok_revote, "late re-vote is rejected even from existing client")
    assert_eq(r.votes["early-bird"], 0, "early-bird's original vote is unchanged")

    # Vote inside the grace window: should succeed
    r.closes_at = time.time() - 0.2   # just past deadline, inside 0.5s grace
    ok_grace = eng.record_vote("just-in-time", 1)
    assert_true(ok_grace, "vote inside grace window is accepted")


def test_reset():
    banner("Reset clears everything")
    eng = GameEngine(STORY, round_duration_s=30)
    eng.start_round()
    eng.record_vote("x", 0)
    eng.reveal()
    eng.next_round()
    eng.reset()
    assert_eq(eng.current_round_index, 0, "reset goes to round 0")
    assert_eq(eng.phase, GamePhase.WAITING, "reset phase is WAITING")
    assert_eq(eng.rounds[0].total_votes(), 0, "votes cleared")
    assert_true(eng.rounds[0].winning_index is None, "winner cleared")


if __name__ == "__main__":
    test_loading()
    test_all_strategies()
    test_full_game_flow()
    test_final_story_rendering()
    test_snapshot_roundtrip()
    test_host_override()
    test_late_votes_rejected()
    test_reset()
    print("\n✦ All smoke tests passed. The pool is ready. ✦")
