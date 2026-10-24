"""Run untrusted code (generated strategies and simulators) in child processes.

Every function here starts `runner_child.py` under `python -I -S` (standard library only, as in
the tournament), hands it a job file and reads back one JSON object. Nothing here raises on a
bad strategy or simulator: failures come back as data (tracebacks, sim_error, problems).
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RUNNER = Path(__file__).with_name("runner_child.py")
DEFAULT_WORK_DIR = Path(tempfile.gettempdir()) / "harness-sandbox"
CHILD_MEMORY_MB = 1536  # per child; two children plus the harness must fit in 4 GB

# Children get this environment and nothing else: in particular no API keys.
_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "LANG": "C.UTF-8",
    "PYTHONHASHSEED": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONIOENCODING": "utf-8",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
}
if os.name == "nt":  # Windows needs these to start Python at all
    for _key in ("SYSTEMROOT", "TEMP", "TMP"):
        if _key in os.environ:
            _ENV[_key] = os.environ[_key]


@dataclass
class ProbeResult:
    ok: bool  # loaded, and every call returned without exception/timeout
    load_error: str | None  # traceback if the file/class failed to load or __init__ raised
    calls: list[dict]  # per observation: {player_id, ok, secs, action_repr, error, literal_ok}
    max_secs: float


@dataclass
class MatchResult:
    players: list[str]  # entry ids, by seat
    seed: int
    scores: list[float] | None  # None if the simulator failed
    winner: int | None  # seat, None = draw / invalid
    turns: int
    sim_error: str | None  # traceback from the simulator: the match does not count
    player_stats: list[dict] = field(default_factory=list)
    # per seat: {max_act_s, mean_act_s, exceptions, timeouts, forfeit_turn, first_error,
    #            malformed, illegal, guard_stats (dict | None)}

    @classmethod
    def from_dict(cls, d: dict) -> "MatchResult":
        return cls(
            players=list(d.get("players") or []),
            seed=int(d.get("seed", 0)),
            scores=d.get("scores"),
            winner=d.get("winner"),
            turns=int(d.get("turns", 0)),
            sim_error=d.get("sim_error"),
            player_stats=list(d.get("player_stats") or []),
        )


def _empty_stats() -> dict:
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


def _invalid(a: str, b: str, seed: int, error: str) -> MatchResult:
    return MatchResult([a, b], seed, None, None, 0, error, [_empty_stats(), _empty_stats()])


def _kill(proc: subprocess.Popen) -> None:
    """Kill the child and whatever it spawned (its own process group on POSIX)."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _run_child(
    job: dict, timeout_s: float, work_dir: Path | None
) -> tuple[dict | None, str | None]:
    """Run one child on `job`: (parsed JSON result | None, error text | None). Never raises."""
    job_path = None
    try:
        work = Path(work_dir) if work_dir is not None else DEFAULT_WORK_DIR
        work.mkdir(parents=True, exist_ok=True)
        fd, job_path = tempfile.mkstemp(prefix="job-", suffix=".json", dir=work)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(job, f)
        with tempfile.TemporaryFile(dir=work) as errf:
            kwargs: dict[str, Any] = {}
            if os.name == "posix":
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(
                [sys.executable, "-I", "-S", str(RUNNER), job_path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=errf,
                cwd=work,
                env=_ENV,
                **kwargs,
            )
            try:
                out, _ = proc.communicate(timeout=max(1.0, timeout_s))
            except subprocess.TimeoutExpired:
                _kill(proc)
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                return None, f"timeout: child killed after {timeout_s:.0f} s"
            finally:
                if proc.poll() is None:
                    _kill(proc)
            if os.name == "posix":  # anything the strategy left running in the group
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            text = out.decode("utf-8", errors="replace").strip()
            if text:
                try:
                    return json.loads(text.splitlines()[-1]), None
                except ValueError:
                    pass
            errf.seek(0, os.SEEK_END)
            errf.seek(max(0, errf.tell() - 3000))
            tail = errf.read().decode("utf-8", errors="replace")
            return None, f"child exited with code {proc.returncode} and no result\n{tail}"
    except Exception as e:
        return None, f"sandbox error: {type(e).__name__}: {e}"
    finally:
        if job_path is not None:
            try:
                os.unlink(job_path)
            except OSError:
                pass


def probe(
    strategy_path: Path,
    observations: list[dict],
    *,
    strategy_class: str = "Strategy",
    player_ids: tuple[int, ...] = (0, 1),
    act_timeout_s: float = 1.0,
    timeout_s: float = 60.0,
    raw: bool = False,
    work_dir: Path | None = None,
) -> ProbeResult:
    """Instantiate the class once per player id and call act on every observation in order.

    raw=True: the file is a bare LLM core (no guard); exceptions come back with tracebacks and
    on Linux act calls over 2 * act_timeout_s are interrupted.
    """
    try:
        obs_texts = [repr(o) for o in observations]
    except Exception as e:
        return ProbeResult(False, f"observations not representable: {e}", [], 0.0)
    job = {
        "mode": "probe",
        "strategy_path": str(Path(strategy_path).resolve()),
        "strategy_class": strategy_class,
        "observations": obs_texts,
        "player_ids": list(player_ids),
        "act_timeout_s": act_timeout_s,
        "init_timeout_s": 10.0,
        "memory_mb": CHILD_MEMORY_MB,
        "raw": raw,
    }
    data, error = _run_child(job, timeout_s, work_dir)
    if data is None or "calls" not in data:
        detail = error or (data or {}).get("error") or "no result"
        return ProbeResult(False, f"probe child failed: {detail}", [], 0.0)
    return ProbeResult(
        ok=bool(data.get("ok")),
        load_error=data.get("load_error"),
        calls=list(data.get("calls") or []),
        max_secs=float(data.get("max_secs") or 0.0),
    )


def play_matches(
    sim_path: Path,
    entries: dict[str, Path | None],
    pairings: list[tuple[str, str, int]],
    *,
    strategy_class: str = "Strategy",
    sim_class: str = "Game",
    act_timeout_s: float = 1.0,
    max_turns: int = 5000,
    workers: int = 2,
    timeout_s: float = 300.0,
    draw_tolerance: float = 0.01,
    work_dir: Path | None = None,
) -> list[MatchResult]:
    """Play `pairings` (id_seat0, id_seat1, seed) on the simulator, in order of the list.

    entries: id -> strategy file; the id 'idle' (or a None path) plays None every turn. The
    pairings are split over `workers` child processes; a child that crashes or exceeds
    `timeout_s` returns sim_error results for the matches it did not finish.
    """
    pairings = [(str(a), str(b), int(s)) for a, b, s in pairings]
    if not pairings:
        return []
    known = {k: (None if v is None else str(Path(v).resolve())) for k, v in entries.items()}
    known.setdefault("idle", None)
    results: list[MatchResult | None] = [None] * len(pairings)
    playable: list[int] = []
    for i, (a, b, seed) in enumerate(pairings):
        missing = [e for e in (a, b) if e not in known]
        if missing:
            results[i] = _invalid(a, b, seed, f"unknown entry id(s): {missing}")
        else:
            playable.append(i)
    n_workers = max(1, min(int(workers), len(playable) or 1))
    chunks = [playable[k::n_workers] for k in range(n_workers)]
    try:
        work = Path(work_dir) if work_dir is not None else DEFAULT_WORK_DIR
        work.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return [r or _invalid(*pairings[i], f"sandbox error: {e}") for i, r in enumerate(results)]

    def run_chunk(chunk: list[int]) -> None:
        if not chunk:
            return
        used = {pairings[i][0] for i in chunk} | {pairings[i][1] for i in chunk}
        fd, progress_path = tempfile.mkstemp(prefix="progress-", suffix=".jsonl", dir=work)
        os.close(fd)
        job = {
            "mode": "matches",
            "sim_path": str(Path(sim_path).resolve()),
            "sim_class": sim_class,
            "strategy_class": strategy_class,
            "entries": {k: v for k, v in known.items() if k in used},
            "pairings": [list(pairings[i]) for i in chunk],
            "act_timeout_s": act_timeout_s,
            "init_timeout_s": 10.0,
            "max_turns": max_turns,
            "draw_tolerance": draw_tolerance,
            "memory_mb": CHILD_MEMORY_MB,
            "progress_path": progress_path,
        }
        try:
            data, error = _run_child(job, timeout_s, work)
            done: list[dict] = []
            if data is not None and isinstance(data.get("results"), list):
                done = data["results"]
            else:
                try:
                    lines = Path(progress_path).read_text(encoding="utf-8").splitlines()
                    done = [json.loads(line) for line in lines if line.strip()]
                except Exception:
                    done = []
                if error is None:
                    error = (data or {}).get("error") or "child returned no results"
            for k, i in enumerate(chunk):
                if k < len(done):
                    try:
                        results[i] = MatchResult.from_dict(done[k])
                        continue
                    except Exception:
                        pass
                results[i] = _invalid(*pairings[i], error or "child returned too few results")
        finally:
            try:
                os.unlink(progress_path)
            except OSError:
                pass

    threads = [threading.Thread(target=run_chunk, args=(c,), daemon=True) for c in chunks]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return [
        r if r is not None else _invalid(*pairings[i], "not played") for i, r in enumerate(results)
    ]


def run_selftest(
    sim_path: Path, timeout_s: float = 60.0, *, work_dir: Path | None = None
) -> tuple[bool, str]:
    """Import sim.py in a child and call its selftest() -> list of failures: (passed, report)."""
    job = {
        "mode": "selftest",
        "sim_path": str(Path(sim_path).resolve()),
        "memory_mb": CHILD_MEMORY_MB,
        "limit_s": max(1.0, timeout_s - 5.0),
    }
    data, error = _run_child(job, timeout_s, work_dir)
    if data is None or "passed" not in data:
        return False, f"selftest child failed: {error or (data or {}).get('error')}"
    return bool(data["passed"]), str(data.get("report", ""))


def check_simulator(
    sim_path: Path,
    spec_info: Any,
    *,
    seeds=(0, 1, 2),
    timeout_s: float = 60.0,
    sim_class: str = "Game",
    max_turns: int = 5000,
    work_dir: Path | None = None,
) -> tuple[bool, list[str]]:
    """Generic simulator checks, no LLM: (ok, problems).

    Game(seed) constructs; observe(0/1) are literal dicts with the spec's keys and value kinds;
    idle-vs-idle and garbage-vs-idle play to the end without raising; scores() is 2 finite
    numbers; is_over() comes within max_turns; the same seed gives the same first observation.
    spec_info: a harness.specdoc.SpecInfo (only obs_keys and example_observations are used).
    """
    obs_keys = getattr(spec_info, "obs_keys", None)
    examples = getattr(spec_info, "example_observations", None) or []
    try:
        example_texts = [repr(e) for e in examples if isinstance(e, dict)]
    except Exception:
        example_texts = []
    job = {
        "mode": "checksim",
        "sim_path": str(Path(sim_path).resolve()),
        "sim_class": sim_class,
        "seeds": [int(s) for s in seeds],
        "obs_keys": sorted(str(k) for k in obs_keys) if obs_keys else None,
        "example_observations": example_texts,
        "max_turns": max_turns,
        "memory_mb": CHILD_MEMORY_MB,
    }
    data, error = _run_child(job, timeout_s, work_dir)
    if data is None or "problems" not in data:
        return False, [f"checksim child failed: {error or (data or {}).get('error')}"]
    problems = [str(p) for p in data["problems"]]
    hard = [p for p in problems if not p.startswith("warning:")]
    return bool(data.get("passed")) and not hard, problems
