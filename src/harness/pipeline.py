"""One run: spec -> candidate strategies + a verified simulator -> self-play -> champion.

Everything game-specific comes from the spec, through the LLM or harness.specdoc; this module
only schedules, verifies, selects and spends. The contracts of the modules it uses are in
docs/ARCHITECTURE.md.

The file at `strategy_path` is valid at every moment: it starts as the guard with no core
(plays None), and is only ever replaced atomically by a guarded candidate that passed its
checks.
"""

import ast
import json
import os
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from dataclasses import dataclass, field
from pathlib import Path

from harness import guard, sandbox
from harness.budget import Budget, estimate_tokens
from harness.clock import Clock
from harness.llm import LLM, extract_code
from harness.specdoc import parse_spec
from harness.tournament import Tournament

PROMPTS = Path(__file__).with_name("prompts")

# Per-act time the strategies may use for search: low in self-play (many matches on 2 CPUs),
# higher in the delivered file (1 s limit per act; the guard cuts the core at 0.6 s).
SELFPLAY_TIME_BUDGET_S = 0.02
FINAL_TIME_BUDGET_S = 0.25
# The delivered file's hard limit per act (arena allows 1.0 s including the pipe round trip;
# a loaded tournament machine needs headroom).
FINAL_ACT_LIMIT_S = 0.70

# A challenger replaces the champion only if its mean per-opponent score is higher by
# PROMOTE_MARGIN and it does not lose the head-to-head, or if it is higher by CLEAR_MARGIN.
PROMOTE_MARGIN = 0.03
CLEAR_MARGIN = 0.12
MAX_POLISH_SEEDS = 24

# A cheap draft of what a typical competitor writes, as a sparring partner in self-play:
# the real field is mostly other LLM-written strategies, many of them naive.
SPARRING_PERSONA = "sparring_naive"

# Self-play files are internal: their class is always this name (the tournament and the
# sandbox look it up), whatever task.json asks for; only the delivered file uses that.
SELFPLAY_CLASS = "Strategy"


@dataclass
class Profile:
    """How a run spends its money and time. Derived from task.json, never from a task name."""

    name: str
    cheap: str
    strong: str
    n_candidates: int  # first-batch strategies
    cand_models: list[str]  # model per first-batch candidate (cycled)
    cand_effort: str
    cand_max_out: int
    sim_model: str | None  # None: no simulator, no self-play
    sim_effort: str
    sim_max_out: int
    analysis_model: str | None
    analysis_effort: str
    repair_model: str
    repair_effort: str
    repair_max_out: int
    improve_model: str | None
    improve_effort: str
    improve_max_out: int
    improve_parallel: int
    call_timeout_s: float
    seeds: int  # self-play seeds per pairing and seat
    max_sim_repairs: int
    max_strategy_repairs: int
    min_improve_s: float  # time an improvement round needs (call + checks + matches)
    # A cheap naive draft in self-play. Off: experiment 1 (24 runs, 3 games) showed no gain on
    # beta/gamma and worse selection on epsilon (full lost 11-1-36 to the arm without it).
    sparring: bool = False
    robust_rank: bool = True  # per-opponent ranking + incumbent protection (else raw standings)
    polish: bool = True  # time left after the last call: more self-play seeds, re-rank
    ablated: tuple[str, ...] = ()


# Development only: HARNESS_ABLATE=sparring,improve,... switches features off, so that every
# feature is kept on measured evidence (same tasks, with vs without, cross-tournament). The
# evaluation sets no such variable, so it always runs the full harness.
ABLATIONS = {
    "sparring": "no sparring draft in self-play",
    "improve": "no improvement rounds",
    "selfplay": "no simulator: no self-play, no improvement (first valid draft wins)",
    "repair": "no repair calls for strategies or simulators",
    "analysis": "no strategic analysis call",
    "robust_rank": "rank by raw standings, no incumbent protection",
    "oneshot": "baseline: one draft, no simulator, no repair (guard still on)",
    "polish": "no extra self-play seeds in the time left after the last LLM call",
    "improve_low": "variant: improvement calls at low effort (cheaper, more rounds)",
}


def apply_ablations(p: Profile, flags: str | None) -> Profile:
    names = tuple(sorted({f.strip() for f in (flags or "").split(",") if f.strip()}))
    for name in names:
        if name not in ABLATIONS:
            continue
        if name in ("sparring", "oneshot"):
            p.sparring = False
        if name in ("improve", "selfplay", "oneshot"):
            p.improve_model = None
        if name in ("selfplay", "oneshot"):
            p.sim_model = None
        if name in ("repair", "oneshot"):
            p.max_strategy_repairs = p.max_sim_repairs = 0
        if name in ("analysis", "oneshot"):
            p.analysis_model = None
        if name == "polish":
            p.polish = False
        if name == "improve_low":
            p.improve_effort = "low"
        if name == "robust_rank":
            p.robust_rank = False
        if name == "oneshot":
            p.n_candidates = 1
    p.ablated = tuple(n for n in names if n in ABLATIONS)
    return p


def make_profile(task: dict, llm: LLM, clock: Clock) -> Profile:
    """Calibrated on the dev games (one-shot strategy from the spec, Oct 24):

        model        effort   secs   output tok   USD     beta wins vs field
        cheapest     low        19        1.6k    0.001   weak
        cheapest     medium    104        9.5k    0.006   weak
        cheapest     high     >456     >40k cap   0.021   no answer: never use high here
        mid          low        49        2.5k    0.041   strong
        mid          medium    145        6.7k    0.083   strong (= low)
        priciest     low        29        2.1k    0.182   strong (= mid)
        priciest     medium    149        7.2k    0.439   strong (= mid)

    So: low effort for first drafts (fast, as good), more effort only where the input is
    richer (improving a champion from tournament evidence). Tiers come from the budget and
    deadline in task.json, never from a task name.
    """
    budget = float(task.get("budget_usd") or 1.0)
    deadline = clock.deadline_s
    cheap, strong = llm.cheapest(), llm.strongest()
    if budget < 0.1 or deadline < 300:  # a few cents, a few minutes: fast cheap drafts
        return Profile(
            name="tiny", cheap=cheap, strong=strong, n_candidates=3,
            cand_models=[strong], cand_effort="low", cand_max_out=6000,
            sim_model=cheap, sim_effort="low", sim_max_out=8000,
            analysis_model=None, analysis_effort="low",
            repair_model=cheap, repair_effort="low", repair_max_out=6000,
            improve_model=strong, improve_effort="low", improve_max_out=6000,
            improve_parallel=2, call_timeout_s=min(90.0, deadline * 0.5), seeds=3,
            max_sim_repairs=1, max_strategy_repairs=1, min_improve_s=50.0,
        )  # fmt: skip
    if budget < 1.5:  # cents per call matter: the strong model at low effort, more drafts
        return Profile(
            name="mid", cheap=cheap, strong=strong, n_candidates=3,
            cand_models=[strong], cand_effort="low", cand_max_out=6000,
            sim_model=strong, sim_effort="low", sim_max_out=9000,
            analysis_model=None, analysis_effort="low",
            repair_model=strong, repair_effort="low", repair_max_out=8000,
            improve_model=strong, improve_effort="medium", improve_max_out=10000,
            improve_parallel=1, call_timeout_s=200.0, seeds=4,
            max_sim_repairs=2, max_strategy_repairs=1, min_improve_s=130.0,
        )  # fmt: skip
    return Profile(  # dollars per run: time binds before money
        name="large", cheap=cheap, strong=strong, n_candidates=4,
        cand_models=[strong, cheap, cheap, strong], cand_effort="low", cand_max_out=16000,
        sim_model=cheap, sim_effort="medium", sim_max_out=24000,
        analysis_model=strong, analysis_effort="medium",
        repair_model=cheap, repair_effort="medium", repair_max_out=20000,
        improve_model=strong, improve_effort="medium", improve_max_out=24000,
        improve_parallel=3, call_timeout_s=360.0, seeds=6,
        max_sim_repairs=3, max_strategy_repairs=2, min_improve_s=220.0,
    )  # fmt: skip


@dataclass
class Candidate:
    id: str
    persona: str
    model: str
    source: str  # the LLM's core, a stand-alone file
    raw_path: Path
    guarded_path: Path  # the core under the guard, as it plays in self-play
    valid: bool = False
    sim_checked: bool = False  # played on the simulator during verification
    problems: list[str] = field(default_factory=list)
    parent: str | None = None


class Run:
    def __init__(
        self, *, spec: str, task: dict, strategy_path: Path, work_dir: Path, log_dir: Path,
        container: bool = False,
    ) -> None:  # fmt: skip
        self.spec = spec
        self.task = task
        self.strategy_path = strategy_path
        self.work = work_dir
        self.logs = log_dir
        self.clock = Clock(task.get("deadline_s"), container=container)
        self.budget = Budget(task.get("budget_usd"), task["prices"])
        self.llm = LLM(task, self.budget, self.clock, log_dir / "transcript.jsonl")
        self.cls = task.get("strategy_class") or "Strategy"
        self.info = parse_spec(spec)
        self.profile = apply_ablations(
            make_profile(task, self.llm, self.clock), os.environ.get("HARNESS_ABLATE")
        )
        self.lock = threading.RLock()
        self.cands: dict[str, Candidate] = {}
        self.champion: Candidate | None = None
        self.fallback: Candidate | None = None  # the safe persona, if it passed
        self.sim_path: Path | None = None
        self.tournament: Tournament | None = None
        self.analysis = "(none)"
        self.pool = ThreadPoolExecutor(max_workers=8)
        self._ids = 0
        self._events = (log_dir / "events.jsonl").open("a", encoding="utf-8")
        (log_dir / "candidates").mkdir(parents=True, exist_ok=True)
        (work_dir / "cands").mkdir(parents=True, exist_ok=True)

        self.system = _read("system.md") + "\n\n" + _read("playbook.md")
        self.spec_message = {"role": "user", "content": "GAME SPECIFICATION\n\n" + spec}
        try:
            self.personas = json.loads(_read("personas.json"))
        except Exception:
            self.personas = [{"name": "default", "persona": "Write the strongest strategy."}]

    # ---------------------------------------------------------------- the run

    def run(self) -> None:
        """Never raises: whatever fails, the file at strategy_path stays valid."""
        self.write_fallback()
        p = self.profile
        self.event(
            "start", profile=p.__dict__, examples=len(self.info.example_observations),
            obs_keys=self.info.obs_keys, deadline_s=self.clock.deadline_s,
            budget_usd=self.task.get("budget_usd"),
        )  # fmt: skip
        try:
            self.first_batch()
            if self.sim_path is not None:
                self.select()
                self.improve()
                self.polish()
        except Exception:
            self.event("error", where="run", traceback=traceback.format_exc())
        self.finish()

    def write_fallback(self) -> None:
        guard.write_atomic(self.strategy_path, guard.assemble(None, strategy_class=self.cls))
        self.event("fallback_written")

    def first_batch(self) -> None:
        """The simulator and the first candidates, in parallel; each verifies itself.

        Submission order is priority order: at most 4 calls are in flight, the rest queue.
        The analysis only feeds the improvement rounds, so it never delays a candidate.
        """
        p = self.profile
        futures = []
        if p.sim_model:
            futures.append(self.pool.submit(self._guarded, self.build_simulator))
        main = [x for x in self.personas if x.get("name") != SPARRING_PERSONA]
        for i, persona in enumerate(main[: p.n_candidates]):
            model = p.cand_models[i % len(p.cand_models)]
            futures.append(self.pool.submit(self._guarded, self.new_candidate, persona, model))
        sparring = [x for x in self.personas if x.get("name") == SPARRING_PERSONA]
        if sparring and p.sim_model and p.sparring:  # only useful when there is self-play
            futures.append(
                self.pool.submit(self._guarded, self.new_candidate, sparring[0], p.cheap, "low")
            )
        if p.analysis_model:
            futures.append(self.pool.submit(self._guarded, self.analyse))
        # Whatever misses the cut is not waited for: the run has to finish.
        self._wait(futures, reserve_s=self.profile.min_improve_s * 0.25 + 15)

    def select(self) -> None:
        """Self-play among the valid candidates; promote the best."""
        if self.sim_path is None:
            return
        # Candidates verified before the simulator existed: play them on it now, repair once.
        late = [c for c in self.valid_candidates() if not c.sim_checked]
        fixes = []
        for c in late:
            c.sim_checked = True
            problems = self._play_problems(c, self.sim_path)
            if problems:
                c.valid = False
                self.event("late_problems", id=c.id, problems=problems[:4])
                if self.clock.remaining() > 90 and c.persona != SPARRING_PERSONA:
                    fixes.append(self.pool.submit(self._guarded, self.repair, c, problems))
        self._wait(fixes, reserve_s=40.0)
        valid = self.valid_candidates()
        if not valid:
            return
        self.tournament = Tournament(
            self.sim_path, self.work / "tournament", seeds=list(range(self.profile.seeds)),
            workers=2,
        )  # fmt: skip
        self.tournament.add("idle", None)
        for c in valid:
            self.tournament.add(c.id, c.guarded_path)
        self.play(share=0.5)
        self.promote_best()

    def improve(self) -> None:
        """Challengers from the strong model, against the pool, while time and money last."""
        p = self.profile
        if p.improve_model is None or self.tournament is None:
            return
        round_no = empty_rounds = 0
        while (
            self.clock.remaining() > p.min_improve_s
            and self.champion is not None
            and empty_rounds < 3
        ):
            round_no += 1
            focus = self.focus_text()
            futures = [
                self.pool.submit(self._guarded, self.challenger, k, focus)
                for k in range(p.improve_parallel)
            ]
            self._wait(futures, reserve_s=p.min_improve_s * 0.4)
            added = [c for f in futures if f.done() and (c := f.result()) is not None]
            self.event("improve_round", round=round_no, added=[c.id for c in added])
            if not added:
                empty_rounds += 1
                if not self.budget.can_afford(p.cheap, 20000, p.improve_max_out):
                    break
                continue
            empty_rounds = 0
            for c in added:
                self.tournament.add(c.id, c.guarded_path)
            self.play(share=0.6)
            self.promote_best()

    def polish(self) -> None:
        """Time left but no money (or no more rounds): more seeds, a less noisy ranking."""
        t = self.tournament
        if t is None or not self.profile.polish:
            return
        added = 0
        while self.clock.remaining() > 30 and added < MAX_POLISH_SEEDS:
            if not t.pending():
                t.seeds.append(max(t.seeds, default=-1) + 1)
                added += 1
            before = len(t.results)
            self.play(share=0.8)
            if len(t.results) == before:
                break  # nothing fit into the time left
        if added:
            self.event("polished", seeds=len(t.seeds), matches=len(t.results))
            self.promote_best()

    def finish(self) -> None:
        """Last check of the delivered file; log the end state."""
        try:
            ok = self._final_probe(self.strategy_path)
            if not ok and self.champion is not None:
                self.write_fallback()  # the promoted file failed its final probe: be safe
            self.event("final_probe", ok=ok)
        except Exception:
            self.event("error", where="finish", traceback=traceback.format_exc())
        self.event(
            "end", champion=self.champion.id if self.champion else None,
            spent=self.budget.spent, elapsed=self.clock.elapsed(),
        )  # fmt: skip
        if self.tournament is not None:
            try:
                (self.logs / "tournament.txt").write_text(self.tournament.report(), "utf-8")
            except Exception:
                pass
        self._events.flush()

    # ------------------------------------------------------------- the stages

    def analyse(self) -> None:
        p = self.profile
        r = self.ask("analysis", p.analysis_model, _read("analysis.md"), p.analysis_effort, 12000)
        if r.ok and r.text.strip():
            self.analysis = r.text.strip()
            self._save_log("analysis.md", self.analysis)

    def repair(self, c: Candidate, problems: list[str]) -> Candidate | None:
        """One repair of a candidate that failed on the simulator."""
        p = self.profile
        fix = _read("repair_strategy.md").format(code=c.source, problems=_bullets(problems))
        r = self.ask(f"fix_{c.id}", p.repair_model, fix, p.repair_effort, p.repair_max_out)
        code = extract_code(r.text) if r.ok else None
        if code is None:
            return None
        return self.verify(code, c.persona, p.repair_model, 0)

    def build_simulator(self) -> None:
        p = self.profile
        prompt = _read("simulator.md").format(
            obs_keys=", ".join(self.info.obs_keys) if self.info.obs_keys else "unknown",
            examples_note=f"The harness extracted {len(self.info.example_observations)} "
            "worked example observation(s) from the specification.",
        )
        models = [p.sim_model] + ([p.strong] if p.strong != p.sim_model else [])
        for attempt, model in enumerate(models):
            r = self.ask(f"sim{attempt}", model, prompt, p.sim_effort, p.sim_max_out)
            code = extract_code(r.text) if r.ok else None
            for repair in range(p.max_sim_repairs + 1):
                if code is None:
                    break
                path = self.work / f"sim_{attempt}_{repair}.py"
                path.write_text(code, "utf-8")
                self._save_log(f"sim_{attempt}_{repair}.py", code)
                problems = self.check_sim(path)
                self.event("sim_checked", attempt=attempt, repair=repair, problems=problems[:8])
                if not problems:
                    with self.lock:
                        self.sim_path = path
                    return
                if repair == p.max_sim_repairs or self.clock.remaining() < 90:
                    break
                fix = _read("repair_sim.md").format(code=code, problems=_bullets(problems))
                r = self.ask(
                    f"sim{attempt}_fix{repair}", p.repair_model, fix, p.repair_effort,
                    p.sim_max_out,
                )  # fmt: skip
                code = extract_code(r.text) if r.ok else None
            if self.clock.remaining() < 150:
                return

    def check_sim(self, path: Path) -> list[str]:
        problems = []
        ok, report = sandbox.run_selftest(path)
        if not ok:
            problems.append(f"selftest() failed:\n{report[:3000]}")
        ok, generic = sandbox.check_simulator(path, self.info)
        if not ok:
            problems += generic
        if not problems:  # the rules engine works: does it accept a real strategy's actions?
            problems += self._sim_vs_candidates(path)
        return problems

    def new_candidate(
        self, persona: dict, model: str, effort: str | None = None
    ) -> Candidate | None:
        p = self.profile
        prompt = _read("strategy.md").format(
            persona=persona.get("persona", ""), time_budget=FINAL_TIME_BUDGET_S,
            analysis=self.analysis,
        )  # fmt: skip
        label = f"cand_{persona.get('name')}"
        r = self.ask(label, model, prompt, effort or p.cand_effort, p.cand_max_out)
        code = extract_code(r.text) if r.ok else None
        if code is None:
            return None
        return self.verify(code, persona.get("name", "?"), model, p.max_strategy_repairs)

    def challenger(self, k: int, focus: str) -> Candidate | None:
        p = self.profile
        champ = self.champion
        if champ is None or self.tournament is None:
            return None
        prompt = _read("improve.md").format(
            champion_code=champ.source, report=self.tournament.report(focus=champ.id),
            analysis=self.analysis, focus=focus if k == 0 else _ALT_FOCUS[k % len(_ALT_FOCUS)],
        )  # fmt: skip
        # The strongest model leads; parallel challengers use the cheaper one (diversity, cost).
        model = p.improve_model if k == 0 else p.cheap
        r = self.ask(f"improve_{champ.id}_{k}", model, prompt, p.improve_effort, p.improve_max_out)
        code = extract_code(r.text) if r.ok else None
        if code is None:
            return None
        c = self.verify(code, f"improve{k}", model, p.max_strategy_repairs)
        if c is not None:
            c.parent = champ.id
        return c if c is not None and c.valid else None

    def verify(self, code: str, persona: str, model: str, repairs: int) -> Candidate | None:
        """Probe a core (and play it on the simulator if there is one); repair on failure."""
        if persona == SPARRING_PERSONA:
            repairs = 0  # a sparring partner is used as written, or not at all
        for attempt in range(repairs + 1):
            c = self._candidate(code, persona, model)
            c.problems = self.problems_of(c)
            self.event("candidate", id=c.id, persona=persona, model=model, attempt=attempt,
                       problems=c.problems[:6])  # fmt: skip
            if not c.problems:
                c.valid = True
                self.accept(c)
                return c
            if attempt == repairs or self.clock.remaining() < 60:
                return c
            fix = _read("repair_strategy.md").format(code=code, problems=_bullets(c.problems))
            p = self.profile
            r = self.ask(f"fix_{c.id}", p.repair_model, fix, p.repair_effort, p.repair_max_out)
            code = (extract_code(r.text) if r.ok else None) or code
            if not r.ok:
                return c
        return None

    def problems_of(self, c: Candidate) -> list[str]:
        problems = []
        obs = self.info.example_observations
        if obs:
            res = sandbox.probe(c.raw_path, obs, strategy_class=_core_class(c.source), raw=True)
            if res.load_error:
                return [f"The file fails to load or __init__ raises:\n{res.load_error[:2500]}"]
            for call in res.calls:
                if call.get("error"):
                    problems.append(
                        f"act() raised on an example observation from the specification "
                        f"(player_id={call.get('player_id')}):\n{call['error'][:2500]}"
                    )
                    break
            if any(not call.get("literal_ok", True) for call in res.calls):
                problems.append("act() returned something that is not a plain Python literal.")
            if res.max_secs > 0.5:
                problems.append(f"act() took {res.max_secs:.2f} s on an example observation.")
        else:  # no examples: at least it has to load
            res = sandbox.probe(c.raw_path, [], strategy_class=_core_class(c.source), raw=True)
            if res.load_error:
                return [f"The file fails to load or __init__ raises:\n{res.load_error[:2500]}"]
        sim = self.sim_path
        if not problems and sim is not None:
            c.sim_checked = True
            problems += self._play_problems(c, sim)
        return problems

    def _play_problems(self, c: Candidate, sim: Path) -> list[str]:
        """One match per seat against idle on the simulator: errors, bad actions, losses."""
        results = sandbox.play_matches(
            sim, {c.id: c.guarded_path}, [(c.id, "idle", 0), ("idle", c.id, 0)],
            strategy_class=SELFPLAY_CLASS, timeout_s=120,
        )  # fmt: skip
        problems = []
        for r in results:
            if r.sim_error:
                continue  # the simulator's fault, not the candidate's
            seat = r.players.index(c.id)
            st = r.player_stats[seat] or {}
            gs = st.get("guard_stats") or {}
            turns = max(1, r.turns)
            if gs.get("first_core_traceback"):
                problems.append(f"As seat {seat}, act() raised during a match:\n"
                                f"{gs['first_core_traceback'][:2500]}")  # fmt: skip
            if gs.get("core_timeouts"):
                problems.append(f"As seat {seat}, act() exceeded its time limit "
                                f"{gs['core_timeouts']} time(s).")  # fmt: skip
            if st.get("malformed", 0) > turns // 10:
                problems.append(
                    f"As seat {seat}, {st['malformed']} of {turns} actions were MALFORMED (the "
                    "whole action was ignored by the game). Check the exact action types the "
                    "specification requires."
                )
            if st.get("illegal", 0) > turns // 2:
                problems.append(f"As seat {seat}, {st['illegal']} illegal moves in {turns} turns "
                                "(each one was skipped by the game).")  # fmt: skip
            # Losing to idle is fine for the sparring draft: naive real opponents do that too.
            lost = r.scores is not None and r.winner is not None and r.winner != seat
            if lost and c.persona != SPARRING_PERSONA:
                problems.append(
                    f"As seat {seat} it LOST against an opponent that never acts (scores "
                    f"{r.scores}). It must at least beat doing nothing."
                )
        return problems[:6]

    def _sim_vs_candidates(self, sim: Path) -> list[str]:
        """A simulator that crashes on real strategies' actions is not usable."""
        cands = self.valid_candidates()[:2]
        if not cands:
            return []
        entries = {c.id: c.guarded_path for c in cands}
        pairings = [(c.id, "idle", 1) for c in cands]
        if len(cands) == 2:
            pairings.append((cands[0].id, cands[1].id, 1))
        problems = []
        for r in sandbox.play_matches(sim, entries, pairings, strategy_class=SELFPLAY_CLASS,
                                      timeout_s=120):  # fmt: skip
            if r.sim_error and r.sim_error != "timeout":
                problems.append(f"The simulator raised while playing real strategies:\n"
                                f"{r.sim_error[:2500]}")  # fmt: skip
                break
        return problems

    # -------------------------------------------------------------- selection

    def accept(self, c: Candidate) -> None:
        """A valid candidate: the provisional champion if there is none yet."""
        with self.lock:
            if c.persona == self._safe_name() and self.fallback is None:
                self.fallback = c
            if self.tournament is not None:
                return  # from here on the tournament decides
            if self.champion is None or self._rank(c) < self._rank(self.champion):
                self._promote(c, reason="provisional")

    def _rank(self, c: Candidate) -> int:
        """Provisional preference before any self-play: main personas, then safe, sparring."""
        if c.persona == SPARRING_PERSONA:
            return 2
        return 1 if c.persona == self._safe_name() else 0

    def ranking(self) -> list[tuple[float, float, float, str]]:
        """(mean, worst, margin, id) per candidate in the tournament, best first.

        Against overfitting to the pool: every opponent weighs the same (a strategy cannot
        climb by farming one weak opponent many times), the worst pairing breaks ties (a
        robust strategy beats a brilliant-but-exploitable one), score margin comes last.
        """
        t = self.tournament
        if t is None:
            return []
        standings = t.standings()
        margin = {s.id: s.mean_margin for s in standings}
        ids = [s.id for s in standings]
        rows = []
        for cid in ids:
            if cid not in self.cands:
                continue  # idle and other non-candidates are opponents only
            per_opponent = []
            for other in ids:
                if other == cid:
                    continue
                w, d, lo = t.head_to_head(cid, other)
                if w + d + lo:
                    per_opponent.append((w + 0.5 * d) / (w + d + lo))
            if per_opponent:
                mean = sum(per_opponent) / len(per_opponent)
                rows.append((mean, min(per_opponent), margin.get(cid, 0.0), cid))
        rows.sort(reverse=True)
        return rows

    def promote_best(self) -> None:
        if not self.profile.robust_rank and self.tournament is not None:  # ablation baseline
            top = [s for s in self.tournament.standings() if s.id in self.cands]
            if top and (self.champion is None or top[0].id != self.champion.id):
                self._promote(self.cands[top[0].id], reason="tournament (raw standings)")
            return
        rows = self.ranking()
        if not rows:
            return
        self.event("ranking", rows=[{"id": i, "mean": round(m, 3), "worst": round(w, 3),
                                     "margin": round(g, 1)} for m, w, g, i in rows])  # fmt: skip
        best = rows[0][3]
        champ = self.champion.id if self.champion is not None else None
        if best == champ:
            return
        by_id = {i: (m, w) for m, w, _, i in rows}
        if champ in by_id:
            # Incumbent protection against selection noise: a challenger must be better by
            # a margin and must not lose the head-to-head, unless it is clearly better.
            gain = by_id[best][0] - by_id[champ][0]
            w, _, lo = self.tournament.head_to_head(best, champ)
            if gain < PROMOTE_MARGIN or (lo > w and gain < CLEAR_MARGIN):
                self.event("kept_champion", champion=champ, challenger=best, gain=round(gain, 3),
                           h2h=[w, lo])  # fmt: skip
                return
        self._promote(self.cands[best], reason="tournament")

    def _promote(self, c: Candidate, reason: str) -> None:
        fb = self.fallback if self.fallback is not None and self.fallback.id != c.id else None
        text = guard.assemble(
            c.source, strategy_class=self.cls, core_class=_core_class(c.source),
            fallback_src=fb.source if fb else None,
            fallback_class=_core_class(fb.source) if fb else "Strategy",
            time_budget_s=FINAL_TIME_BUDGET_S, act_limit_s=FINAL_ACT_LIMIT_S,
            header=f"candidate {c.id} ({c.model}, {c.persona})",
        )  # fmt: skip
        staged = self.work / "staged_strategy.py"
        staged.write_text(text, "utf-8")
        if not self._final_probe(staged):
            self.event("promote_rejected", id=c.id)
            return
        guard.write_atomic(self.strategy_path, text)
        self.champion = c
        self.event("promoted", id=c.id, reason=reason, fallback=fb.id if fb else None)

    def _final_probe(self, path: Path) -> bool:
        obs = self.info.example_observations
        res = sandbox.probe(path, obs, strategy_class=self.cls, raw=False)
        if res.load_error or not res.ok or res.max_secs > 0.9:
            return False
        if self.sim_path is not None:
            for r in sandbox.play_matches(
                self.sim_path, {"final": path}, [("final", "idle", 7), ("idle", "final", 7)],
                strategy_class=self.cls, timeout_s=120,
            ):  # fmt: skip
                if r.sim_error:
                    continue
                st = r.player_stats[r.players.index("final")] or {}
                if st.get("forfeit_turn") is not None or st.get("max_act_s", 0) > 0.95:
                    return False
        return True

    def play(self, share: float) -> None:
        """Run the tournament for a share of the remaining time (leaving the finish margin)."""
        if self.tournament is None:
            return
        budget_s = max(10.0, (self.clock.remaining() - 20) * share)
        try:
            self.tournament.run(budget_s=budget_s)
        except Exception:
            self.event("error", where="tournament", traceback=traceback.format_exc())

    def focus_text(self) -> str:
        if self.tournament is None or self.champion is None:
            return "Make it stronger."
        me = self.champion.id
        worst = []
        for s in self.tournament.standings():
            if s.id == me:
                continue
            w, d, lo = self.tournament.head_to_head(me, s.id)
            if lo or d:
                worst.append((lo - w, s.id, w, d, lo))
        if not worst:
            return (
                "It wins every pairing so far. Make it stronger against strong opponents: "
                "anticipate the opponent's best response and contest its best nodes."
            )
        worst.sort(reverse=True)
        parts = [f"{oid}: {w} wins, {d} draws, {lo} losses" for _, oid, w, d, lo in worst[:3]]
        return "Its worst pairings: " + "; ".join(parts) + ". Find out why and fix it."

    def valid_candidates(self) -> list[Candidate]:
        with self.lock:
            return [c for c in self.cands.values() if c.valid]

    def _safe_name(self) -> str:
        for p in self.personas:
            if "safe" in p.get("name", "").lower():
                return p["name"]
        return self.personas[-1].get("name", "") if self.personas else ""

    # ---------------------------------------------------------------- helpers

    def ask(self, label: str, model: str, stage_prompt: str, effort: str, max_out: int):
        # Worst-case reservations of calls in flight can block a call that the money would
        # cover once they settle: wait for them rather than skip.
        need = estimate_tokens(self.system + self.spec + stage_prompt)
        while (
            not self.budget.can_afford(model, need, max_out)
            and self.budget.in_flight() > 0
            and self.clock.remaining() > 60
        ):
            time.sleep(1.0)
        timeout = min(self.profile.call_timeout_s, self.clock.remaining() - 10)
        r = self.llm.call(
            label=label, model=model, instructions=self.system,
            input=[self.spec_message, {"role": "user", "content": stage_prompt}],
            effort=effort, max_output_tokens=max_out, timeout_s=timeout,
        )  # fmt: skip
        self.event("llm", label=label, model=model, status=r.status, secs=round(r.secs, 1),
                   cost=round(r.cost, 5), spent=round(self.budget.spent, 5))  # fmt: skip
        return r

    def _candidate(self, code: str, persona: str, model: str) -> Candidate:
        with self.lock:
            self._ids += 1
            cid = f"c{self._ids}_{_slug(persona)}"
        raw = self.work / "cands" / f"{cid}_raw.py"
        raw.write_text(code, "utf-8")
        guarded = self.work / "cands" / f"{cid}.py"
        guarded.write_text(
            guard.assemble(code, strategy_class=SELFPLAY_CLASS, core_class=_core_class(code),
                           time_budget_s=SELFPLAY_TIME_BUDGET_S),
            "utf-8",
        )  # fmt: skip
        self._save_log(f"{cid}.py", code)
        c = Candidate(cid, persona, model, code, raw, guarded)
        with self.lock:
            self.cands[cid] = c
        return c

    def _guarded(self, fn, *args):
        try:
            return fn(*args)
        except Exception:
            where = getattr(fn, "__name__", "?")
            self.event("error", where=where, traceback=traceback.format_exc())
            return None

    def _wait(self, futures: list[Future], reserve_s: float) -> None:
        """Wait for futures, but never past the point where `reserve_s` is left."""
        pending = [f for f in futures if f is not None]
        while pending:
            left = self.clock.remaining() - reserve_s
            if left <= 0:
                self.event("wait_cut", pending=len(pending))
                return
            _, not_done = wait_futures(pending, timeout=min(left, 5.0))
            pending = list(not_done)

    def _save_log(self, name: str, text: str) -> None:
        try:
            (self.logs / "candidates" / name).write_text(text, "utf-8")
        except Exception:
            pass

    def event(self, kind: str, **data) -> None:
        record = {"t": round(self.clock.elapsed(), 2), "event": kind, **data}
        line = json.dumps(record, default=str)
        with self.lock:
            self._events.write(line + "\n")
            self._events.flush()
        print(f"[{record['t']:7.1f}s ${self.budget.spent:.4f}] {kind} "
              f"{json.dumps(data, default=str)[:200]}", flush=True)  # fmt: skip


_ALT_FOCUS = [
    "",
    "Play the opponent's side too: predict where a strong opponent will move next turn and "
    "choose the allocation that maximises your margin against that prediction.",
    "Remove waste: units that cost more than they earn, oscillating moves, idle units that "
    "could earn, units arriving after the value is gone. Keep everything that works.",
]


def _read(name: str) -> str:
    return (PROMPTS / name).read_text("utf-8")


def _bullets(problems: list[str]) -> str:
    return "\n\n".join(f"- {p}" for p in problems)


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text.lower())[:24] or "x"


def _core_class(source: str) -> str:
    """The class the LLM's core defines: 'Strategy' if present, else the first with act()."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "Strategy"
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    names = [n.name for n in classes]
    if "Strategy" in names:
        return "Strategy"
    for n in classes:
        if any(isinstance(b, ast.FunctionDef) and b.name == "act" for b in n.body):
            return n.name
    return "Strategy"
