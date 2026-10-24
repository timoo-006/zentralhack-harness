"""Measure harness runs: conformance, strength against the baselines, spend and time.

Dev-only (imports arena). For every run directory (runs/<task>/<time>/, as `harness TASK_DIR`
and scripts/run_harness.sh leave it) or strategy file:

1. check: `arena check` (isolated, every seat against idle; must never forfeit);
2. panel: round robin against the game's BASELINES, both seats, `--seeds` seeds;
3. `--cross`: a tournament between the strategies of different groups (`--group NAME=GLOB`,
   e.g. two harness versions), never pairing two of one group, scored +1/0/-1 like the
   evaluation (draw: |s0 - s1| <= 1% of |s0| + |s1|; a forfeit loses);
4. logs: spend, calls and wall time from output/logs/{transcript,events}.jsonl and
   run_meta.json, against the task's budget_usd and deadline_s.

Prints a markdown summary on stdout and appends one JSON line per strategy to
runs/eval_log.jsonl. Isolation needs Linux: run it in WSL (or pass --wsl on Windows, which
re-runs it there); plain Windows falls back to in-process play, without time limits.

    python scripts/evaluate.py runs/beta-B                     # every run of that task
    python scripts/evaluate.py --group old='runs-old/beta-B/*' --group new='runs/beta-B/*' --cross
"""

import argparse
import glob
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from itertools import combinations
from multiprocessing import get_context
from pathlib import Path

from make_tasks import GAMES, REPO

RUN_NAME = re.compile(r"^\d{8}-\d{6}$")  # runs/<task>/<YYYYmmdd-HHMMSS>/
# Isolated play (arena: process groups, signals, rlimits) needs POSIX: Linux and macOS.
# Elsewhere (Windows) the script re-runs itself in WSL with --wsl, or plays in-process.
LINUX = os.name == "posix"
WSL_PY = "/root/venv-harness/bin/python"


@dataclass
class Tally:
    """Results of one strategy (or group): +1/0/-1 per match, like the evaluation."""

    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    margin_sum: float = 0.0
    forfeits: int = 0  # matches in which this strategy forfeited
    per_opponent: dict[str, list[int]] = field(default_factory=dict)  # id -> [w, d, l]

    def add(self, result: int, margin: float, forfeited: bool, opponent: str) -> None:
        self.played += 1
        self.wins += result == 1
        self.draws += result == 0
        self.losses += result == -1
        self.margin_sum += margin
        self.forfeits += forfeited
        self.per_opponent.setdefault(opponent, [0, 0, 0])[1 - result] += 1

    @property
    def net(self) -> int:
        return self.wins - self.losses

    @property
    def score(self) -> float:  # points / played, a draw is half a win
        return (self.wins + 0.5 * self.draws) / self.played if self.played else 0.0

    @property
    def margin(self) -> float:
        return self.margin_sum / self.played if self.played else 0.0

    def to_dict(self) -> dict:
        return asdict(self) | {"net": self.net, "score": self.score, "mean_margin": self.margin}

    def wdl(self) -> str:
        return f"{self.wins}-{self.draws}-{self.losses}"


@dataclass
class Run:
    id: str
    group: str
    path: Path  # the run directory (or the strategy file's directory)
    strategy: Path | None  # None: the run left no strategy
    task_name: str
    task: dict
    meta: dict
    game: str | None = None  # key of GAMES, or a module
    logs: dict = field(default_factory=dict)
    check: dict | None = None
    panel: Tally | None = None
    cross: Tally | None = None

    @property
    def target(self) -> str:
        return f"{self.strategy.resolve()}:{self.task.get('strategy_class') or 'Strategy'}"


# ---------------------------------------------------------------------------- collecting runs


def _is_run_dir(p: Path) -> bool:
    return p.is_dir() and (
        (p / "output").is_dir() or (p / "run_meta.json").is_file() or bool(RUN_NAME.match(p.name))
    )


def expand(pattern: str) -> list[Path]:
    """Run directories (or strategy files) for a path or glob; a task's runs/<task>/ directory
    stands for every run in it (not `latest`), and runs/ for every run of every task."""
    found = []
    for m in [Path(p) for p in sorted(glob.glob(pattern))] or [Path(pattern)]:
        if m.is_file() and m.suffix == ".py":
            found.append(m)
        elif _is_run_dir(m):
            found.append(m)
        elif m.is_dir():
            subdirs = sorted(d for d in m.iterdir() if d.is_dir() and d.name != "latest")
            runs = [d for d in subdirs if _is_run_dir(d)]
            if not runs:  # runs/: one more level
                runs = [d for s in subdirs for d in sorted(s.iterdir()) if d.name != "latest"
                        and _is_run_dir(d)]  # fmt: skip
            found += runs
    return found


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _windows_path(p: str) -> str:
    m = re.match(r"^/mnt/([a-z])/(.*)$", p)
    return f"{m[1].upper()}:/{m[2]}" if m and not LINUX else p


def make_run(path: Path, group: str, tasks_dir: Path, game: str | None) -> Run:
    path = path.resolve()  # runs/<task>/latest is a link: use the run directory it names
    in_run = path.is_file() and path.parent.name == "output"
    if path.is_file():  # a bare strategy file: .../output/strategy.py or anything
        strategy = path
        run_dir = path.parent.parent if in_run else path.parent
    else:
        run_dir = path
        strategy = None
    meta = _read_json(run_dir / "run_meta.json")
    task_name = run_dir.parent.name if RUN_NAME.match(run_dir.name) else run_dir.name
    task = {}
    # the run's own copy (run_harness.sh), then tasks/<task>/, then where the run read it from
    candidates = [run_dir / "task.json", tasks_dir / task_name / "task.json"]
    if meta.get("task_dir"):
        candidates.append(Path(_windows_path(meta["task_dir"])) / "task.json")
    for c in candidates:
        if c.is_file():
            task = _read_json(c)
            break
    if strategy is None:
        s = run_dir / "output" / (task.get("strategy_file") or "strategy.py")
        strategy = s if s.is_file() else None
    run = Run(
        id=f"{task_name}/{run_dir.name}" if path.is_dir() or in_run else f"{task_name}/{path.stem}",
        group=group,
        path=run_dir,
        strategy=strategy,
        task_name=task_name,
        task=task,
        meta=meta,
    )
    run.game = game or task.get("game_name") or task_name.split("-")[0]
    run.logs = read_logs(run_dir, task, meta)
    return run


def game_ref(key: str) -> str:
    return GAMES.get(key, key)


# ---------------------------------------------------------------------------------- logs


def _jsonl(path: Path) -> list[dict]:
    rows = []
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _num(x) -> float | None:
    return float(x) if isinstance(x, int | float) and not isinstance(x, bool) else None


def _recost(row: dict, prices: dict) -> float | None:
    """Cost of a logged call from its usage (template-format lines carry no cost)."""
    price = prices.get(row.get("model") or "")
    if not row.get("usage") or not price:
        return None
    try:
        from harness.pricing import cost

        return cost(row["usage"], price)
    except Exception:
        return None


def read_logs(run_dir: Path, task: dict, meta: dict) -> dict:
    """Spend, calls and wall time of a run, from what it logged."""
    logs = run_dir / "output" / "logs"
    rows = _jsonl(logs / "transcript.jsonl")
    statuses: Counter[str] = Counter()
    by_model: dict[str, dict] = {}
    spent_sum = spent_max = llm_secs = 0.0
    underlogged = 0  # calls whose logged cost is below what their usage costs
    for row in rows:
        status = str(row.get("status") or "completed")
        statuses[status] += 1
        for key in ("spent_total", "spent_usd"):  # running totals (ours, the template's)
            spent_max = max(spent_max, _num(row.get(key)) or 0.0)
        if status.startswith("skipped"):
            continue
        # Re-price every call from its usage, independently of the harness's own accounting
        # (what the gateway charges); a logged cost may be higher (kept reservations).
        c = _num(row.get("cost"))
        rc = _recost(row, task.get("prices") or {})
        if rc is not None:
            if c is not None and rc > c + 1e-9 + 1e-6 * rc:
                underlogged += 1
            c = rc if c is None else max(c, rc)
        m = by_model.setdefault(str(row.get("model")), {"calls": 0, "cost": 0.0})
        m["calls"] += 1
        m["cost"] += c or 0.0
        spent_sum += c or 0.0
        llm_secs += _num(row.get("secs")) or 0.0

    events = _jsonl(logs / "events.jsonl")
    ev_spent = ev_time = 0.0
    promotions = 0
    for e in events:
        for key in ("spent", "spent_usd", "spent_total", "spend"):
            ev_spent = max(ev_spent, _num(e.get(key)) or 0.0)
        for key in ("elapsed", "elapsed_s", "t", "time_s", "time"):
            ev_time = max(ev_time, _num(e.get(key)) or 0.0)
        name = " ".join(str(e.get(k, "")) for k in ("event", "stage", "type", "kind", "name"))
        name = name.lower()
        promotions += "promot" in name and "reject" not in name  # not promote_rejected

    wall, source = _num(meta.get("wall_s")), "run_meta"
    if wall is None and ev_time:
        wall, source = ev_time, "events"
    if wall is None:
        wall, source = _mtime_wall(run_dir), "mtime"
    spend = max(spent_sum, spent_max, ev_spent)
    budget = _num(task.get("budget_usd"))
    deadline = _num(task.get("deadline_s")) or _num(meta.get("deadline_s"))
    return {
        "spend_usd": round(spend, 6),
        "budget_usd": budget,
        "within_budget": None if budget is None else spend <= budget,
        "cost_underlogged": underlogged,
        "calls": sum(m["calls"] for m in by_model.values()),
        "skipped_calls": sum(n for s, n in statuses.items() if s.startswith("skipped")),
        "statuses": dict(statuses),
        "by_model": by_model,
        "llm_secs": round(llm_secs, 1),
        "events": len(events),
        "promotions": promotions,
        "wall_s": None if wall is None else round(wall, 1),
        "wall_source": source if wall is not None else None,
        "deadline_s": deadline,
        "within_deadline": None if wall is None or deadline is None else wall <= deadline,
        "sigterm": meta.get("sigterm"),
        "sigkill": meta.get("sigkill"),
        "exit_code": meta.get("exit_code"),
        "run_commit": meta.get("commit"),
    }


def _mtime_wall(run_dir: Path) -> float | None:
    """Wall time estimate: run directory name (start) to the newest file under output/."""
    try:
        start = datetime.strptime(run_dir.name, "%Y%m%d-%H%M%S").timestamp()
    except ValueError:
        return None
    newest = [
        os.path.getmtime(os.path.join(d, f))
        for d, _, files in os.walk(run_dir / "output")
        for f in files
    ]
    wall = max(newest) - start if newest else None
    # A negative or absurd value: the name is not the start (copied run, another time zone).
    return wall if wall is not None and 0 <= wall <= 86400 else None


# ------------------------------------------------------------------------------ playing


def _limits(turn_timeout: float):
    from arena.isolation import Limits

    return Limits(turn_timeout=turn_timeout)


def bare_python() -> str | None:
    """The interpreter isolated strategies run under: this venv's base Python, which has no
    third-party packages, like the evaluation's bare Python 3.13. Otherwise a strategy that
    imports e.g. pydantic passes here and forfeits there. None: this interpreter."""
    base = getattr(sys, "_base_executable", None)
    if LINUX and sys.prefix != sys.base_prefix and base and os.access(base, os.X_OK):
        return base
    return None


def check_one(
    ref: str, target: str, seeds: list[int], turn_timeout: float, isolate: bool,
    python: str | None = None,
):  # fmt: skip
    """arena check: the strategy in every seat against idle; it must never forfeit."""
    try:
        from arena.conformance import check_strategy
        from arena.core import Entrant
        from arena.loading import load_game
        from arena.match import play_match

        game_cls = load_game(ref)
        if isolate:
            report = check_strategy(
                game_cls, target, seeds=seeds, limits=_limits(turn_timeout), python=python
            )
            matches = report["matches"]
        else:  # Windows: same matches in this interpreter (no time limits, no isolation)
            cand, idle = Entrant("candidate", target), Entrant("idle", "arena.strategies:Idle")
            n = getattr(game_cls, "n_players", 2)
            matches = [
                play_match(
                    game_cls, [cand if s == seat else idle for s in range(n)], seed
                ).to_dict()
                for seed in seeds
                for seat in range(n)
            ]
        failed = [
            m for m in matches if m["forfeit"] is not None and m["players"][m["forfeit"]] != "idle"
        ]
        first = failed[0] if failed else {}
        return {
            "passed": not failed,
            "matches": len(matches),
            "forfeits": len(failed),
            "reason": first.get("forfeit_reason"),
            "error": (first.get("error") or "")[-2000:] or None,
        }
    except Exception:
        return {"passed": False, "matches": 0, "forfeits": 0, "reason": "evaluator error",
                "error": traceback.format_exc()[-2000:]}  # fmt: skip


def outcome(scores: list[float], forfeited: list[bool], tol: float) -> list[int]:
    """+1/0/-1 per seat: a lone forfeit loses; else the higher score wins unless the scores
    differ by at most tol * (|s0| + |s1|)."""
    if len(scores) != 2:
        best = max(scores)
        return [1 if s == best and scores.count(best) == 1 else -1 for s in scores]
    if forfeited[0] != forfeited[1]:
        return [-1, 1] if forfeited[0] else [1, -1]
    s0, s1 = scores
    if abs(s0 - s1) <= tol * (abs(s0) + abs(s1)):
        return [0, 0]
    return [1, -1] if s0 > s1 else [-1, 1]


def tally(results, tallies: dict[str, Tally], tol: float) -> None:
    """Add every match to the tallies of the seats whose id is in `tallies`."""
    for r in results:
        n = len(r.players)
        turns = r.forfeit_turns or [None] * n
        forfeited = [turns[s] is not None or r.forfeit == s for s in range(n)]
        res = outcome(r.scores, forfeited, tol)
        for seat, pid in enumerate(r.players):
            if pid in tallies:
                others = [r.scores[o] for o in range(n) if o != seat]
                opponent = ",".join(p for o, p in enumerate(r.players) if o != seat)
                margin = r.scores[seat] - max(others)
                tallies[pid].add(res[seat], margin, forfeited[seat], opponent)


def play(game_cls, seatings, seeds, args, isolate: bool):
    from arena.tournament import iter_matches

    return list(
        iter_matches(
            game_cls,
            seatings,
            seeds,
            isolate=isolate,
            limits=_limits(args.turn_timeout),
            workers=args.workers,
        )
    )


def evaluate_game(key: str, runs: list[Run], args, isolate: bool) -> dict:
    """Panel and cross tournament for the runs of one game; returns cross-group info."""
    from arena.core import Entrant
    from arena.loading import load_game_package
    from arena.tournament import rotations, tables

    package = load_game_package(game_ref(key))
    game_cls = package.GAME
    playable = [r for r in runs if r.strategy is not None]
    seeds = list(range(args.seed_start, args.seed_start + args.seeds))
    python = args.python if isolate else None  # the baselines keep this one (they need arena)
    entrants = {r.id: Entrant(r.id, r.target, python=python) for r in playable}
    info: dict = {}

    if not args.skip_panel and playable:
        panel = [
            Entrant(c.__name__, f"{c.__module__}:{c.__qualname__}", trusted=True)
            for c in package.BASELINES
        ]
        seatings = tables(list(entrants.values()), 2, panel)
        _say(f"{key}: panel, {len(seatings) * len(seeds)} matches vs {[p.id for p in panel]}")
        t0 = time.monotonic()
        tallies = {r.id: Tally() for r in playable}
        tally(play(game_cls, seatings, seeds, args, isolate), tallies, args.draw_tol)
        for r in playable:
            r.panel = tallies[r.id]
        info["panel"] = [p.id for p in panel]
        _say(f"{key}: panel done in {time.monotonic() - t0:.0f}s")

    if args.cross and len(playable) >= 2:
        groups = {r.group for r in playable}
        own = len(groups) < 2  # one group: every strategy is a group of its own
        if own:
            _say(f"{key}: --cross with one group: every strategy against every other")
        group_of = {r.id: (r.id if own else r.group) for r in playable}
        pairs = [(a, b) for a, b in combinations(playable, 2) if group_of[a.id] != group_of[b.id]]
        seatings = [s for a, b in pairs for s in rotations((entrants[a.id], entrants[b.id]))]
        n_cross = args.cross_seeds or args.seeds
        cross_seeds = list(range(args.seed_start, args.seed_start + n_cross))
        _say(f"{key}: cross, {len(seatings) * len(cross_seeds)} matches")
        t0 = time.monotonic()
        results = play(game_cls, seatings, cross_seeds, args, isolate)
        tallies = {r.id: Tally() for r in playable}
        tally(results, tallies, args.draw_tol)
        for r in playable:
            r.cross = tallies[r.id]
        # group totals and group-vs-group results
        gt: dict[str, Tally] = {}
        for r in playable:
            g = gt.setdefault(group_of[r.id], Tally())
            for opp, (w, d, lo) in r.cross.per_opponent.items():
                g.played += w + d + lo
                g.wins, g.draws, g.losses = g.wins + w, g.draws + d, g.losses + lo
                vs = g.per_opponent.setdefault(group_of.get(opp, opp), [0, 0, 0])
                vs[0], vs[1], vs[2] = vs[0] + w, vs[1] + d, vs[2] + lo
            g.margin_sum += r.cross.margin_sum
            g.forfeits += r.cross.forfeits
        info["cross_groups"] = gt
        info["cross_by_strategy"] = own
        _say(f"{key}: cross done in {time.monotonic() - t0:.0f}s")
    return info


# ------------------------------------------------------------------------------ output


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _ok(flag) -> str:
    return "?" if flag is None else ("ok" if flag else "OVER")


def _frac(xs: list) -> str:
    """ok/known, plus how many are unknown (None)."""
    known = [x for x in xs if x is not None]
    unknown = len(xs) - len(known)
    return f"{sum(1 for x in known if x)}/{len(known)}" + (f" ({unknown} ?)" if unknown else "")


def _check_cell(r: Run) -> str:
    if r.strategy is None:
        return "MISSING"
    if r.check is None:
        return "-"
    if r.check["passed"]:
        return "PASS"
    return f"FAIL ({r.check['reason']}, {r.check['forfeits']}/{r.check['matches']})"


def _forfeits(r: Run) -> int:
    """Matches the strategy forfeited in the panel and the cross tournament."""
    return sum(t.forfeits for t in (r.panel, r.cross) if t)


def _check_ok(r: Run) -> bool | None:
    """Check passed; None if it was not run."""
    if r.strategy is None:
        return False
    return None if r.check is None else bool(r.check["passed"])


def markdown(by_game: dict[str, list[Run]], infos: dict[str, dict], args, isolate: bool) -> str:
    out = []
    mode = "isolated" if isolate else "IN-PROCESS (no time limits: not conformance-grade)"
    if isolate:
        mode += f", strategies under {args.python or sys.executable}"
    for key, runs in by_game.items():
        info = infos.get(key, {})
        out.append(f"## {key}: {len(runs)} run(s), {mode}, seeds {args.seeds}\n")
        out.append(
            "| run | group | check | panel W-D-L | net | score | margin | F | spend $ | calls"
            " | wall s | flags |"
        )
        out.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in runs:
            lg, p = r.logs, r.panel
            budget = "?" if lg["budget_usd"] is None else f"{lg['budget_usd']:g}"
            deadline = "?" if lg["deadline_s"] is None else f"{lg['deadline_s']:g}"
            flags = []
            if lg["within_budget"] is False:
                flags.append("BUDGET")
            if lg["within_deadline"] is False:
                flags.append("DEADLINE")
            if lg["sigterm"]:
                flags.append("SIGTERM")
            if lg["sigkill"]:
                flags.append("SIGKILL")
            if lg["cost_underlogged"]:
                flags.append(f"COSTLOG({lg['cost_underlogged']})")  # usage costs more than logged
            if _forfeits(r):
                flags.append(f"FORFEITS({_forfeits(r)})")  # in panel/cross, beyond the check
            wall = "?" if lg["wall_s"] is None else f"{lg['wall_s']:.0f}"
            cells = [
                r.id, r.group, _check_cell(r),
                p.wdl() if p else "-", f"{p.net:+d}" if p else "-",
                f"{p.score:.0%}" if p else "-", f"{p.margin:+.1f}" if p else "-",
                str(p.forfeits) if p else "-",
                f"{lg['spend_usd']:.4f} / {budget} {_ok(lg['within_budget'])}",
                f"{lg['calls']}" + (f" +{lg['skipped_calls']} skipped" * bool(lg["skipped_calls"])),
                f"{wall} / {deadline} {_ok(lg['within_deadline'])}",
                " ".join(flags) or "-",
            ]  # fmt: skip
            out.append("| " + " | ".join(cells) + " |")
        if info.get("panel"):
            out.append("\nvs each baseline (W-D-L, both seats):\n")
            out.append("| run | " + " | ".join(info["panel"]) + " |")
            out.append("|---|" + "---|" * len(info["panel"]))
            for r in runs:
                if r.panel:
                    po = r.panel.per_opponent
                    cells = ["-".join(map(str, po.get(b, [0, 0, 0]))) for b in info["panel"]]
                    out.append(f"| {r.id} | " + " | ".join(cells) + " |")
        if info.get("cross_groups"):
            what = "strategy" if info["cross_by_strategy"] else "group"
            out.append(f"\ncross tournament (+1/0/-1, same {what} never paired):\n")
            out.append("| run | group | W-D-L | net | margin | F |")
            out.append("|---|---|---|---|---|---|")
            for r in sorted(runs, key=lambda r: -(r.cross.net if r.cross else -1e9)):
                if r.cross:
                    c = r.cross
                    out.append(
                        f"| {r.id} | {r.group} | {c.wdl()} | {c.net:+d} | {c.margin:+.1f}"
                        f" | {c.forfeits} |"
                    )
            if not info["cross_by_strategy"]:
                gt = info["cross_groups"]
                names = sorted(gt)
                out.append("\n| group | W-D-L | net | net/strategy | " + " | ".join(
                    f"vs {g}" for g in names) + " |")  # fmt: skip
                out.append("|---|---|---|---|" + "---|" * len(names))
                for g in sorted(names, key=lambda g: -gt[g].net):
                    n_strat = sum(1 for r in runs if r.group == g and r.cross)
                    vs = ["-".join(map(str, gt[g].per_opponent[h])) if h in gt[g].per_opponent
                          else "" for h in names]  # fmt: skip
                    out.append(
                        f"| {g} | {gt[g].wdl()} | {gt[g].net:+d}"
                        f" | {gt[g].net / max(1, n_strat):+.1f} | " + " | ".join(vs) + " |"
                    )
        out.append("")

    all_runs = [r for runs in by_game.values() for r in runs]
    groups = sorted({r.group for r in all_runs})
    out.append("## groups\n")
    out.append(
        "| group | game | runs | strategy | check pass | panel score | panel net | budget ok"
        " | deadline ok | mean spend $ | mean wall s |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for g in groups:
        for key in by_game:
            rs = [r for r in by_game[key] if r.group == g]
            if not rs:
                continue
            panels = [r.panel for r in rs if r.panel]
            walls = [r.logs["wall_s"] for r in rs if r.logs["wall_s"] is not None]
            out.append(
                f"| {g} | {key} | {len(rs)} | {_frac([r.strategy is not None for r in rs])}"
                f" | {_frac([_check_ok(r) for r in rs])}"
                f" | {(sum(p.score for p in panels) / len(panels)) if panels else 0:.0%}"
                f" | {sum(p.net for p in panels):+d}"
                f" | {_frac([r.logs['within_budget'] for r in rs])}"
                f" | {_frac([r.logs['within_deadline'] for r in rs])}"
                f" | {sum(r.logs['spend_usd'] for r in rs) / len(rs):.4f}"
                f" | {f'{sum(walls) / len(walls):.0f}' if walls else '?'} |"
            )
    return "\n".join(out)


def _git(*cmd: str) -> str:
    try:
        p = subprocess.run(
            ["git", "-c", "safe.directory=*", *cmd],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def write_log(path: Path, runs: list[Run], args, isolate: bool) -> None:
    commit = _git("rev-parse", "--short", "HEAD") or "unknown"
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in runs:
            row = {
                "ts": ts,
                "eval_commit": commit,
                "eval_dirty": dirty,
                "platform": platform.system(),
                "isolated": isolate,
                "strategy_python": args.python if isolate else None,
                "id": r.id,
                "group": r.group,
                "run_dir": str(r.path.resolve()),
                "task": r.task_name,
                "game": r.game,
                "profile": r.task_name.partition("-")[2] or None,
                "strategy_present": r.strategy is not None,
                "check": r.check,
                "panel": r.panel.to_dict() if r.panel else None,
                "panel_seeds": args.seeds,
                "cross": r.cross.to_dict() if r.cross else None,
                **r.logs,
            }
            f.write(json.dumps(row) + "\n")


# --------------------------------------------------------------------------------- main


def reexec_in_wsl(argv: list[str], distro: str) -> int:
    """Re-run this command in WSL (isolated play), from the same directory."""

    def wsl(p: str) -> str:
        m = re.match(r"^([A-Za-z]):[\\/](.*)$", p)
        return f"/mnt/{m[1].lower()}/{m[2].replace(chr(92), '/')}" if m else p

    def conv(a: str) -> str:
        name, sep, value = a.partition("=")
        if sep and not a.startswith("-") and re.match(r"^\w[\w.-]*$", name):
            return f"{name}={wsl(value)}"  # --group NAME=PATH
        return wsl(a)

    rest = [conv(a) for a in argv if a != "--wsl"]
    script = wsl(str(Path(__file__).resolve()))
    cmd = f"cd {shlex.quote(wsl(os.getcwd()))} && {WSL_PY} {shlex.quote(script)} {shlex.join(rest)}"
    _say(f"re-running in WSL ({distro}): {cmd}")
    # -e: no second shell between wsl.exe and bash (with `--` it re-parses the command line)
    return subprocess.call(["wsl", "-d", distro, "-e", "bash", "-lc", cmd])


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("runs", nargs="*", help="run directories, runs/<task>/ dirs, strategy "
                        "files or globs (group 'runs')")  # fmt: skip
    parser.add_argument(
        "--group",
        action="append",
        default=[],
        metavar="NAME=GLOB",
        help="a named group of runs (repeatable), e.g. v2='runs/beta-B/*'",
    )
    parser.add_argument("--game", help="game key or package for every run (default: task.json)")
    parser.add_argument("--tasks-dir", type=Path, default=REPO / "tasks")
    parser.add_argument("--seeds", type=int, default=5, help="panel seeds per seat (default 5)")
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--check-seeds", type=int, default=1, help="arena check seeds per seat")
    parser.add_argument("--cross", action="store_true", help="tournament between the groups")
    parser.add_argument("--cross-seeds", type=int, help="seeds for --cross (default: --seeds)")
    parser.add_argument("--workers", type=int, default=4, help="matches in parallel (default 4)")
    parser.add_argument("--turn-timeout", type=float, default=1.0)
    parser.add_argument("--draw-tol", type=float, default=0.01, help="draw: |s0-s1| <= tol*(..)")
    parser.add_argument("--skip-check", action="store_true")
    parser.add_argument("--skip-panel", action="store_true")
    parser.add_argument("--in-process", action="store_true", help="never isolate (fast, unsafe)")
    parser.add_argument(
        "--python",
        help="interpreter for isolated strategies (default: the venv's base Python, no "
        "third-party packages, like the evaluation; '' = this interpreter)",
    )
    parser.add_argument("--wsl", action="store_true", help="Windows: re-run inside WSL")
    parser.add_argument("--wsl-distro", default="Ubuntu-24.04")
    parser.add_argument("--log", type=Path, default=REPO / "runs" / "eval_log.jsonl")
    parser.add_argument("--no-log", action="store_true")
    parser.add_argument(
        "--strict", action="store_true", help="exit 1 on a failed check, budget or deadline"
    )
    args = parser.parse_args(argv)

    if args.wsl and not LINUX:
        return reexec_in_wsl(argv, args.wsl_distro)
    isolate = LINUX and not args.in_process
    args.python = (bare_python() if args.python is None else args.python) or None
    if not LINUX and not args.in_process:
        _say(
            "WARNING: isolated play needs Linux; playing IN-PROCESS instead (no act time limit, "
            "a hanging strategy hangs this script). For conformance-grade numbers use --wsl."
        )

    specs = [("runs", p) for p in args.runs]
    for g in args.group:
        name, sep, pattern = g.partition("=")
        if not sep:
            parser.error(f"--group: expected NAME=GLOB, got {g!r}")
        specs.append((name, pattern))
    seen: set[str] = set()
    runs: list[Run] = []
    for group, pattern in specs:
        paths = expand(pattern)
        if not paths:
            _say(f"nothing matches {pattern!r}")
        for path in paths:
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            run = make_run(path, group, args.tasks_dir, args.game)
            ids = {r.id for r in runs}
            base, k = run.id, 2
            while run.id in ids:
                run.id, k = f"{base}#{k}", k + 1
            runs.append(run)
    if not runs:
        parser.error("no runs found")

    by_game: dict[str, list[Run]] = {}
    for r in runs:
        by_game.setdefault(str(r.game), []).append(r)
    _say(f"{len(runs)} run(s): " + ", ".join(f"{k}: {len(v)}" for k, v in by_game.items()))

    if not args.skip_check:
        todo = [r for r in runs if r.strategy is not None]
        seeds = list(range(args.check_seeds))
        _say(f"check: {len(todo)} strateg{'y' if len(todo) == 1 else 'ies'}")
        with ProcessPoolExecutor(max(1, args.workers), mp_context=get_context("spawn")) as pool:
            python = args.python if isolate else None
            futures = [
                pool.submit(
                    check_one, game_ref(r.game), r.target, seeds, args.turn_timeout, isolate, python
                )
                for r in todo
            ]
            for r, fut in zip(todo, futures, strict=True):
                try:
                    r.check = fut.result()
                except Exception:  # e.g. an in-process strategy killed the pool's process
                    r.check = {"passed": False, "matches": 0, "forfeits": 0,
                               "reason": "evaluator error",
                               "error": traceback.format_exc()[-2000:]}  # fmt: skip

    infos: dict[str, dict] = {}
    for key, game_runs in by_game.items():
        try:
            infos[key] = evaluate_game(key, game_runs, args, isolate)
        except Exception:
            _say(f"{key}: evaluation failed:\n{traceback.format_exc()}")

    print(markdown(by_game, infos, args, isolate))
    if not args.no_log:
        write_log(args.log, runs, args, isolate)
        _say(f"appended {len(runs)} line(s) to {args.log}")
    if args.strict:
        bad = [
            r
            for r in runs
            if r.strategy is None
            or (r.check is not None and not r.check["passed"])
            or _forfeits(r)  # a forfeit on any seed is a conformance failure too
            or r.logs["within_budget"] is False
            or r.logs["within_deadline"] is False
        ]
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
