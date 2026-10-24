"""Tests for harness.sandbox and harness.runner_child: untrusted code in child processes."""

import ast
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness import sandbox
from harness.guard import assemble

FIXTURES = Path(__file__).parent / "fixtures"
BETA_SIM = FIXTURES / "beta_sim.py"
TINY_SIM = FIXTURES / "tiny_sim.py"
GREEDY = FIXTURES / "greedy_beta.py"
HAS_TIMER = hasattr(signal, "setitimer")
linux_only = pytest.mark.skipif(not HAS_TIMER, reason="needs signal.setitimer (Linux)")


@pytest.fixture(scope="module")
def beta_obs() -> list[dict]:
    from arena_games_dev.beta.game import BetaGame

    g = BetaGame(3)
    out = [g.observe(0), g.observe(1)]
    g.step([[], []])
    out.append(g.observe(0))
    return out


def test_runner_child_is_stdlib_only():
    src = (Path(sandbox.__file__).with_name("runner_child.py")).read_text(encoding="utf-8")
    tree = ast.parse(src)
    import sys

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert name.split(".")[0] in sys.stdlib_module_names, name


def test_probe_good_strategy(tmp_path, beta_obs):
    res = sandbox.probe(GREEDY, beta_obs, raw=True, work_dir=tmp_path)
    assert res.ok, res
    assert res.load_error is None
    assert len(res.calls) == 2 * len(beta_obs)
    assert all(c["literal_ok"] and c["error"] is None for c in res.calls)
    assert res.calls[0]["action_repr"].startswith("[")


def test_probe_raw_raising_core_reports_traceback(tmp_path, beta_obs):
    obs = beta_obs * 2  # 6 calls per player: the 5th raises
    res = sandbox.probe(FIXTURES / "bad_raise_turn5.py", obs, raw=True, work_dir=tmp_path)
    assert not res.ok
    errors = [c for c in res.calls if c["error"]]
    assert errors and "boom on turn 5" in errors[0]["error"]
    assert "Traceback" in errors[0]["error"]


def test_probe_raw_numpy_import_fails_like_the_tournament(tmp_path, beta_obs):
    res = sandbox.probe(FIXTURES / "bad_import_numpy.py", beta_obs, raw=True, work_dir=tmp_path)
    assert not res.ok
    assert "numpy" in (res.load_error or "")


def test_probe_init_failure(tmp_path, beta_obs):
    res = sandbox.probe(FIXTURES / "bad_init_raises.py", beta_obs, raw=True, work_dir=tmp_path)
    assert not res.ok and "cannot initialise" in (res.load_error or "")


def test_probe_non_literal(tmp_path, beta_obs):
    res = sandbox.probe(FIXTURES / "bad_non_literal.py", beta_obs, raw=True, work_dir=tmp_path)
    assert res.ok  # no exception; the bad output is flagged per call
    assert not any(c["literal_ok"] for c in res.calls)


def test_probe_guarded_bad_core_is_ok(tmp_path, beta_obs):
    path = tmp_path / "guarded.py"
    path.write_text(
        assemble(
            (FIXTURES / "bad_raise_turn5.py").read_text(encoding="utf-8"),
            fallback_src=GREEDY.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )
    res = sandbox.probe(path, beta_obs * 2, work_dir=tmp_path)
    assert res.ok, res
    assert all(c["literal_ok"] for c in res.calls)


def test_probe_missing_file(tmp_path):
    res = sandbox.probe(tmp_path / "nope.py", [{}], work_dir=tmp_path)
    assert not res.ok and res.load_error


@linux_only
def test_probe_raw_sleeper_is_interrupted(tmp_path, beta_obs):
    res = sandbox.probe(
        FIXTURES / "bad_sleep_turn3.py", beta_obs, raw=True, player_ids=(0,), work_dir=tmp_path
    )
    assert not res.ok
    assert res.calls[2]["error"].startswith("timeout")
    assert res.max_secs < 3.0


def test_play_matches_reproduces_arena_scores(tmp_path):
    """The fixture sim (copied from beta's game.py) gives arena's exact scores."""
    from arena.core import Entrant
    from arena.match import play_match
    from arena_games_dev.beta.game import BetaGame

    pairings = [("greedy", "idle", 5), ("idle", "greedy", 5)]
    results = sandbox.play_matches(
        BETA_SIM, {"greedy": GREEDY}, pairings, workers=2, work_dir=tmp_path
    )
    greedy = Entrant("greedy", f"{GREEDY}:Strategy")
    idle = Entrant("idle", "arena.strategies:Idle")
    for (a, b, seed), r in zip(pairings, results, strict=True):
        assert r.sim_error is None, r.sim_error
        ref = play_match(
            BetaGame, [greedy if a == "greedy" else idle, greedy if b == "greedy" else idle], seed
        )
        assert r.scores == ref.scores
        assert r.turns == ref.turns == 100
        assert r.winner == ref.winner
        seat = r.players.index("greedy")
        st = r.player_stats[seat]
        assert st["forfeit_turn"] is None and st["exceptions"] == 0
        assert st["malformed"] == 0 and st["illegal"] == 0
        assert st["max_act_s"] > 0


def test_play_matches_forfeit_and_winner(tmp_path):
    entries = {"raiser": FIXTURES / "bad_raise_turn5.py", "lister": FIXTURES / "list_player.py"}
    pairings = [("raiser", "lister", 0), ("lister", "raiser", 0), ("idle", "idle", 0)]
    results = sandbox.play_matches(TINY_SIM, entries, pairings, workers=2, work_dir=tmp_path)
    r0, r1, r2 = results
    assert r0.player_stats[0]["forfeit_turn"] == 4
    assert "boom on turn 5" in r0.player_stats[0]["first_error"]
    assert r0.winner == 1 and r1.winner == 0
    assert r2.winner is None and r2.scores == [0.0, 0.0]
    # tiny_sim reports idle's None as malformed every turn
    assert r2.player_stats[0]["malformed"] == 10


def test_play_matches_guard_stats_collected(tmp_path):
    path = tmp_path / "guarded.py"
    path.write_text(
        assemble((FIXTURES / "bad_raise_turn5.py").read_text(encoding="utf-8")), encoding="utf-8"
    )
    (r,) = sandbox.play_matches(TINY_SIM, {"g": path}, [("g", "idle", 1)], work_dir=tmp_path)
    st = r.player_stats[0]
    assert st["forfeit_turn"] is None
    assert st["guard_stats"]["core_exceptions"] == 1
    assert st["guard_stats"]["turns"] == 10


def test_play_matches_sim_errors(tmp_path):
    entries = {"lister": FIXTURES / "list_player.py"}
    (bug,) = sandbox.play_matches(TINY_SIM, entries, [("lister", "idle", 98)], work_dir=tmp_path)
    assert bug.scores is None and "simulator bug at turn 3" in bug.sim_error
    (missing,) = sandbox.play_matches(
        tmp_path / "nope.py", entries, [("lister", "idle", 0)], work_dir=tmp_path
    )
    assert missing.sim_error and missing.scores is None
    (unknown,) = sandbox.play_matches(TINY_SIM, entries, [("x", "idle", 0)], work_dir=tmp_path)
    assert "unknown entry" in unknown.sim_error


def test_play_matches_child_timeout_keeps_finished(tmp_path):
    entries = {"lister": FIXTURES / "list_player.py"}
    pairings = [("lister", "idle", 0), ("lister", "idle", 99), ("idle", "lister", 1)]
    results = sandbox.play_matches(
        TINY_SIM, entries, pairings, workers=1, timeout_s=4.0, work_dir=tmp_path
    )
    assert results[0].sim_error is None and results[0].scores == [10.0, 0.0]
    assert results[1].sim_error and results[1].sim_error.startswith("timeout")
    assert results[2].sim_error and results[2].sim_error.startswith("timeout")


def test_play_matches_print_flood_raw_forfeits(tmp_path):
    entries = {"flood": FIXTURES / "bad_print_flood.py"}
    (r,) = sandbox.play_matches(TINY_SIM, entries, [("flood", "idle", 0)], work_dir=tmp_path)
    assert r.sim_error is None
    assert r.player_stats[0]["forfeit_turn"] is not None  # arena would kill it at 16 MB


def test_run_selftest(tmp_path):
    ok, report = sandbox.run_selftest(BETA_SIM, work_dir=tmp_path)
    assert ok, report
    ok, report = sandbox.run_selftest(FIXTURES / "failing_selftest_sim.py", work_dir=tmp_path)
    assert not ok and "worked example 1" in report
    ok, report = sandbox.run_selftest(GREEDY, work_dir=tmp_path)
    assert not ok and "selftest" in report


def test_check_simulator_accepts_the_real_game(tmp_path):
    from arena_games_dev.beta.game import BetaGame

    example = BetaGame(11).observe(0)
    info = SimpleNamespace(obs_keys=list(example), example_observations=[example])
    ok, problems = sandbox.check_simulator(BETA_SIM, info, work_dir=tmp_path)
    assert ok, problems


def test_check_simulator_finds_problems(tmp_path):
    from arena_games_dev.beta.game import BetaGame

    example = BetaGame(11).observe(0)
    src = BETA_SIM.read_text(encoding="utf-8")
    broken = src.replace('"edges": list(self.edges),', '"edges": [list(e) for e in self.edges],')
    broken = broken.replace('"turn": self.turn,', '"turn": self.turn, "extra": 1,')
    assert broken != src
    path = tmp_path / "broken_sim.py"
    path.write_text(broken, encoding="utf-8")
    info = SimpleNamespace(obs_keys=list(example), example_observations=[example])
    ok, problems = sandbox.check_simulator(path, info, work_dir=tmp_path)
    text = "\n".join(problems)
    assert not ok
    assert "edges[0]: expected tuple, got list" in text
    assert "extra" in text


def test_check_simulator_garbage_crash(tmp_path):
    src = TINY_SIM.read_text(encoding="utf-8").replace(
        "self.last_ok[i] = isinstance(a, list)", "self.last_ok[i] = len(a) >= 0"
    )
    path = tmp_path / "fragile_sim.py"
    path.write_text(src, encoding="utf-8")
    ok, problems = sandbox.check_simulator(path, SimpleNamespace(), work_dir=tmp_path)
    assert not ok
    assert any("garbage vs idle" in p or "idle vs idle" in p for p in problems)


def test_children_hash_strings_reproducibly(tmp_path):
    path = tmp_path / "hasher.py"
    path.write_text(
        "class Strategy:\n"
        "    def __init__(self, pid):\n"
        "        pass\n"
        "    def act(self, obs):\n"
        "        return hash('reproducible')\n",
        encoding="utf-8",
    )
    first = sandbox.probe(path, [{}], player_ids=(0,), work_dir=tmp_path)
    second = sandbox.probe(path, [{}], player_ids=(0,), work_dir=tmp_path)
    assert first.ok and second.ok
    assert first.calls[0]["action_repr"] == second.calls[0]["action_repr"]


# ------------------------------------------------------------------ review: adversarial


def test_module_state_does_not_leak_between_matches(tmp_path):
    """arena starts a fresh worker per match: module globals must start fresh too."""
    path = tmp_path / "counter.py"
    path.write_text(
        "CALLS = []\n"
        "class Strategy:\n"
        "    def __init__(self, pid): pass\n"
        "    def act(self, obs):\n"
        "        CALLS.append(1)\n"
        "        return [] if len(CALLS) <= 10 else None\n",
        encoding="utf-8",
    )
    pairings = [("c", "idle", 0), ("c", "idle", 1), ("idle", "c", 2)]
    results = sandbox.play_matches(TINY_SIM, {"c": path}, pairings, workers=1, work_dir=tmp_path)
    assert [r.scores for r in results] == [[10.0, 0.0], [10.0, 0.0], [0.0, 10.0]]
    guarded = tmp_path / "counter_guarded.py"
    guarded.write_text(assemble(path.read_text(encoding="utf-8")), encoding="utf-8")
    results = sandbox.play_matches(
        TINY_SIM, {"g": guarded}, [("g", "idle", 0), ("g", "idle", 1)], workers=1, work_dir=tmp_path
    )
    assert [r.scores for r in results] == [[10.0, 0.0], [10.0, 0.0]]


def test_progress_file_with_a_cut_off_last_line(tmp_path):
    good = {"players": ["a", "b"], "seed": 0, "scores": [1.0, 0.0], "winner": 0, "turns": 3}
    p = tmp_path / "progress.jsonl"
    import json

    p.write_text(json.dumps(good) + "\n" + json.dumps(good) + "\n" + '{"players": ["a"', "utf-8")
    assert sandbox._read_progress(p) == [good, good]
    assert sandbox._read_progress(tmp_path / "missing.jsonl") == []


def test_killed_child_marks_the_match_in_progress(tmp_path):
    entries = {"lister": FIXTURES / "list_player.py"}
    pairings = [("lister", "idle", 0), ("lister", "idle", 99), ("idle", "lister", 1)]
    results = sandbox.play_matches(
        TINY_SIM, entries, pairings, workers=1, timeout_s=4.0, work_dir=tmp_path
    )
    assert results[0].sim_error is None and not results[0].in_progress
    assert results[1].sim_error == "timeout" and results[1].in_progress  # the contract's marker
    assert results[2].sim_error == "timeout" and not results[2].in_progress


def test_probe_never_raises_on_odd_input(tmp_path):
    res = sandbox.probe(tmp_path / "missing.py", [{"a": 1}], work_dir=tmp_path)
    assert not res.ok and res.load_error
    res = sandbox.probe(GREEDY, [], work_dir=tmp_path)
    assert res.ok and res.calls == []
    ok, problems = sandbox.check_simulator(tmp_path / "missing.py", None, work_dir=tmp_path)
    assert not ok and problems


def test_schema_check_accepts_none_where_the_spec_shows_none() -> None:
    from harness.runner_child import type_problems

    # list[int | None]: hidden nodes are None, visible ones an int (the spec's example has both)
    spec = [[0, None, None, 3]]
    assert type_problems("opp", [None, 2, 0, None], spec) == []
    assert type_problems("opp", [1.5], spec) == ["opp[0]: expected int, got float"]
    # where the examples never show None, None is still wrong
    assert type_problems("units", [None], [[1, 2]]) == ["units[0]: expected int, got NoneType"]
    assert type_problems("turn", None, [0, 1]) == ["turn: expected int, got NoneType"]
    # tuples stay tuples, bools are not ints, a field in any example observation counts
    assert type_problems("move", [[1, 2, 3]], [[(1, 2, 3)]]) == [
        "move[0]: expected tuple, got list"
    ]
    assert type_problems("n", True, [3]) == ["n: expected int, got bool"]
    assert type_problems("bonus", None, [5, None]) == []
