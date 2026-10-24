"""Tests for harness.guard: the never-forfeit wrapper around LLM-written strategy code."""

import ast
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from harness.guard import assemble, sanitize, write_atomic

FIXTURES = Path(__file__).parent / "fixtures"
HAS_TIMER = hasattr(signal, "setitimer")
linux_only = pytest.mark.skipif(not HAS_TIMER, reason="needs signal.setitimer (Linux)")

BAD_CORES = [
    "bad_raise_turn5",
    "bad_sleep_turn3",
    "bad_infinite_loop",
    "bad_bare_except_loop",
    "bad_non_literal",
    "bad_import_numpy",
    "bad_init_raises",
    "bad_print_flood",
    "bad_misc",
]
# Cores that do not hang: safe to call in-process on platforms without setitimer.
NON_HANGING = [c for c in BAD_CORES if c not in ("bad_infinite_loop", "bad_bare_except_loop")]


def fixture_src(name: str) -> str:
    return (FIXTURES / f"{name}.py").read_text(encoding="utf-8")


def load_like_arena(path: Path, name: str = "Strategy"):
    """Load a strategy class exactly as arena's worker does (exec of the source)."""
    from arena.worker import load

    return load(f"{path}:{name}", path.read_text(encoding="utf-8"))


def write_guarded(tmp_path: Path, name: str, core: str | None, **kw) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(assemble(core, **kw), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def beta_obs() -> dict:
    from arena_games_dev.beta.game import BetaGame

    return BetaGame(0).observe(0)


def test_assemble_none_always_returns_none(tmp_path):
    path = write_guarded(tmp_path, "fallback", None)
    src = path.read_text(encoding="utf-8")
    compile(src, str(path), "exec")
    assert "import numpy" not in src
    cls = load_like_arena(path)
    s = cls(1)
    assert s.act({"turn": 0}) is None
    assert s.act(None) is None
    stats = s.guard_stats()
    for key in (
        "turns",
        "core_ok",
        "core_exceptions",
        "core_timeouts",
        "core_bad_output",
        "fallback_used",
        "none_used",
        "max_core_s",
        "first_core_traceback",
    ):
        assert key in stats
    assert stats["turns"] == 2 and stats["none_used"] == 2


def test_assembled_file_is_stdlib_only(tmp_path):
    path = write_guarded(tmp_path, "g", fixture_src("greedy_beta"))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= set(sys.stdlib_module_names)


def test_good_core_plays_like_the_raw_core(tmp_path, beta_obs):
    path = write_guarded(tmp_path, "good", fixture_src("greedy_beta"))
    guarded = load_like_arena(path)(0)
    raw = load_like_arena(FIXTURES / "greedy_beta.py")(0)
    assert guarded.act(dict(beta_obs)) == raw.act(dict(beta_obs))
    stats = guarded.guard_stats()
    assert stats["core_ok"] == 1 and stats["fallback_used"] == 0


@pytest.mark.parametrize("name", NON_HANGING)
def test_bad_core_never_raises_and_falls_back(tmp_path, beta_obs, name):
    if name == "bad_sleep_turn3" and not HAS_TIMER:
        pytest.skip("no hard timer on this platform")
    path = write_guarded(tmp_path, name, fixture_src(name), fallback_src=fixture_src("greedy_beta"))
    s = load_like_arena(path)(0)
    for _ in range(8):
        action = s.act(dict(beta_obs))
        assert ast.literal_eval(repr(action)) == action
        assert len(repr(action)) <= 900_000
    stats = s.guard_stats()
    assert stats["turns"] == 8
    assert stats["core_ok"] + stats["fallback_used"] + stats["none_used"] == 8
    if name != "bad_print_flood":
        assert stats["fallback_used"] >= 1
        assert stats["first_core_traceback"]


def test_print_flood_is_silenced(tmp_path, beta_obs, capfd):
    path = write_guarded(tmp_path, "flood", fixture_src("bad_print_flood"))
    s = load_like_arena(path)(0)
    s.act(dict(beta_obs))
    out, err = capfd.readouterr()
    assert len(out) + len(err) < 1000


def test_raising_core_is_reported(tmp_path, beta_obs):
    path = write_guarded(tmp_path, "r", fixture_src("bad_raise_turn5"), fallback_src=None)
    s = load_like_arena(path)(0)
    actions = [s.act(dict(beta_obs)) for _ in range(6)]
    assert actions[4] is None and actions[5] == []
    stats = s.guard_stats()
    assert stats["core_exceptions"] == 1 and stats["none_used"] == 1
    assert "boom on turn 5" in stats["first_core_traceback"]


def test_core_import_and_init_failures(tmp_path):
    for name, needle in (
        ("bad_import_numpy", "numpy"),
        ("bad_init_raises", "cannot initialise"),
    ):
        path = write_guarded(
            tmp_path,
            name,
            fixture_src(name),
            fallback_src="class Strategy:\n"
            "    def __init__(self, pid): pass\n"
            "    def act(self, obs): return []\n",
        )
        s = load_like_arena(path)(0)
        assert s.act({}) == []
        stats = s.guard_stats()
        assert stats["fallback_used"] == 1
        assert needle in stats["first_core_traceback"]


def test_syntax_error_core_and_missing_class(tmp_path):
    for core in ("def broken(:\n", "class Other:\n    pass\n"):
        path = write_guarded(tmp_path, "broken", core)
        s = load_like_arena(path)(0)
        assert s.act({"turn": 1}) is None
        assert s.guard_stats()["core_loaded"] is False


def test_time_budget_print_and_dataclasses(tmp_path):
    core = (
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "TIME_BUDGET = 0.25\n"
        "@dataclass\n"
        "class P:\n"
        "    x: int\n"
        "class Strategy:\n"
        "    def __init__(self, pid):\n"
        "        self.p = P(pid)\n"
        "    def act(self, obs):\n"
        "        print('noise')\n"
        "        return (TIME_BUDGET, self.p.x)\n"
    )
    path = write_guarded(tmp_path, "dc", core, time_budget_s=0.125)
    s = load_like_arena(path)(3)
    assert s.act({}) == (0.125, 3)


def test_nan_and_fake_ints_are_bad_output(tmp_path):
    core = (
        "class F(int):\n"
        "    def __repr__(self):\n"
        "        return 'np.int64(1)'\n"
        "class Strategy:\n"
        "    def __init__(self, pid):\n"
        "        self.t = 0\n"
        "    def act(self, obs):\n"
        "        self.t += 1\n"
        "        return [(0, 1, float('nan'))] if self.t == 1 else [(F(1), 1, 1)]\n"
    )
    path = write_guarded(tmp_path, "nan", core)
    s = load_like_arena(path)(0)
    assert s.act({}) is None and s.act({}) is None
    assert s.guard_stats()["core_bad_output"] == 2


def test_returned_value_is_a_plain_copy(tmp_path):
    core = (
        "class L(list):\n"
        "    pass\n"
        "class Strategy:\n"
        "    def __init__(self, pid):\n"
        "        pass\n"
        "    def act(self, obs):\n"
        "        obs['mutated'] = True\n"
        "        return L([(0, 1, 2)])\n"
    )
    path = write_guarded(tmp_path, "plain", core)
    action = load_like_arena(path)(0).act({})
    assert type(action) is list and action == [(0, 1, 2)]


def test_sanitize_rewrites_handlers_that_swallow_base_exceptions():
    src = (
        "import contextlib\n"
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    except:\n"
        "        pass\n"
        "    try:\n"
        "        pass\n"
        "    except (ValueError, BaseException):\n"
        "        pass\n"
        "    with contextlib.suppress(BaseException):\n"
        "        pass\n"
    )
    out = sanitize(src)
    assert "BaseException" not in out
    tree = ast.parse(out)
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
    assert all(h.type is not None for h in handlers)
    clean = "def f():\n    try:\n        pass\n    except Exception:  # keep me\n        pass\n"
    assert sanitize(clean) == clean
    assert sanitize("def broken(:\n") == "def broken(:\n"


def test_guard_works_off_the_main_thread(tmp_path, beta_obs):
    path = write_guarded(tmp_path, "thr", fixture_src("greedy_beta"))
    cls = load_like_arena(path)
    out = []
    t = threading.Thread(target=lambda: out.append(cls(0).act(dict(beta_obs))))
    t.start()
    t.join()
    assert out and isinstance(out[0], list)


def test_write_atomic(tmp_path):
    target = tmp_path / "strategy.py"
    write_atomic(target, "a = 1\n")
    write_atomic(target, "a = 2\n")
    assert target.read_text(encoding="utf-8") == "a = 2\n"
    with pytest.raises(UnicodeEncodeError):
        write_atomic(target, "bad \udcff surrogate")
    assert target.read_text(encoding="utf-8") == "a = 2\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["strategy.py"]


# ------------------------------------------------------------------------- Linux only


@linux_only
@pytest.mark.parametrize("name", ["bad_infinite_loop", "bad_bare_except_loop", "bad_sleep_turn3"])
def test_hanging_core_is_cut_within_the_act_limit(tmp_path, beta_obs, name):
    path = write_guarded(tmp_path, name, fixture_src(name), fallback_src=fixture_src("greedy_beta"))
    s = load_like_arena(path)(0)
    worst = 0.0
    for _ in range(4):
        t0 = time.perf_counter()
        action = s.act(dict(beta_obs))
        worst = max(worst, time.perf_counter() - t0)
        assert isinstance(action, list)
    assert worst < 0.85
    assert s.guard_stats()["core_timeouts"] >= 1


@linux_only
def test_outer_timer_and_handler_are_restored(tmp_path):
    path = write_guarded(
        tmp_path,
        "g",
        "class Strategy:\n    def __init__(self, p): pass\n    def act(self, obs): return []\n",
    )
    s = load_like_arena(path)(0)
    fired = []

    def outer(signum, frame):
        fired.append(signum)

    prev = signal.signal(signal.SIGALRM, outer)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.3)
        assert s.act({}) == []
        assert signal.getsignal(signal.SIGALRM) is outer
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 0
        time.sleep(0.5)
        assert fired == [signal.SIGALRM]
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prev)


def _arena_cli() -> Path:
    exe = Path(sys.executable)
    return exe.with_name("arena")


@linux_only
def test_every_guarded_bad_core_passes_arena_check(tmp_path):
    """MUST PASS: guard.assemble(bad core, fallback=greedy) never forfeits in arena's worker."""
    cli = _arena_cli()
    if not cli.exists():
        pytest.skip("arena CLI not installed next to this interpreter")
    jobs = []
    for name in BAD_CORES:
        path = write_guarded(
            tmp_path, name, fixture_src(name), fallback_src=fixture_src("greedy_beta")
        )
        jobs.append((name, path, "arena_games_dev.beta"))
    for game in ("arena_games_dev.alpha", "arena_games_dev.beta"):
        jobs.append(("none-" + game, write_guarded(tmp_path, "none_" + game[-4:], None), game))

    def check(job):
        name, path, game = job
        proc = subprocess.run(
            [str(cli), "check", f"{path}:Strategy", "--game", game, "--seeds", "2"],
            capture_output=True,
            text=True,
            timeout=900,
            cwd=tmp_path,
        )
        return name, proc.returncode, proc.stdout[-3000:] + proc.stderr[-3000:]

    with ThreadPoolExecutor(len(jobs)) as pool:
        outcomes = list(pool.map(check, jobs))
    failed = [(n, out) for n, code, out in outcomes if code != 0 or "PASSED" not in out]
    assert not failed, failed
