"""Tests for our own test games: the specs' worked examples and constants must match game.py
exactly, and the games must be robust and deterministic like the dev games.

    uv run pytest -q testgames/test_testgames.py
"""

import ast
import importlib
import re
from pathlib import Path

import pytest

PACKAGES = ["testgames.gamma", "testgames.delta"]

# The worked example of each spec: (seed, [player 0's action, player 1's action]).
EXAMPLES = {
    "testgames.gamma": (
        9,
        [
            {"moves": {"T0": 9, "T1": 1, "T2": 8, "S0": 8, "S1": 8, "S2": 2}},
            {"moves": {"T0": 6, "T1": 4, "S0": 8, "S1": 7, "S2": 7, "S3": 2, "S4": 2, "S5": 2}},
        ],
    ),
    "testgames.delta": (
        2,
        [
            {"moves": [(0, 11, 3), (0, 7, 3), (11, 8, 2)], "build": 1},
            {"moves": [(9, 10, 6), (9, 1, 2)], "build": 0},
        ],
    ),
}

GARBAGE = [
    None,
    [],
    (),
    0,
    1.5,
    "moves",
    True,
    {},
    {"moves": None},
    {"moves": [], "build": None},
    {"moves": {}, "build": 0},
    {"moves": {}, "extra": 1},
    {"moves": {"T0": True}},
    {"moves": {"T0": 1.0}},
    {"moves": {0: 1}},
    {"moves": {"T0": -1, "S9": 0, "XX": 3, "T1": 10**30}},
    {"moves": [(0, 1, 5)], "build": True},
    {"moves": [(0, 1, 5)], "build": 10**30},
    {"moves": [(0, 1, 5)], "build": -(10**30)},
    {"moves": [[0, 1, 5]], "build": 0},
    {"moves": [(0, 1)], "build": 0},
    {"moves": [(10**30, -(10**30), 10**30), (-1, 0, 1)], "build": 0},
    {"moves": ((0, 1, 1),), "build": 0},
    [(0, 1, 1)],
]


def _package(name: str):
    return importlib.import_module(name)


def _python_blocks(spec: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", spec, flags=re.DOTALL)


def _example_observations(spec: str) -> list[dict]:
    """Every python block of the spec that is a literal observation dict, in order."""
    found = []
    for block in _python_blocks(spec):
        try:
            value = ast.literal_eval(block.strip())
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, dict) and "turn" in value:
            found.append(value)
    return found


def _spec_constants(spec: str) -> dict:
    """NAME = literal assignments of the spec's constants block."""
    block = next(b for b in _python_blocks(spec) if "N_TURNS =" in b)
    constants = {}
    for node in ast.parse(block).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            constants[node.targets[0].id] = ast.literal_eval(node.value)
    return constants


def _play(game, strategies) -> list[dict]:
    """Play a match to the end; returns every observation seen, both seats."""
    seen = []
    while not game.is_over():
        observations = [game.observe(p) for p in range(2)]
        seen.extend(observations)
        game.step([s.act(o) for s, o in zip(strategies, observations, strict=True)])
    return seen


@pytest.mark.parametrize("name", PACKAGES)
def test_package_exports(name):
    package = _package(name)
    assert Path(package.SPEC).is_file()
    assert package.GAME.n_players == 2
    assert {"DoNothing", "RandomStrategy"} <= {b.__name__ for b in package.BASELINES}
    assert len(package.BASELINES) >= 3


@pytest.mark.parametrize("name", PACKAGES)
def test_worked_example_matches_game(name):
    package = _package(name)
    spec = Path(package.SPEC).read_text(encoding="utf-8")
    seed, actions = EXAMPLES[name]
    for action in actions:  # the actions we replay are exactly the ones the spec shows
        assert f"plays `{action!r}`".replace("'", '"') in spec

    examples = _example_observations(spec)
    assert len(examples) == 2
    game = package.GAME(seed)
    assert game.observe(0) == examples[0]
    game.step(actions)
    assert game.observe(0) == examples[1]


@pytest.mark.parametrize("name", PACKAGES)
def test_spec_constants_match_game(name):
    package = _package(name)
    module = importlib.import_module(f"{name}.game")
    constants = _spec_constants(Path(package.SPEC).read_text(encoding="utf-8"))
    assert constants
    for key, value in constants.items():
        assert getattr(module, key) == value, key


@pytest.mark.parametrize("name", PACKAGES)
def test_deterministic_and_literal(name):
    game_cls = _package(name).GAME
    for seed in range(5):
        a, b = game_cls(seed), game_cls(seed)
        for p in range(2):
            obs = a.observe(p)
            assert obs == b.observe(p)
            assert ast.literal_eval(repr(obs)) == obs


@pytest.mark.parametrize("name", PACKAGES)
def test_map_constraints(name):
    module = importlib.import_module(f"{name}.game")
    for seed in range(50):
        inst = module.generate_instance(seed)
        dist = module.hop_distances(inst.n_nodes, inst.edges)
        assert all(d >= 0 for row in dist for d in row)  # connected
        assert dist[inst.bases[0]][inst.bases[1]] >= module.MIN_BASE_DISTANCE
        assert inst.edges == sorted(set(inst.edges))
        assert all(u < v for u, v in inst.edges)
        lo, hi = module.N_NODES_RANGE
        assert lo <= inst.n_nodes <= hi


@pytest.mark.parametrize("name", PACKAGES)
def test_garbage_actions_never_raise(name):
    game = _package(name).GAME(3)
    for k in range(game.N_TURNS + 3):  # also past the end
        action = GARBAGE[k % len(GARBAGE)]
        game.step([action, GARBAGE[(k * 7 + 3) % len(GARBAGE)]])
        obs = game.observe(0)
        assert isinstance(obs["action_format_correct_last_turn"], bool)
    assert len(game.scores()) == 2


@pytest.mark.parametrize("name", PACKAGES)
def test_none_is_malformed_noop(name):
    game_cls = _package(name).GAME
    game = game_cls(1)
    before = game.observe(0)
    game.step([None, None])
    after = game.observe(0)
    assert after["action_format_correct_last_turn"] is False
    assert after["n_invalid_actions_last_turn"] == 0
    assert after["player_cumulative_profit"] == 0.0
    key = "player_units" if "player_units" in before else "player_drones"
    assert after[key] == before[key]


@pytest.mark.parametrize("name", PACKAGES)
def test_idle_scores_zero(name):
    package = _package(name)
    do_nothing = next(b for b in package.BASELINES if b.__name__ == "DoNothing")
    game = package.GAME(4)
    _play(game, [do_nothing(0), do_nothing(1)])
    assert game.scores() == [0.0, 0.0]


@pytest.mark.parametrize("name", PACKAGES)
def test_baselines_play_legally(name):
    package = _package(name)
    for baseline in package.BASELINES:
        for seed in range(3):
            game = package.GAME(seed)
            seen = _play(game, [baseline(0), baseline(1)])
            reports = seen[2:]  # turn 0 reports nothing
            assert all(o["action_format_correct_last_turn"] for o in reports), baseline
            assert all(o["n_invalid_actions_last_turn"] == 0 for o in reports), baseline


@pytest.mark.parametrize("name", PACKAGES)
def test_greedy_beats_idle_and_random(name):
    package = _package(name)
    by_name = {b.__name__: b for b in package.BASELINES}
    greedy = next(b for n, b in by_name.items() if n.startswith("Greedy"))
    for opponent in (by_name["DoNothing"], by_name["RandomStrategy"]):
        wins = 0
        for seed in range(4):
            for seat in range(2):
                game = package.GAME(seed)
                players = [opponent(0), opponent(1)]
                players[seat] = greedy(seat)
                _play(game, players)
                wins += game.scores()[seat] > game.scores()[1 - seat]
        assert wins >= 6, (opponent.__name__, wins)
