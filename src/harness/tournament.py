"""Round-robin self-play on our simulator: candidates (and idle) in both seats, many seeds.

Incremental and cached: `run` plays only the (seat 0, seat 1, seed) triples not played yet,
seeds in order, within a time budget; adding an entry later plays just its new pairings.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path

from harness.sandbox import MatchResult, play_matches

_PROBLEM_KEYS = ("forfeits", "exceptions", "timeouts", "malformed", "illegal")
MAX_KILLS = 2  # a match cut off this often by the time budget is given up (invalid)
_GUARD_KEYS = ("core_exceptions", "core_timeouts", "core_bad_output", "fallback_used", "none_used")


@dataclass
class Standing:
    id: str
    played: int
    wins: int
    draws: int
    losses: int
    points: float  # wins + 0.5 * draws
    win_rate: float  # points / played (0 if none)
    mean_margin: float  # mean (own - opponent) score, per match
    problems: dict = field(default_factory=dict)  # summed exceptions, timeouts, malformed, ...


def _outcome(r: MatchResult, seat: int) -> int:
    """+1 win, 0 draw, -1 loss for `seat` in a valid result."""
    if r.winner is None:
        return 0
    return 1 if r.winner == seat else -1


def _valid(r: MatchResult) -> bool:
    return r.sim_error is None and r.scores is not None and len(r.scores) == 2


class Tournament:
    def __init__(
        self,
        sim_path: Path,
        workdir: Path,
        *,
        seeds: list[int],
        workers: int = 2,
        act_timeout_s: float = 1.0,
        max_turns: int = 5000,
        draw_tolerance: float = 0.01,
    ) -> None:
        self.sim_path = Path(sim_path)
        self.workdir = Path(workdir)
        self.seeds = list(seeds)
        self.workers = max(1, int(workers))
        self.act_timeout_s = act_timeout_s
        self.max_turns = max_turns
        self.draw_tolerance = draw_tolerance
        self.entries: dict[str, Path | None] = {}
        self.results: list[MatchResult] = []
        self._done: dict[tuple[str, str, int], MatchResult] = {}
        self._match_secs = 0.0  # wall seconds x workers used, over _match_count matches
        self._match_count = 0
        self._kills: dict[tuple[str, str, int], int] = {}  # cut off while being played

    def add(self, entry_id: str, strategy_path: Path | None) -> None:
        """Add an entry (None only for 'idle'). Re-adding an id with a new file replays it."""
        path = None if strategy_path is None else Path(strategy_path)
        if entry_id in self.entries and self.entries[entry_id] != path:
            self._done = {k: v for k, v in self._done.items() if entry_id not in k[:2]}
            self.results = [r for r in self.results if entry_id not in r.players]
        self.entries[entry_id] = path

    def pending(self) -> list[tuple[str, str, int]]:
        """Unplayed (seat 0, seat 1, seed) triples, seed by seed."""
        ids = list(self.entries)
        out = []
        for seed in self.seeds:
            for a in ids:
                for b in ids:
                    if a != b and (a, b, seed) not in self._done:
                        out.append((a, b, seed))
        return out

    def _per_match_s(self) -> float | None:
        if self._match_count == 0:
            return None
        return self._match_secs / self._match_count

    def run(self, *, budget_s: float) -> None:
        """Play pending pairings within about budget_s seconds of wall time."""
        t0 = time.monotonic()
        while True:
            todo = self.pending()
            if not todo:
                return
            # Matches that were cut off while being played go last: a match too slow for the
            # budget must not block every later run (and the new entries' pairings).
            todo.sort(key=lambda p: self._kills.get(p, 0))
            left = budget_s - (time.monotonic() - t0)
            per_match = self._per_match_s()
            if per_match is None:
                # No estimate yet: one full round of the first pending seed, if possible.
                first_seed = todo[0][2]
                batch = [p for p in todo if p[2] == first_seed]
                if left <= 0:
                    return
            else:
                affordable = int(left * self.workers * 0.9 / max(per_match, 1e-3))
                if affordable < 1:
                    return
                batch = todo[:affordable]
                round_size = sum(1 for p in todo if p[2] == todo[0][2])
                if len(batch) > round_size:  # cut at a seed boundary: balanced partial runs
                    seeds_in = [p[2] for p in batch]
                    last = seeds_in[-1]
                    if len(batch) < len(todo) and todo[len(batch)][2] == last:
                        trimmed = [p for p in batch if p[2] != last]
                        batch = trimmed or batch
            started = time.monotonic()
            timeout = max(10.0, left + 5.0)
            entries = {k: v for k, v in self.entries.items()}
            results = play_matches(
                self.sim_path,
                entries,
                batch,
                act_timeout_s=self.act_timeout_s,
                max_turns=self.max_turns,
                workers=self.workers,
                timeout_s=timeout,
                draw_tolerance=self.draw_tolerance,
                work_dir=self.workdir,
            )
            wall = time.monotonic() - started
            finished = 0
            killed = False
            for key, r in zip(batch, results, strict=True):
                err = r.sim_error or ""
                if err.startswith("not started") or (err == "timeout" and not r.in_progress):
                    killed = True  # never played: not cached, retried next run
                    continue
                if err.startswith("timeout"):
                    killed = True  # cut by our own deadline while being played
                    self._kills[key] = self._kills.get(key, 0) + 1
                    if self._kills[key] < MAX_KILLS:
                        continue  # retried (last) next run; cached as invalid after MAX_KILLS
                finished += 1
                self._done[key] = r
                self.results.append(r)
            if finished:
                parallel = min(self.workers, len(batch))
                self._match_secs += wall * parallel
                self._match_count += finished
            if killed or finished == 0:
                return

    # ---------------------------------------------------------------------------- results

    def _valid_results(self) -> list[MatchResult]:
        return [r for r in self.results if _valid(r)]

    def standings(self) -> list[Standing]:
        """One row per entry, best first (win rate, then points, then margin)."""
        rows = {
            e: Standing(e, 0, 0, 0, 0, 0.0, 0.0, 0.0, dict.fromkeys(_PROBLEM_KEYS + _GUARD_KEYS, 0))
            for e in self.entries
        }
        margins: dict[str, float] = dict.fromkeys(self.entries, 0.0)
        for r in self._valid_results():
            for seat in range(2):
                eid = r.players[seat]
                if eid not in rows:
                    continue
                row = rows[eid]
                row.played += 1
                o = _outcome(r, seat)
                row.wins += o == 1
                row.draws += o == 0
                row.losses += o == -1
                margins[eid] += r.scores[seat] - r.scores[1 - seat]
                stats = r.player_stats[seat] if seat < len(r.player_stats) else {}
                p = row.problems
                p["forfeits"] += stats.get("forfeit_turn") is not None
                for k in ("exceptions", "timeouts", "malformed", "illegal"):
                    p[k] += int(stats.get(k) or 0)
                gs = stats.get("guard_stats") or {}
                if isinstance(gs, dict):
                    for k in _GUARD_KEYS:
                        v = gs.get(k)
                        if isinstance(v, int | float):
                            p[k] += int(v)
        for e, row in rows.items():
            row.points = row.wins + 0.5 * row.draws
            row.win_rate = row.points / row.played if row.played else 0.0
            row.mean_margin = margins[e] / row.played if row.played else 0.0
        return sorted(rows.values(), key=lambda s: (-s.win_rate, -s.points, -s.mean_margin, s.id))

    def head_to_head(self, a: str, b: str) -> tuple[int, int, int]:
        """Wins, draws and losses of `a` against `b`, both seats."""
        w = d = losses = 0
        for r in self._valid_results():
            if set(r.players) != {a, b} or a == b:
                continue
            o = _outcome(r, r.players.index(a))
            w += o == 1
            d += o == 0
            losses += o == -1
        return w, d, losses

    def report(self, focus: str | None = None, max_chars: int = 4000) -> str:
        """Plain-text summary for prompts and logs."""
        valid = self._valid_results()
        invalid = [r for r in self.results if not _valid(r)]
        lines = [
            f"Tournament: {len(valid)} valid matches, {len(invalid)} invalid (simulator errors), "
            f"{len(self.entries)} entries, seeds played "
            f"{len({r.seed for r in valid})}/{len(self.seeds)}, pending {len(self.pending())}",
            "",
            "Standings (W-D-L; win% = (W + D/2) / played; margin = mean own - opponent score):",
            f"{'#':>2} {'entry':<18} {'played':>6} {'W-D-L':>11} {'win%':>6} {'margin':>9} "
            "forfeit exc tmo malformed illegal",
        ]
        for i, s in enumerate(self.standings(), 1):
            p = s.problems
            lines.append(
                f"{i:>2} {s.id[:18]:<18} {s.played:>6} {f'{s.wins}-{s.draws}-{s.losses}':>11} "
                f"{100 * s.win_rate:>5.1f}% {s.mean_margin:>9.1f} {p['forfeits']:>7} "
                f"{p['exceptions']:>3} {p['timeouts']:>3} {p['malformed']:>9} {p['illegal']:>7}"
            )
        if focus is not None and focus in self.entries:
            lines += ["", *self._focus_lines(focus, valid)]
        errors = self._error_lines(invalid)
        if errors:
            lines += ["", *errors]
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 20].rstrip() + "\n... (truncated)"
        return text

    def _focus_lines(self, focus: str, valid: list[MatchResult]) -> list[str]:
        mine = [r for r in valid if focus in r.players]
        out = [f"Focus: {focus} ({len(mine)} matches)"]
        for opp in self.entries:
            if opp == focus:
                continue
            per_seat = []
            margin, n = 0.0, 0
            for seat in range(2):
                rs = [r for r in mine if r.players[seat] == focus and r.players[1 - seat] == opp]
                os_ = [_outcome(r, seat) for r in rs]
                per_seat.append(f"{os_.count(1)}-{os_.count(0)}-{os_.count(-1)}")
                margin += sum(r.scores[seat] - r.scores[1 - seat] for r in rs)
                n += len(rs)
            if n == 0:
                continue
            w, d, loss = self.head_to_head(focus, opp)
            out.append(
                f"  vs {opp[:18]:<18} {w}-{d}-{loss}  (as seat 0: {per_seat[0]}, as seat 1: "
                f"{per_seat[1]})  mean margin {margin / n:+.1f}"
            )
        for seat in range(2):
            rs = [r for r in mine if r.players[seat] == focus]
            if not rs:
                continue
            os_ = [_outcome(r, seat) for r in rs]
            m = sum(r.scores[seat] - r.scores[1 - seat] for r in rs) / len(rs)
            out.append(
                f"  seat {seat}: {os_.count(1)}-{os_.count(0)}-{os_.count(-1)}, "
                f"mean margin {m:+.1f}"
            )
        stats = [r.player_stats[r.players.index(focus)] for r in mine if r.player_stats]
        if stats:
            max_act = max(float(s.get("max_act_s") or 0.0) for s in stats)
            mean_act = sum(float(s.get("mean_act_s") or 0.0) for s in stats) / len(stats)
            out.append(f"  act time: max {max_act:.3f} s, mean {mean_act:.4f} s")
            totals: dict[str, float] = {}
            for s in stats:
                gs = s.get("guard_stats")
                if isinstance(gs, dict):
                    for k in ("turns", "core_ok", *_GUARD_KEYS):
                        v = gs.get(k)
                        if isinstance(v, int | float):
                            totals[k] = totals.get(k, 0) + v
            if totals:
                out.append(
                    "  guard: "
                    + ", ".join(
                        f"{k} {int(v)}" for k, v in totals.items() if v or k in ("turns", "core_ok")
                    )
                )
            first = next((s.get("first_error") for s in stats if s.get("first_error")), None)
            if first:
                out.append(f"  first error: {_clip(first, 800)}")
            gtb = next(
                (
                    s["guard_stats"].get("first_core_traceback")
                    for s in stats
                    if isinstance(s.get("guard_stats"), dict)
                    and s["guard_stats"].get("first_core_traceback")
                ),
                None,
            )
            if gtb:
                out.append(f"  first core failure (handled by the guard): {_clip(gtb, 800)}")
        return out

    def _error_lines(self, invalid: list[MatchResult]) -> list[str]:
        out = []
        seen: set[str] = set()
        for r in self._valid_results():
            for seat, stats in enumerate(r.player_stats):
                eid = r.players[seat]
                err = stats.get("first_error")
                if err and eid not in seen and eid != "idle":
                    seen.add(eid)
                    out.append(
                        f"First error of {eid} (seed {r.seed}, seat {seat}): {_clip(err, 600)}"
                    )
        cut = [r for r in invalid if (r.sim_error or "").startswith("timeout")]
        crashed = [r for r in invalid if not (r.sim_error or "").startswith("timeout")]
        if crashed:
            r = crashed[0]
            out.append(
                f"First simulator error ({r.players[0]} vs {r.players[1]}, seed {r.seed}): "
                f"{_clip(r.sim_error or '', 800)}"
            )
        if cut:
            r = cut[0]
            out.append(
                f"{len(cut)} match(es) never finished within the time budget (a strategy or the "
                f"simulator is too slow), e.g. {r.players[0]} vs {r.players[1]}, seed {r.seed}"
            )
        return (["Problems:", *out]) if out else []


def _clip(text: str, n: int) -> str:
    """The last n characters of a traceback (where the error is), on one indented block."""
    text = text.strip()
    if len(text) > n:
        text = "..." + text[-n:]
    return text.replace("\n", "\n    ")
