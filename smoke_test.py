"""End-to-end smoke test for the Scrying Pool game engine.

Tests:
- Both story files load cleanly
- Story1 has 5 rounds, Story2 has 10 rounds
- Each of the 6 selection strategies returns a valid winner
- Full game flow: start -> vote -> reveal -> next_round -> final
- Final story renders with all slots substituted
- Snapshot round-trips correctly (including round_duration_s)
- Host override actually overrides the strategy result
- Late votes rejected after deadline
- Story switching works correctly
- Duration control works correctly
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.game import GameEngine, Strategy, GamePhase

STORY1_PATH = Path(__file__).parent / "app" / "data" / "story1.json"
STORY2_PATH = Path(__file__).parent / "app" / "data" / "story2.json"


def load_story(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


STORY1 = load_story(STORY1_PATH)
STORY2 = load_story(STORY2_PATH)


def banner(msg):
    print(f"\n=== {msg} ===")


def assert_eq(a, b, msg):
    assert a == b, f"FAIL {msg}: expected {b!r}, got {a!r}"
    print(f"  ✓ {msg}")


def assert_true(cond, msg):
    assert cond, f"FAIL {msg}"
    print(f"  ✓ {msg}")


def test_loading():
    banner("Loading both stories")
    eng1 = GameEngine(STORY1, round_duration_s=15)
    assert_eq(len(eng1.rounds), 5, "story1 has 5 rounds")
    assert_eq(eng1.phase, GamePhase.WAITING, "initial phase is WAITING")
    for i, r in enumerate(eng1.rounds):
        assert_true(len(r.options) >= 4, f"story1 round {i+1} has >=4 options")
        assert_true(r.format in ("standard", "misleading_poll"), f"story1 round {i+1} has valid format")
        assert_true(r.story_slot.isupper() or "_" in r.story_slot, f"story1 round {i+1} has a story slot")

    eng2 = GameEngine(STORY2, round_duration_s=15)
    assert_eq(len(eng2.rounds), 10, "story2 has 10 rounds")
    for i, r in enumerate(eng2.rounds):
        assert_true(len(r.options) >= 4, f"story2 round {i+1} has >=4 options")


def test_loading_from_path():
    banner("Loading from file path (backward compat)")
    eng = GameEngine(STORY1_PATH, round_duration_s=15)
    assert_eq(len(eng.rounds), 5, "loaded 5 rounds from path")


def test_all_strategies():
    banner("All 6 strategies return valid winners")
    eng = GameEngine(STORY2, round_duration_s=30)
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


def test_full_game_flow_story1():
    banner("Full game: story1, 5 rounds")
    eng = GameEngine(STORY1, round_duration_s=30)
    for i in range(5):
        assert_eq(eng.current_round_index, i, f"on round {i+1}")
        eng.start_round()
        assert_eq(eng.phase, GamePhase.VOTING, f"round {i+1} is VOTING")
        for v in range(5):
            eng.record_vote(f"voter{v}", 0)
        eng.reveal()
        assert_eq(eng.phase, GamePhase.REVEALED, f"round {i+1} is REVEALED")
        assert_true(eng.current_round().winning_index is not None, f"round {i+1} has a winner")
        if i < 4:
            eng.next_round()
    eng.next_round()
    assert_eq(eng.phase, GamePhase.FINAL, "game ends in FINAL after round 5")


def test_full_game_flow_story2():
    banner("Full game: story2, 10 rounds")
    eng = GameEngine(STORY2, round_duration_s=30)
    for i in range(10):
        assert_eq(eng.current_round_index, i, f"on round {i+1}")
        eng.start_round()
        for v in range(5):
            eng.record_vote(f"voter{v}", 0)
        for v in range(3):
            eng.record_vote(f"voter{5+v}", 1)
        eng.reveal()
        if i < 9:
            eng.next_round()
    eng.next_round()
    assert_eq(eng.phase, GamePhase.FINAL, "game ends in FINAL after round 10")


def test_final_story_rendering():
    banner("Final story renders with all slots filled (story1)")
    eng = GameEngine(STORY1, round_duration_s=30)
    for i in range(5):
        eng.start_round()
        for v in range(5):
            eng.record_vote(f"v{v}", 0)
        eng.reveal()
        if i < 4:
            eng.next_round()
    eng.next_round()
    paragraphs = eng.rendered_story()
    assert_true(len(paragraphs) > 0, "story has paragraphs")
    full = "\n".join(paragraphs)
    import re
    unfilled = re.findall(r"\{[A-Z_]+\}", full)
    assert_eq(unfilled, [], "no unfilled placeholders")
    for r in eng.rounds:
        assert_true(r.winning_index is not None, f"round {r.id} has a winner")
        winning_word = r.options[r.winning_index]
        assert_true(winning_word in full, f"winning word '{winning_word}' ({r.story_slot}) appears in final story")
    print("\n  --- STORY1 FINAL PREVIEW ---")
    for p in paragraphs:
        print(f"  {p}")
    print("  --- END PREVIEW ---")


def test_final_story_segments():
    banner("Final story segments include character highlights (story2)")
    eng = GameEngine(STORY2, round_duration_s=30)
    for i in range(10):
        eng.start_round()
        for v in range(5):
            eng.record_vote(f"v{v}", 0)
        eng.reveal()
        if i < 9:
            eng.next_round()
    eng.next_round()
    segments = eng.rendered_story_segments()
    assert_true(len(segments) > 0, "has paragraph segments")
    # Check that at least some character segments exist
    char_segments = [seg for para in segments for seg in para if seg.get("char")]
    assert_true(len(char_segments) > 0, f"found {len(char_segments)} character-highlighted segments")
    # Verify known characters appear
    char_texts = {seg["text"] for seg in char_segments}
    assert_true("Valerio the Chance Caster" in char_texts, "Valerio highlighted")
    assert_true("Johannes the Timekeeper" in char_texts, "Johannes Timekeeper highlighted")
    print(f"  Character segments found: {sorted(char_texts)}")


def test_snapshot_roundtrip():
    banner("Snapshot persistence round-trip")
    eng1 = GameEngine(STORY2, round_duration_s=20)
    eng1.start_round()
    eng1.record_vote("alice", 2)
    eng1.record_vote("bob", 1)
    eng1.reveal()
    snap = eng1.snapshot()

    eng2 = GameEngine(STORY2, round_duration_s=30)
    eng2.restore(snap)
    assert_eq(eng2.phase, GamePhase.REVEALED, "restored phase")
    assert_eq(eng2.current_round_index, 0, "restored round index")
    assert_eq(eng2.current_round().total_votes(), 2, "restored vote count")
    assert_eq(eng2.current_round().winning_index, eng1.current_round().winning_index, "restored winner")
    assert_eq(eng2.round_duration_s, 20, "restored round_duration_s")


def test_host_override():
    banner("Host override picks a different word than strategy would")
    eng = GameEngine(STORY2, round_duration_s=30)
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
    eng = GameEngine(STORY2, round_duration_s=1)
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
    ok_revote = eng.record_vote("early-bird", 2)
    assert_true(not ok_revote, "late re-vote is rejected even from existing client")
    assert_eq(r.votes["early-bird"], 0, "early-bird's original vote is unchanged")

    # Vote inside the grace window: should succeed
    r.closes_at = time.time() - 0.2   # just past deadline, inside 0.5s grace
    ok_grace = eng.record_vote("just-in-time", 1)
    assert_true(ok_grace, "vote inside grace window is accepted")


def test_story1_always_most_popular():
    banner("Story1 strategies are always most_popular, even after restore")
    eng = GameEngine(STORY1, round_duration_s=15)
    for r in eng.rounds:
        assert_eq(r.strategy, Strategy.MOST_POPULAR, f"story1 round {r.id} strategy before snapshot")
    # Start a round, snapshot, restore, and verify strategies survive
    eng.start_round()
    eng.record_vote("a", 0)
    eng.reveal()
    snap = eng.snapshot()
    eng2 = GameEngine(STORY1, round_duration_s=15)
    eng2.restore(snap)
    for r in eng2.rounds:
        assert_eq(r.strategy, Strategy.MOST_POPULAR, f"story1 round {r.id} strategy after restore")


def test_switch_story():
    banner("Story switching")
    eng = GameEngine(STORY1, round_duration_s=15)
    assert_eq(len(eng.rounds), 5, "starts with story1 (5 rounds)")
    assert_true(not eng.game_has_started(), "game not started yet")

    # Switch to story2
    eng.switch_story(STORY2)
    assert_eq(len(eng.rounds), 10, "switched to story2 (10 rounds)")
    assert_eq(eng.phase, GamePhase.WAITING, "phase reset to WAITING")
    assert_eq(eng.current_round_index, 0, "round index reset to 0")

    # Start a round, then verify game_has_started
    eng.start_round()
    assert_true(eng.game_has_started(), "game has started after start_round")


def test_duration_control():
    banner("Round duration control")
    eng = GameEngine(STORY1, round_duration_s=15)
    assert_eq(eng.round_duration_s, 15, "default duration is 15s")
    eng.round_duration_s = 30
    eng.start_round()
    r = eng.current_round()
    expected = r.opened_at + 30
    assert_true(abs(r.closes_at - expected) < 0.1, "round uses updated duration")


def test_story2_strategy_rotation():
    banner("Story2 uses the full 6-strategy rotation")
    eng = GameEngine(STORY2, round_duration_s=15)
    expected = ["most_popular", "least_popular", "random", "second_place",
                "host_choice", "inverse_momentum", "most_popular", "random",
                "least_popular", "second_place"]
    for i, r in enumerate(eng.rounds):
        assert_eq(r.strategy.value, expected[i], f"story2 round {i+1} strategy = {expected[i]}")


def test_story2_final_story_rendering():
    banner("Final story renders with all slots filled (story2)")
    eng = GameEngine(STORY2, round_duration_s=30)
    for i in range(10):
        eng.start_round()
        for v in range(5):
            eng.record_vote(f"v{v}", 0)
        for v in range(3):
            eng.record_vote(f"v{5+v}", 1)
        eng.reveal()
        if i < 9:
            eng.next_round()
    eng.next_round()
    paragraphs = eng.rendered_story()
    assert_true(len(paragraphs) > 0, "story2 has paragraphs")
    import re
    full = "\n".join(paragraphs)
    unfilled = re.findall(r"\{[A-Z_]+\}", full)
    assert_eq(unfilled, [], "no unfilled placeholders in story2")
    for r in eng.rounds:
        assert_true(r.winning_index is not None, f"round {r.id} has a winner")
        winning_word = r.options[r.winning_index]
        assert_true(winning_word in full, f"winning word '{winning_word}' ({r.story_slot}) in story2")


def test_switch_story_resets_strategies():
    banner("Switching story resets strategy rotation correctly")
    eng = GameEngine(STORY2, round_duration_s=15)
    # Verify story2 has mixed strategies
    assert_eq(eng.rounds[1].strategy, Strategy.LEAST_POPULAR, "story2 round 2 starts with least_popular")
    # Switch to story1 - should get all most_popular
    eng.switch_story(STORY1)
    for r in eng.rounds:
        assert_eq(r.strategy, Strategy.MOST_POPULAR, f"after switch: story1 round {r.id} = most_popular")
    # Switch back to story2 - should get mixed again
    eng.switch_story(STORY2)
    assert_eq(eng.rounds[1].strategy, Strategy.LEAST_POPULAR, "after switch back: story2 round 2 = least_popular")
    assert_eq(eng.rounds[2].strategy, Strategy.RANDOM, "after switch back: story2 round 3 = random")


def test_reset_clears_game_started():
    banner("Reset clears game_has_started flag")
    eng = GameEngine(STORY1, round_duration_s=15)
    assert_true(not eng.game_has_started(), "not started initially")
    eng.start_round()
    assert_true(eng.game_has_started(), "started after start_round")
    eng.reset()
    assert_true(not eng.game_has_started(), "not started after reset")


# ------------------------------------------------------------------ run all

if __name__ == "__main__":
    test_loading()
    test_loading_from_path()
    test_all_strategies()
    test_full_game_flow_story1()
    test_full_game_flow_story2()
    test_final_story_rendering()
    test_final_story_segments()
    test_snapshot_roundtrip()
    test_host_override()
    test_late_votes_rejected()
    test_story1_always_most_popular()
    test_switch_story()
    test_duration_control()
    test_story2_strategy_rotation()
    test_story2_final_story_rendering()
    test_switch_story_resets_strategies()
    test_reset_clears_game_started()
    print("\n✦ All tests passed! ✦\n")
