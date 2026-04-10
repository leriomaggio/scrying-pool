"""
Game engine for The Scrying Pool: Mad Libs Audience Game.

The engine manages game state, votes, and the six selection strategies
that secretly decide which word wins each round.

The audience thinks the most popular answer always wins.
The audience is wrong.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any


class GamePhase(str, Enum):
    WAITING = "waiting"       # round not started yet (lobby / between rounds)
    VOTING = "voting"         # countdown running, votes being collected
    REVEALED = "revealed"     # winning word shown
    FINAL = "final"           # all rounds done, final story shown


class Strategy(str, Enum):
    MOST_POPULAR = "most_popular"
    LEAST_POPULAR = "least_popular"
    RANDOM = "random"
    SECOND_PLACE = "second_place"
    HOST_CHOICE = "host_choice"
    INVERSE_MOMENTUM = "inverse_momentum"

    @property
    def label(self) -> str:
        return {
            "most_popular": "Most Popular",
            "least_popular": "The Underdog",
            "random": "Random Draw",
            "second_place": "The Runner-Up",
            "host_choice": "Host's Wild Card",
            "inverse_momentum": "Late Surge",
        }[self.value]

    @property
    def flavour(self) -> str:
        return {
            "most_popular": "The crowd has spoken.",
            "least_popular": "Only a brave few voted for this one... and yet...",
            "random": "The dice of fate have rolled.",
            "second_place": "So close. Yet, not quite.",
            "host_choice": "The host has decided. No, you may not argue.",
            "inverse_momentum": "A late surge from the shadows.",
        }[self.value]


@dataclass
class Round:
    id: int
    format: str                  # "standard" | "misleading_poll"
    poll_question: str
    category_label: str          # e.g. "ADJECTIVE" or "HOT TAKE"
    story_slot: str              # e.g. "HERO_ADJECTIVE"
    options: list[str]
    strategy: Strategy
    # runtime:
    votes: dict[str, int] = field(default_factory=dict)  # client_id -> option_index
    vote_history: list[tuple[float, int]] = field(default_factory=list)  # (timestamp, option_index) for momentum
    opened_at: float | None = None
    closes_at: float | None = None
    winning_index: int | None = None
    host_override_index: int | None = None  # for HOST_CHOICE or manual override

    def tally(self) -> list[int]:
        counts = [0] * len(self.options)
        for idx in self.votes.values():
            if 0 <= idx < len(counts):
                counts[idx] += 1
        return counts

    def total_votes(self) -> int:
        return len(self.votes)

    def to_public_dict(self, reveal_strategy: bool = False) -> dict[str, Any]:
        """Data safe to send to audience / screen. Omits the real story slot."""
        data = {
            "id": self.id,
            "format": self.format,
            "poll_question": self.poll_question,
            "category_label": self.category_label,
            "options": self.options,
            "tally": self.tally(),
            "total_votes": self.total_votes(),
            "opened_at": self.opened_at,
            "closes_at": self.closes_at,
            "winning_index": self.winning_index,
            "winning_word": self.options[self.winning_index] if self.winning_index is not None else None,
        }
        if reveal_strategy:
            data["strategy"] = self.strategy.value
            data["strategy_label"] = self.strategy.label
            data["strategy_flavour"] = self.strategy.flavour
        return data

    def to_host_dict(self) -> dict[str, Any]:
        """Full round data for host dashboard (includes story slot + strategy)."""
        d = self.to_public_dict(reveal_strategy=True)
        d["story_slot"] = self.story_slot
        d["host_override_index"] = self.host_override_index
        return d


class GameEngine:
    def __init__(self, story_path: Path, round_duration_s: int = 30):
        self.story_path = Path(story_path)
        self.round_duration_s = round_duration_s
        self._load_story()
        self.phase: GamePhase = GamePhase.WAITING
        self.current_round_index: int = 0
        self.version: int = 0  # monotonic, incremented on every state change
        self.state_event_log: list[dict[str, Any]] = []  # for debugging / snapshot

    # ---------- story loading ----------

    def _load_story(self) -> None:
        with self.story_path.open("r", encoding="utf-8") as f:
            self.story: dict[str, Any] = json.load(f)
        rotation = self.story["strategy_rotation"]
        rounds_raw = self.story["rounds"]
        self.rounds: list[Round] = []
        for i, r in enumerate(rounds_raw):
            strat_name = rotation[i % len(rotation)]
            self.rounds.append(Round(
                id=r["id"],
                format=r["format"],
                poll_question=r["poll_question"],
                category_label=r["category_label"],
                story_slot=r["story_slot"],
                options=list(r["options"]),
                strategy=Strategy(strat_name),
            ))

    # ---------- state helpers ----------

    def _bump(self) -> None:
        self.version += 1

    def current_round(self) -> Round | None:
        if 0 <= self.current_round_index < len(self.rounds):
            return self.rounds[self.current_round_index]
        return None

    # ---------- host actions ----------

    def start_round(self) -> None:
        r = self.current_round()
        if r is None:
            self.phase = GamePhase.FINAL
            self._bump()
            return
        r.opened_at = time.time()
        r.closes_at = r.opened_at + self.round_duration_s
        r.winning_index = None
        r.votes.clear()
        r.vote_history.clear()
        self.phase = GamePhase.VOTING
        self._bump()

    def end_voting(self) -> None:
        """Close voting early (host button); does not reveal yet."""
        r = self.current_round()
        if r and self.phase == GamePhase.VOTING:
            r.closes_at = time.time()
            self._bump()

    def reveal(self) -> None:
        """Compute the winner using the round's strategy and show it."""
        r = self.current_round()
        if r is None:
            return
        if r.host_override_index is not None:
            r.winning_index = r.host_override_index
        else:
            r.winning_index = self._pick_winner(r)
        self.phase = GamePhase.REVEALED
        self._bump()

    def next_round(self) -> None:
        if self.current_round_index < len(self.rounds) - 1:
            self.current_round_index += 1
            self.phase = GamePhase.WAITING
        else:
            self.phase = GamePhase.FINAL
        self._bump()

    def show_final(self) -> None:
        self.phase = GamePhase.FINAL
        self._bump()

    def reset(self) -> None:
        self.current_round_index = 0
        self.phase = GamePhase.WAITING
        for r in self.rounds:
            r.votes.clear()
            r.vote_history.clear()
            r.opened_at = None
            r.closes_at = None
            r.winning_index = None
            r.host_override_index = None
        self._bump()

    def set_host_override(self, option_index: int | None) -> None:
        r = self.current_round()
        if r is not None:
            r.host_override_index = option_index
            self._bump()

    def override_strategy(self, strategy: Strategy) -> None:
        r = self.current_round()
        if r is not None:
            r.strategy = strategy
            self._bump()

    # ---------- audience actions ----------

    # Grace period for votes arriving just after closes_at. Covers the normal
    # network + clock-skew jitter between a phone and the server so a vote
    # the user *initiated* while the countdown was still on the screen
    # doesn't get silently dropped by a few hundred milliseconds of latency.
    VOTE_GRACE_S = 0.5

    def record_vote(self, client_id: str, option_index: int) -> bool:
        r = self.current_round()
        if r is None or self.phase != GamePhase.VOTING:
            return False
        if not (0 <= option_index < len(r.options)):
            return False
        # Enforce the round deadline server-side. The countdown on the phone
        # is a UX hint; this is the authoritative cutoff.
        if r.closes_at is not None and time.time() > r.closes_at + self.VOTE_GRACE_S:
            return False
        # One vote per client, but re-voting is allowed (replaces previous)
        r.votes[client_id] = option_index
        r.vote_history.append((time.time(), option_index))
        self._bump()
        return True

    # ---------- strategies ----------

    def _pick_winner(self, r: Round) -> int:
        tally = r.tally()
        n = len(r.options)

        # Edge case: no votes at all → random
        if sum(tally) == 0:
            return random.randrange(n)

        strat = r.strategy

        if strat == Strategy.MOST_POPULAR:
            return max(range(n), key=lambda i: tally[i])

        if strat == Strategy.LEAST_POPULAR:
            # Among options with the fewest votes (but prefer ones with >=1 vote if any have votes)
            voted = [i for i in range(n) if tally[i] > 0]
            pool = voted if voted else list(range(n))
            return min(pool, key=lambda i: tally[i])

        if strat == Strategy.RANDOM:
            # Unweighted random over all options (even unvoted ones): maximum chaos
            return random.randrange(n)

        if strat == Strategy.SECOND_PLACE:
            ranked = sorted(range(n), key=lambda i: tally[i], reverse=True)
            # Find the first option with strictly fewer votes than the top
            top_score = tally[ranked[0]]
            for idx in ranked[1:]:
                if tally[idx] < top_score:
                    return idx
            # Fallback (everyone tied) → pick the second in ranking
            return ranked[1] if len(ranked) > 1 else ranked[0]

        if strat == Strategy.HOST_CHOICE:
            # If host didn't override, fall through to most popular
            return max(range(n), key=lambda i: tally[i])

        if strat == Strategy.INVERSE_MOMENTUM:
            # Look at the second half of vote_history, find the option with
            # the biggest late-stage share relative to its overall share.
            history = r.vote_history
            if len(history) < 4:
                return max(range(n), key=lambda i: tally[i])
            mid = len(history) // 2
            late = history[mid:]
            late_counts = [0] * n
            for _, idx in late:
                late_counts[idx] += 1
            late_total = sum(late_counts)
            if late_total == 0:
                return max(range(n), key=lambda i: tally[i])
            # Score = late share - overall share. The option that surged the most wins.
            total = sum(tally)
            scores = []
            for i in range(n):
                late_share = late_counts[i] / late_total
                overall_share = tally[i] / total if total else 0
                scores.append(late_share - overall_share)
            return max(range(n), key=lambda i: scores[i])

        # Default safety net
        return max(range(n), key=lambda i: tally[i])

    # ---------- serialisation ----------

    def public_state(self) -> dict[str, Any]:
        """State payload for audience + screen (no secrets)."""
        r = self.current_round()
        return {
            "version": self.version,
            "phase": self.phase.value,
            "title": self.story.get("title"),
            "subtitle": self.story.get("subtitle"),
            "round_index": self.current_round_index,
            "total_rounds": len(self.rounds),
            "round": r.to_public_dict(reveal_strategy=(self.phase == GamePhase.REVEALED)) if r else None,
            "final_story": self.rendered_story() if self.phase == GamePhase.FINAL else None,
            "final_story_segments": self.rendered_story_segments() if self.phase == GamePhase.FINAL else None,
        }

    def host_state(self) -> dict[str, Any]:
        """State payload for host dashboard (reveals everything)."""
        r = self.current_round()
        return {
            "version": self.version,
            "phase": self.phase.value,
            "title": self.story.get("title"),
            "subtitle": self.story.get("subtitle"),
            "round_index": self.current_round_index,
            "total_rounds": len(self.rounds),
            "round": r.to_host_dict() if r else None,
            "all_rounds_summary": [
                {
                    "id": rr.id,
                    "category_label": rr.category_label,
                    "story_slot": rr.story_slot,
                    "strategy": rr.strategy.value,
                    "winning_word": rr.options[rr.winning_index] if rr.winning_index is not None else None,
                }
                for rr in self.rounds
            ],
            "final_story": self.rendered_story() if self.phase == GamePhase.FINAL else None,
            "story_template": self.story.get("story_template"),
            "narrator_intro": self.story.get("narrator_intro"),
        }

    def rendered_story(self) -> list[str]:
        """Render the final story, substituting each {SLOT} with the winning word."""
        slot_values: dict[str, str] = {}
        for r in self.rounds:
            if r.winning_index is not None:
                slot_values[r.story_slot] = r.options[r.winning_index]
            else:
                slot_values[r.story_slot] = f"[{r.story_slot}]"
        paragraphs = self.story.get("story_template", [])
        rendered = []
        for p in paragraphs:
            out = p
            for slot, value in slot_values.items():
                out = out.replace("{" + slot + "}", value)
            rendered.append(out)
        return rendered

    def rendered_story_segments(self) -> list[list[dict[str, Any]]]:
        """Like rendered_story() but returns each paragraph as a list of
        ``{"text": str, "hl": bool, "char": bool}`` segments.

        ``hl=True`` marks a word that was filled in from a round's winning
        vote (rendered in blue + bold on the big screen).

        ``char=True`` marks a recurring fantasy character name (rendered in
        blue + italic). The list of names to highlight comes from the
        story's ``highlighted_characters`` list.
        """
        import re
        slot_values: dict[str, str] = {}
        for r in self.rounds:
            if r.winning_index is not None:
                slot_values[r.story_slot] = r.options[r.winning_index]
        paragraphs = self.story.get("story_template", [])
        slot_pattern = re.compile(r"\{([A-Z_][A-Z0-9_]*)\}")

        # Build a character-name matcher from the story config. Sort by
        # length descending so longer names match first ("Alex the Wizard"
        # before "Alex"), avoiding partial matches.
        character_names = self.story.get("highlighted_characters") or []
        character_pattern = None
        if character_names:
            sorted_names = sorted(character_names, key=len, reverse=True)
            character_pattern = re.compile(
                "(" + "|".join(re.escape(n) for n in sorted_names) + ")"
            )

        def split_plain(text: str) -> list[dict[str, Any]]:
            """Split a plain-text segment further, tagging character names."""
            if not text or character_pattern is None:
                return [{"text": text, "hl": False, "char": False}] if text else []
            out: list[dict[str, Any]] = []
            cursor = 0
            for m in character_pattern.finditer(text):
                if m.start() > cursor:
                    out.append({"text": text[cursor:m.start()], "hl": False, "char": False})
                out.append({"text": m.group(0), "hl": False, "char": True})
                cursor = m.end()
            if cursor < len(text):
                out.append({"text": text[cursor:], "hl": False, "char": False})
            return out

        result: list[list[dict[str, Any]]] = []
        for p in paragraphs:
            segments: list[dict[str, Any]] = []
            cursor = 0
            for match in slot_pattern.finditer(p):
                if match.start() > cursor:
                    segments.extend(split_plain(p[cursor:match.start()]))
                slot = match.group(1)
                if slot in slot_values:
                    segments.append({"text": slot_values[slot], "hl": True, "char": False})
                else:
                    # Slot never got a winner (shouldn't happen in a
                    # complete game); leave the placeholder visible so
                    # we can see what's missing.
                    segments.append({"text": f"[{slot}]", "hl": False, "char": False})
                cursor = match.end()
            if cursor < len(p):
                segments.extend(split_plain(p[cursor:]))
            result.append(segments)
        return result

    # ---------- snapshot persistence ----------

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "phase": self.phase.value,
            "current_round_index": self.current_round_index,
            "rounds": [
                {
                    "id": r.id,
                    "votes": r.votes,
                    "vote_history": r.vote_history,
                    "opened_at": r.opened_at,
                    "closes_at": r.closes_at,
                    "winning_index": r.winning_index,
                    "host_override_index": r.host_override_index,
                    "strategy": r.strategy.value,
                }
                for r in self.rounds
            ],
        }

    def restore(self, snap: dict[str, Any]) -> None:
        try:
            self.version = int(snap.get("version", 0))
            self.phase = GamePhase(snap.get("phase", "waiting"))
            self.current_round_index = int(snap.get("current_round_index", 0))
            for r, saved in zip(self.rounds, snap.get("rounds", [])):
                r.votes = {str(k): int(v) for k, v in saved.get("votes", {}).items()}
                r.vote_history = [(float(t), int(i)) for t, i in saved.get("vote_history", [])]
                r.opened_at = saved.get("opened_at")
                r.closes_at = saved.get("closes_at")
                r.winning_index = saved.get("winning_index")
                r.host_override_index = saved.get("host_override_index")
                if saved.get("strategy"):
                    r.strategy = Strategy(saved["strategy"])
        except Exception:
            # If snapshot is corrupt, start fresh rather than crash
            self.reset()
