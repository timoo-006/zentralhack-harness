"""Runs untrusted code (strategies, simulators) for harness.sandbox, in its own process.

Standard library only, and it must never import harness or anything third-party: it runs as

    python -s -S -P runner_child.py job.json       (-I minus -E, so PYTHONHASHSEED=0 holds)

so that a strategy importing a third-party package fails here exactly as it would in the
tournament. It prints exactly one JSON object on the original stdout; whatever the untrusted
code prints goes to stderr (capped per strategy and match, like arena's 16 MB file limit).

The job file (written by harness.sandbox; the schema is internal to the two modules):

    {"mode": "probe" | "matches" | "selftest" | "checksim",
     "act_timeout_s": float, "init_timeout_s": float, "memory_mb": int | null,
     "strategy_class": str, "sim_class": str,
     # probe
     "strategy_path": str, "observations": [repr(obs), ...], "player_ids": [int, ...],
     "raw": bool,
     # matches
     "sim_path": str, "entries": {id: path | null}, "pairings": [[id0, id1, seed], ...],
     "max_turns": int, "draw_tolerance": float, "progress_path": str | null,
     # selftest: "sim_path"
     # checksim: "sim_path", "seeds": [int], "obs_keys": [str] | null,
     #           "example_observations": [repr(obs), ...], "max_turns": int}

Observations travel as repr() strings, so tuples and lists stay distinct.
"""

import ast
import gc
import importlib.util
import json
import math
import os
import random
import signal
import sys
import time
import traceback
from pathlib import Path

MAX_REPLY_BYTES = 1_000_000  # arena's limit on repr(action)
MAX_OUTPUT_BYTES = 16 * 2**20  # arena: RLIMIT_FSIZE on the worker's stderr
SIM_CALL_LIMIT_S = 30.0  # a single simulator call (construct, observe, step, ...)
TB_CHARS = 3000


class ChildTimeout(BaseException):
    """Raised by this process's timer when untrusted code overruns its limit."""


class CappedOutput:
    """sys.stdout/sys.stderr replacement: forwards a little to stderr, fails past the cap.

    The cap is counted per `key` (strategy seat and match), as arena counts it per worker.
    Past the cap writes raise OSError, as the worker would be killed by SIGXFSZ there.
    """

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, stream, forward_bytes: int = 32_000) -> None:
        self._stream = stream
        self._forward_left = forward_bytes
        self.key = None
        self._counts: dict = {}

    def write(self, s) -> int:
        n = len(s)
        count = self._counts.get(self.key, 0) + n
        self._counts[self.key] = count
        if count > MAX_OUTPUT_BYTES:
            raise OSError(27, "File too large (output over 16 MB)")
        if self._forward_left > 0:
            try:
                text = s if isinstance(s, str) else repr(s)
                self._stream.write(text[: self._forward_left])
                self._forward_left -= n
            except Exception:
                self._forward_left = 0
        return n

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return False


OUTPUT = CappedOutput(sys.__stderr__)


_ARMED = [False]
_EXEMPT: set = set()  # code objects in which an alarm never raises (our own bookkeeping)


def _on_alarm(signum, frame):
    if _ARMED[0] and (frame is None or frame.f_code not in _EXEMPT):
        raise ChildTimeout()


def _can_time() -> bool:
    return hasattr(signal, "setitimer") and hasattr(signal, "SIGALRM")


def timed_call(fn, args: tuple, limit: float):
    """(status, value, error_text, secs): status 'ok' | 'timeout' | 'exception'.

    On Linux the call is interrupted after `limit` seconds (and every 50 ms after that); on
    other platforms it runs to completion and is only measured. Never raises.
    """
    timed = False
    prev = None
    t0 = time.perf_counter()
    status, value, exc = "exception", None, None
    try:
        if limit > 0 and _can_time():
            prev = signal.signal(signal.SIGALRM, _on_alarm)
            signal.setitimer(signal.ITIMER_REAL, limit, 0.05)
            timed = True
        _ARMED[0] = timed
        try:
            value = fn(*args)
            status = "ok"
        except ChildTimeout:
            status = "timeout"
        except BaseException as e:
            exc = e
    except BaseException as e:
        exc = e
    finally:
        _ARMED[0] = False
        if timed:
            try:
                signal.setitimer(signal.ITIMER_REAL, 0.0)
                signal.signal(signal.SIGALRM, prev if prev is not None else signal.SIG_DFL)
            except BaseException:
                pass
    secs = time.perf_counter() - t0
    if isinstance(exc, ChildTimeout):
        status, exc = "timeout", None
    if status == "timeout":
        return status, None, f"timeout: interrupted after {secs:.2f} s", secs
    error = None
    if exc is not None:
        try:
            error = "".join(traceback.format_exception(exc))[-TB_CHARS:]
        except BaseException:
            error = repr(exc)[:TB_CHARS]
    return status, value, error, secs


_EXEMPT.add(timed_call.__code__)


def load_module(path: str, name: str, register: bool):
    """Load a file like arena's worker (spec_from_file_location + exec of its source)."""
    path_obj = Path(path)
    source = path_obj.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path_obj)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[name] = module
    exec(compile(source, str(path_obj), "exec"), module.__dict__)
    return module


def load_class(path: str, cls_name: str, name: str, register: bool, limit: float):
    """(cls | None, error | None), loading under a timer."""

    def go():
        obj = load_module(path, name, register)
        for part in cls_name.split("."):
            obj = getattr(obj, part)
        return obj

    status, value, error, _ = timed_call(go, (), limit)
    return (value, None) if status == "ok" else (None, error or status)


def literal_copy(value):
    """(ok, copy, text): whether value survives repr -> literal_eval unchanged."""
    try:
        text = repr(value)
    except Exception:
        return False, None, None
    try:
        copy = ast.literal_eval(text)
    except Exception:
        return False, None, text
    try:
        same = bool(copy == value)
    except Exception:
        same = False
    return same, copy, text


def jsonable(value):
    try:
        json.dumps(value)
        return value
    except Exception:
        return repr(value)[:2000]


# ---------------------------------------------------------------------------------- probe


def run_probe(job: dict) -> dict:
    act_timeout = float(job.get("act_timeout_s", 1.0))
    init_limit = float(job.get("init_timeout_s", 10.0))
    raw = bool(job.get("raw", False))
    stem = Path(job["strategy_path"]).stem
    cls, error = load_class(
        job["strategy_path"], job.get("strategy_class", "Strategy"), stem, raw, init_limit
    )
    out = {"ok": False, "load_error": error, "calls": [], "max_secs": 0.0}
    if cls is None:
        return out
    observations = job.get("observations", [])
    ok = True
    for pid in job.get("player_ids", [0, 1]):
        status, inst, error, _ = timed_call(cls, (pid,), init_limit)
        if status != "ok":
            out["load_error"] = f"__init__({pid}) failed: {error}"
            return out
        for text in observations:
            obs = ast.literal_eval(text)
            OUTPUT.key = ("probe", pid)
            status, action, error, secs = timed_call(inst.act, (obs,), 2 * act_timeout)
            OUTPUT.key = None
            literal_ok, _, rtext = literal_copy(action) if status == "ok" else (False, None, None)
            if rtext is not None and len(rtext) > MAX_REPLY_BYTES:
                literal_ok = False
            call_ok = status == "ok" and secs <= act_timeout
            if status == "ok" and not call_ok:
                error = f"timeout: act took {secs:.2f} s (limit {act_timeout:.2f} s)"
            ok = ok and call_ok
            out["max_secs"] = max(out["max_secs"], secs)
            out["calls"].append(
                {
                    "player_id": pid,
                    "ok": call_ok,
                    "secs": round(secs, 4),
                    "action_repr": (rtext or "")[:300],
                    "error": error,
                    "literal_ok": literal_ok,
                }
            )
    out["ok"] = ok
    return out


# -------------------------------------------------------------------------------- matches


def new_seat_stats() -> dict:
    return {
        "max_act_s": 0.0,
        "mean_act_s": 0.0,
        "exceptions": 0,
        "timeouts": 0,
        "forfeit_turn": None,
        "first_error": None,
        "malformed": 0,
        "illegal": 0,
        "guard_stats": None,
    }


def decide_winner(scores: list[float], forfeits: list[bool], tolerance: float) -> int | None:
    if forfeits[0] and forfeits[1]:
        return None
    if forfeits[0] or forfeits[1]:
        return 1 if forfeits[0] else 0
    s0, s1 = scores
    if abs(s0 - s1) <= tolerance * (abs(s0) + abs(s1)):
        return None
    return 0 if s0 > s1 else 1


def play_one(job: dict, sim_cls, classes: dict, pairing: list, index: int) -> dict:
    a, b, seed = pairing[0], pairing[1], int(pairing[2])
    players = [a, b]
    act_timeout = float(job.get("act_timeout_s", 1.0))
    init_limit = float(job.get("init_timeout_s", 10.0))
    max_turns = int(job.get("max_turns", 5000))
    tolerance = float(job.get("draw_tolerance", 0.01))
    hard_limit = max(2 * act_timeout, act_timeout + 0.5)
    stats = [new_seat_stats(), new_seat_stats()]
    result = {
        "players": players,
        "seed": seed,
        "scores": None,
        "winner": None,
        "turns": 0,
        "sim_error": None,
        "player_stats": stats,
    }

    def sim_fail(what: str, error: str | None) -> dict:
        result["sim_error"] = f"{what}: {error}"[-TB_CHARS:]
        return result

    if sim_cls is None:
        return sim_fail("simulator failed to load", job.get("_sim_load_error"))
    status, game, error, _ = timed_call(sim_cls, (seed,), SIM_CALL_LIMIT_S)
    if status != "ok":
        return sim_fail(f"Game({seed}) failed", error)

    insts: list = [None, None]
    alive = [False, False]
    total_secs = [0.0, 0.0]
    n_calls = [0, 0]
    for seat, eid in enumerate(players):
        entry = classes.get(eid)
        if entry is None:  # idle: always None, never forfeits
            continue
        cls, load_error, load_secs = entry
        if cls is None:
            stats[seat]["forfeit_turn"] = 0
            stats[seat]["exceptions" if "timeout" not in str(load_error) else "timeouts"] += 1
            stats[seat]["first_error"] = f"load failed: {load_error}"[-TB_CHARS:]
            continue
        OUTPUT.key = (index, seat)
        # arena's init_timeout covers import and __init__ together
        limit = max(0.1, init_limit - load_secs)
        status, inst, error, _ = timed_call(cls, (seat,), limit)
        OUTPUT.key = None
        if status != "ok":
            stats[seat]["forfeit_turn"] = 0
            stats[seat]["exceptions" if status == "exception" else "timeouts"] += 1
            stats[seat]["first_error"] = f"__init__ failed: {error}"[-TB_CHARS:]
            continue
        insts[seat] = inst
        alive[seat] = True

    has_report = callable(getattr(game, "action_report", None))
    turn = 0
    while True:
        status, over, error, _ = timed_call(game.is_over, (), SIM_CALL_LIMIT_S)
        if status != "ok":
            return sim_fail("is_over() failed", error)
        if over or turn >= max_turns:
            break
        observations = []
        for seat in range(2):
            status, obs, error, _ = timed_call(game.observe, (seat,), SIM_CALL_LIMIT_S)
            if status != "ok":
                return sim_fail(f"observe({seat}) failed at turn {turn}", error)
            same, copy, text = literal_copy(obs)
            if not same:
                return sim_fail(f"observe({seat}) at turn {turn}", f"not a literal: {text!r:.200}")
            observations.append(copy)
        actions: list = [None, None]
        for seat in range(2):
            if not alive[seat]:
                continue
            OUTPUT.key = (index, seat)
            status, action, error, secs = timed_call(
                insts[seat].act, (observations[seat],), hard_limit
            )
            OUTPUT.key = None
            st = stats[seat]
            total_secs[seat] += secs
            n_calls[seat] += 1
            st["max_act_s"] = max(st["max_act_s"], secs)
            failure = None
            if status == "exception":
                st["exceptions"] += 1
                failure = error
            elif status == "timeout" or secs > act_timeout:
                st["timeouts"] += 1
                failure = f"timeout: act took {secs:.2f} s at turn {turn} (limit {act_timeout} s)"
            else:
                try:
                    text = repr(action)
                except Exception:
                    text = None
                    failure = f"repr(action) failed: {traceback.format_exc()[-TB_CHARS:]}"
                    st["exceptions"] += 1
                if text is not None and len(text) > MAX_REPLY_BYTES:
                    failure = f"reply longer than {MAX_REPLY_BYTES} bytes"
                elif text is not None:
                    try:
                        actions[seat] = ast.literal_eval(text)
                    except Exception:
                        actions[seat] = None  # not a literal: the game sees a malformed action
            if failure is not None:
                alive[seat] = False
                actions[seat] = None
                st["forfeit_turn"] = turn
                if st["first_error"] is None:
                    st["first_error"] = f"turn {turn}: {failure}"[-TB_CHARS:]
        status, _, error, _ = timed_call(game.step, (actions,), SIM_CALL_LIMIT_S)
        if status != "ok":
            return sim_fail(f"step() failed at turn {turn}", error)
        if has_report:
            for seat in range(2):
                status, rep, error, _ = timed_call(game.action_report, (seat,), SIM_CALL_LIMIT_S)
                if status == "ok" and isinstance(rep, dict):
                    try:
                        if not rep.get("well_formed", True):
                            stats[seat]["malformed"] += 1
                        stats[seat]["illegal"] += int(rep.get("n_illegal", 0) or 0)
                    except Exception:
                        pass
        turn += 1
    result["turns"] = turn

    status, scores, error, _ = timed_call(game.scores, (), SIM_CALL_LIMIT_S)
    if status != "ok":
        return sim_fail("scores() failed", error)
    try:
        scores = [float(s) for s in scores]
        if len(scores) != 2 or not all(math.isfinite(s) for s in scores):
            raise ValueError(f"expected 2 finite scores, got {scores!r}")
    except Exception:
        return sim_fail("scores()", traceback.format_exc())
    result["scores"] = scores
    forfeits = [stats[s]["forfeit_turn"] is not None for s in range(2)]
    result["winner"] = decide_winner(scores, forfeits, tolerance)

    for seat in range(2):
        stats[seat]["max_act_s"] = round(stats[seat]["max_act_s"], 4)
        stats[seat]["mean_act_s"] = (
            round(total_secs[seat] / n_calls[seat], 5) if n_calls[seat] else 0.0
        )
        inst = insts[seat]
        getter = getattr(inst, "guard_stats", None) if inst is not None else None
        if callable(getter):
            status, gs, _, _ = timed_call(getter, (), 2.0)
            if status == "ok" and isinstance(gs, dict):
                stats[seat]["guard_stats"] = jsonable(gs)
    return result


def load_entries(job: dict, pairing: list, index: int) -> dict:
    """Fresh strategy classes for one match, as arena starts a fresh worker for every match.

    {id: None (idle) | (cls | None, error | None, load seconds)}. Module-level state of a
    strategy (caches, counters) thus never leaks from one match into the next.
    """
    init_limit = float(job.get("init_timeout_s", 10.0))
    entries = job.get("entries", {})
    classes: dict = {}
    for seat, eid in enumerate(pairing[:2]):
        path = entries.get(eid)
        if eid in classes or path is None:
            classes.setdefault(eid, None)
            continue
        OUTPUT.key = (index, seat)
        t0 = time.perf_counter()
        cls, error = load_class(
            path, job.get("strategy_class", "Strategy"), f"_hz_entry_{seat}", True, init_limit
        )
        OUTPUT.key = None
        classes[eid] = (cls, error, time.perf_counter() - t0)
    return classes


def run_matches(job: dict) -> dict:
    sim_cls, sim_error = load_class(
        job["sim_path"], job.get("sim_class", "Game"), "_hz_sim", True, SIM_CALL_LIMIT_S
    )
    job["_sim_load_error"] = sim_error
    progress_path = job.get("progress_path")
    progress = open(progress_path, "a", encoding="utf-8") if progress_path else None
    results = []
    try:
        for index, pairing in enumerate(job.get("pairings", [])):
            before = set(sys.modules)
            try:
                classes = load_entries(job, pairing, index)
                res = play_one(job, sim_cls, classes, pairing, index)
            except BaseException:
                res = {
                    "players": [pairing[0], pairing[1]],
                    "seed": int(pairing[2]),
                    "scores": None,
                    "winner": None,
                    "turns": 0,
                    "sim_error": f"runner error: {traceback.format_exc()[-TB_CHARS:]}",
                    "player_stats": [new_seat_stats(), new_seat_stats()],
                }
            finally:
                classes = None
                for name in [m for m in sys.modules if m.startswith("_hz_") and m not in before]:
                    sys.modules.pop(name, None)  # this match's strategy modules: drop them
                gc.collect()
            results.append(res)
            if progress is not None:
                progress.write(json.dumps(res) + "\n")
                progress.flush()
    finally:
        if progress is not None:
            progress.close()
    return {"ok": True, "results": results}


# ------------------------------------------------------------------------------- selftest


def run_selftest(job: dict) -> dict:
    module_cls, error = load_class(job["sim_path"], "selftest", "_hz_sim", True, SIM_CALL_LIMIT_S)
    if module_cls is None:
        return {"ok": False, "passed": False, "report": f"sim.py did not load: {error}"}
    status, failures, error, secs = timed_call(module_cls, (), float(job.get("limit_s", 50.0)))
    if status != "ok":
        return {"ok": True, "passed": False, "report": f"selftest() raised: {error}"}
    if not isinstance(failures, list | tuple):
        return {
            "ok": True,
            "passed": False,
            "report": f"selftest() must return a list of failures, got {failures!r:.300}",
        }
    lines = [str(f)[:1000] for f in failures]
    if not lines:
        return {"ok": True, "passed": True, "report": f"selftest: all passed ({secs:.2f} s)"}
    report = f"selftest: {len(lines)} failure(s)\n" + "\n".join(f"- {line}" for line in lines)
    return {"ok": True, "passed": False, "report": report[:8000]}


# ------------------------------------------------------------------------------- checksim


def type_problems(path: str, value, example, depth: int = 0) -> list[str]:
    """Differences in kind between a simulator value and the spec's example value."""
    if example is None or depth > 3:
        return []
    if isinstance(example, bool):
        ok = isinstance(value, bool)
    elif isinstance(example, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif isinstance(example, float):
        ok = isinstance(value, int | float) and not isinstance(value, bool)
    else:
        ok = type(value) is type(example)
    if not ok:
        return [f"{path}: expected {type(example).__name__}, got {type(value).__name__}"]
    if isinstance(example, list | tuple) and example and value:
        return type_problems(f"{path}[0]", value[0], example[0], depth + 1)
    if isinstance(example, dict):
        out = []
        for k in example:
            if k in value:
                out += type_problems(f"{path}[{k!r}]", value[k], example[k], depth + 1)
        return out
    return []


def garbage_action(rng: random.Random, obs):
    """Random values a buggy strategy might send: wrong shapes, types and ranges."""
    n = 0
    if isinstance(obs, dict):
        for v in obs.values():
            if isinstance(v, int) and not isinstance(v, bool):
                n = max(n, v)
    n = max(3, min(n, 50))
    choices = [
        lambda: None,
        lambda: [],
        lambda: 0,
        lambda: -1,
        lambda: "move",
        lambda: 1.5,
        lambda: True,
        lambda: {"a": 1},
        lambda: (1, 2, 3),
        lambda: [None],
        lambda: [[1, 2, 3]],
        lambda: [(1, 2)],
        lambda: [(rng.randint(-2, n + 2), rng.randint(-2, n + 2), rng.randint(-5, 40))],
        lambda: [
            (rng.randint(0, n), rng.randint(0, n), rng.randint(0, 10))
            for _ in range(rng.randint(1, 12))
        ],
        lambda: [(0, 1, 10**12)],
        lambda: [(True, False, 1)],
        lambda: [(1.0, 2.0, 3.0)],
        lambda: {"moves": [(0, 1, 1)]},
        lambda: [rng.randint(0, n) for _ in range(rng.randint(0, 5))],
    ]
    return rng.choice(choices)()


def run_checksim(job: dict) -> dict:
    problems: list[str] = []
    warnings: list[str] = []
    sim_cls, error = load_class(
        job["sim_path"], job.get("sim_class", "Game"), "_hz_sim", True, SIM_CALL_LIMIT_S
    )
    if sim_cls is None:
        return {"ok": True, "passed": False, "problems": [f"sim.py did not load: {error}"]}
    obs_keys = job.get("obs_keys")
    examples = []
    for text in job.get("example_observations") or []:
        try:
            ex = ast.literal_eval(text)
            if isinstance(ex, dict):
                examples.append(ex)
        except Exception:
            pass
    max_turns = int(job.get("max_turns", 5000))
    seeds = [int(s) for s in job.get("seeds", [0, 1, 2])]

    def check_obs(obs, where: str) -> None:
        same, _, text = literal_copy(obs)
        if not isinstance(obs, dict):
            problems.append(f"{where}: observation is {type(obs).__name__}, expected dict")
            return
        if not same:
            problems.append(f"{where}: observation is not a plain literal: {text!r:.200}")
            return
        if obs_keys:
            missing = sorted(set(obs_keys) - set(obs))
            extra = sorted(set(obs) - set(obs_keys))
            if missing or extra:
                problems.append(
                    f"{where}: keys differ from the spec: missing {missing}, extra {extra}"
                )
        for key, value in obs.items():
            example = next((ex[key] for ex in examples if key in ex), None)
            for p in type_problems(key, value, example):
                problems.append(f"{where}: {p}")

    for seed in seeds:
        status, game, error, _ = timed_call(sim_cls, (seed,), SIM_CALL_LIMIT_S)
        if status != "ok":
            problems.append(f"Game({seed}) failed: {error}")
            continue
        firsts = []
        for seat in range(2):
            status, obs, error, _ = timed_call(game.observe, (seat,), SIM_CALL_LIMIT_S)
            if status != "ok":
                problems.append(f"Game({seed}).observe({seat}) failed: {error}")
                continue
            check_obs(obs, f"Game({seed}).observe({seat})")
            firsts.append(repr(obs))
        status, game2, error, _ = timed_call(sim_cls, (seed,), SIM_CALL_LIMIT_S)
        if status == "ok":
            status, obs2, error, _ = timed_call(game2.observe, (0,), SIM_CALL_LIMIT_S)
            if status == "ok" and firsts and repr(obs2) != firsts[0]:
                problems.append(f"Game({seed}): not deterministic (same seed, other observation)")
        rep_fn = getattr(game, "action_report", None)
        if not callable(rep_fn):
            warnings.append("warning: Game has no action_report(player_id)")
        if len(problems) > 30:
            break

        rng = random.Random(seed)
        for label, garbage in (("idle vs idle", False), ("garbage vs idle", True)):
            status, g, error, _ = timed_call(sim_cls, (seed,), SIM_CALL_LIMIT_S)
            if status != "ok":
                break

            def play(g=g, garbage=garbage, seed=seed, rng=rng):
                turns = 0
                while not g.is_over():
                    if turns >= max_turns:
                        return turns, f"is_over() not True after {max_turns} turns"
                    obs = [g.observe(0), g.observe(1)]
                    if turns < 3 or turns % 25 == 0:
                        check_obs(obs[0], f"seed {seed} turn {turns} observe(0)")
                    actions = [garbage_action(rng, obs[0]) if garbage else None, None]
                    g.step(actions)
                    if callable(getattr(g, "action_report", None)):
                        rep = g.action_report(0)
                        if not (
                            isinstance(rep, dict)
                            and isinstance(rep.get("well_formed"), bool)
                            and isinstance(rep.get("n_illegal"), int)
                        ):
                            return turns, f"action_report(0) returned {rep!r:.200}"
                    turns += 1
                scores = g.scores()
                if not (
                    isinstance(scores, list | tuple)
                    and len(scores) == 2
                    and all(
                        isinstance(s, int | float) and not isinstance(s, bool) and math.isfinite(s)
                        for s in scores
                    )
                ):
                    return turns, f"scores() must be 2 finite numbers, got {scores!r:.200}"
                return turns, None

            status, value, error, _ = timed_call(play, (), SIM_CALL_LIMIT_S * 2)
            if status != "ok":
                problems.append(f"seed {seed}, {label}: raised: {error}")
            elif value[1] is not None:
                problems.append(f"seed {seed}, {label}: {value[1]}")
    seen: set = set()
    unique = [p for p in problems if not (p in seen or seen.add(p))]
    return {"ok": True, "passed": not unique, "problems": unique[:40] + sorted(set(warnings))}


# ----------------------------------------------------------------------------------- main


def set_limits(memory_mb) -> None:
    try:
        import resource
    except ImportError:  # not POSIX
        return
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if memory_mb:
            limit = int(memory_mb) * 2**20
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        file_bytes = 64 * 2**20  # bounds what anything can write to disk via raw fds
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
        if hasattr(signal, "SIGXFSZ"):
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)  # EFBIG instead of being killed
    except Exception:
        pass


def main() -> None:
    global OUTPUT
    # The result owns the original stdout; everything else written to fd 1 goes to stderr.
    channel = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    OUTPUT = CappedOutput(sys.__stderr__)
    sys.stdout = sys.stderr = OUTPUT
    try:
        job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        set_limits(job.get("memory_mb"))
        mode = job.get("mode")
        handler = {
            "probe": run_probe,
            "matches": run_matches,
            "selftest": run_selftest,
            "checksim": run_checksim,
        }.get(mode)
        if handler is None:
            result = {"ok": False, "error": f"unknown mode {mode!r}"}
        else:
            result = handler(job)
    except BaseException:
        result = {"ok": False, "error": traceback.format_exc()[-TB_CHARS:]}
    try:
        text = json.dumps(result)
    except Exception:
        text = json.dumps({"ok": False, "error": "result not JSON: " + repr(result)[:2000]})
    channel.write(text + "\n")
    channel.flush()
    os._exit(0)  # do not run untrusted atexit hooks or wait for stray threads


if __name__ == "__main__":
    main()
