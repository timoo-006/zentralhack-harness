"""Tests for Theta: the spec's worked example and constants must match game.py exactly, and the
game must be robust and deterministic like the dev games.

    uv run pytest -q testgames/theta/test_game.py
"""

import ast
import random
import re
from pathlib import Path

import pytest

import testgames.theta as package
from testgames.theta import game as module
from testgames.theta.baselines import DoNothing, GreedyScout, RandomStrategy

SPEC = Path(package.SPEC).read_text(encoding="utf-8")
EXAMPLE_SEED = 44
UNIT_NAMES = ["heavy", "light", "heavy", "light", "Heavy", 0]

GARBAGE = [
    None,
    (),
    0,
    1.5,
    "moves",
    True,
    {},
    [None],
    [(8, 11, "heavy")],
    [[8, 11, "heavy", 1]],
    [(8, 11, "Heavy", 1)],
    [(8, 11, 0, 1)],
    [(8, 11, "light", True)],
    [(8, 11, "light", 1.0)],
    [(8.0, 11, "light", 1)],
    [(10**30, -(10**30), "light", 10**30), (-1, 0, "heavy", 1)],
    [(0, 0, "light", 1), (0, 1, "heavy", -5)],
    ((0, 1, "light", 1),),
]


def _python_blocks(spec: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", spec, flags=re.DOTALL)


def _example_observations(spec: str) -> list[dict]:
    found = []
    for block in _python_blocks(spec):
        try:
            value = ast.literal_eval(block.strip())
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, dict) and "turn" in value:
            found.append(value)
    return found


def _example_actions(spec: str) -> list:
    plays = re.findall(r"- Player (\d) plays `(.*?)`\.", spec)
    assert [p for p, _ in plays] == ["0", "1"]
    return [ast.literal_eval(a) for _, a in plays]


def _play(game, strategies) -> list[dict]:
    seen = []
    while not game.is_over():
        observations = [game.observe(p) for p in range(2)]
        seen.extend(observations)
        game.step([s.act(o) for s, o in zip(strategies, observations, strict=True)])
    return seen


def test_package_exports():
    assert Path(package.SPEC).is_file()
    assert package.GAME is module.ThetaGame
    assert package.GAME.n_players == 2
    assert [b.__name__ for b in package.BASELINES] == ["DoNothing", "RandomStrategy", "GreedyScout"]


def test_worked_example_matches_game():
    examples = _example_observations(SPEC)
    assert len(examples) == 2
    actions = _example_actions(SPEC)
    game = module.ThetaGame(EXAMPLE_SEED)
    assert game.observe(0) == examples[0]
    game.step(actions)
    assert game.observe(0) == examples[1]
    assert game.profit_last_turn == pytest.approx([11.7, 1.0])
    # Claims made in the example's prose.
    inst = game.instance
    assert inst.base_demand[0] == 2.2 and inst.base_demand[11] == 3.9
    assert inst.demand[1][7] == 9.6 and inst.surge_left[1][7] == 3
    assert inst.base_demand[10] == 9.8 and inst.base_demand[4] == 7.7
    p1 = module.ThetaGame(EXAMPLE_SEED).observe(1)
    assert [v for v, d in enumerate(p1["demand"]) if d is not None] == [6, 9]


def test_spec_constants_match_game():
    block = next(b for b in _python_blocks(SPEC) if "N_TURNS =" in b)
    constants = {}
    for node in ast.parse(block).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            constants[node.targets[0].id] = ast.literal_eval(node.value)
    assert len(constants) == 14
    for key, value in constants.items():
        assert getattr(module, key) == value, key


@pytest.mark.parametrize("seed", range(6))
def test_deterministic_per_seed(seed):
    a, b = module.ThetaGame(seed), module.ThetaGame(seed)
    assert a.instance == b.instance
    seen_a = _play(a, [RandomStrategy(0), GreedyScout(1)])
    seen_b = _play(b, [RandomStrategy(0), GreedyScout(1)])
    assert seen_a == seen_b
    assert a.scores() == b.scores()
    assert all(ast.literal_eval(repr(o)) == o for o in seen_a[:4])


def test_seeds_differ():
    instances = [module.generate_instance(s) for s in range(10)]
    assert len({(i.n_nodes, tuple(i.edges), tuple(i.base_demand)) for i in instances}) == 10


def test_map_and_demand_constraints():
    for seed in range(50):
        inst = module.generate_instance(seed)
        dist = module.hop_distances(inst.n_nodes, inst.edges)
        assert all(d >= 0 for row in dist for d in row)
        assert dist[inst.bases[0]][inst.bases[1]] >= module.MIN_BASE_DISTANCE
        assert inst.edges == sorted(set(inst.edges)) and all(u < v for u, v in inst.edges)
        assert 12 <= inst.n_nodes <= 16
        assert len(inst.edges) == inst.n_nodes - 1 + round(0.4 * inst.n_nodes)
        for b in inst.bases:
            assert inst.base_demand[b] == 0.0
            assert all(row[b] == 0.0 for row in inst.demand)
        prev = [0] * inst.n_nodes
        for t in range(module.N_TURNS):
            for v in range(inst.n_nodes):
                left = inst.surge_left[t][v]
                assert left == 3 or left == max(prev[v] - 1, 0)
                expected = round(3 * inst.base_demand[v], 1) if left else inst.base_demand[v]
                assert inst.demand[t][v] == expected
            prev = inst.surge_left[t]


def test_split_demand():
    assert module.split_demand(10.0, 8, 6) == (8, 2.0)
    assert module.split_demand(10.0, 8, 9) == (1.0, 9)
    assert module.split_demand(9.0, 9, 9) == (4.5, 4.5)
    assert module.split_demand(23.4, 8, 8) == (8, 8)
    assert module.split_demand(3.9, 3, 0) == (3, 0)
    assert module.split_demand(0.0, 8, 0) == (0.0, 0.0)


def test_garbage_actions_never_raise():
    game = module.ThetaGame(3)
    for k in range(module.N_TURNS + 3):  # also past the end
        game.step([GARBAGE[k % len(GARBAGE)], GARBAGE[(k * 7 + 3) % len(GARBAGE)]])
        obs = game.observe(0)
        assert isinstance(obs["action_format_correct_last_turn"], bool)
    assert game.scores() == [0.0, 0.0]


def _junk(rng: random.Random, n: int, depth: int = 0):
    """A random value that is sometimes a (possibly illegal) well-formed move or action."""
    r = rng.random()
    if r < 0.35:
        return (
            rng.randint(-2, n + 2),
            rng.randint(-2, n + 2),
            rng.choice(UNIT_NAMES),
            rng.choice([-3, 0, 1, 5, 25, 10**30]),
        )
    if r < 0.5 and depth < 2:
        return [_junk(rng, n, depth + 1) for _ in range(rng.randint(0, 6))]
    if r < 0.6 and depth < 2:
        return tuple(_junk(rng, n, depth + 1) for _ in range(rng.randint(0, 5)))
    return rng.choice(
        [None, True, -(10**40), 2.5, float("nan"), "light", b"heavy", {}, {"a": 1}, {1}, object()]
    )


@pytest.mark.parametrize("seed", range(20))
def test_fuzzed_actions_never_raise(seed):
    rng = random.Random(seed)
    game = module.ThetaGame(seed)
    for _ in range(module.N_TURNS + 2):
        for p in range(2):
            obs = game.observe(p)
            assert ast.literal_eval(repr(obs)) == obs
        n = game.n_nodes
        game.step([_junk(rng, n), [_junk(rng, n) for _ in range(rng.randint(0, 3))]])
        for fleet in game.units:
            for unit_type, row in fleet.items():
                assert min(row) >= 0 and sum(row) == module.UNITS_PER_PLAYER[unit_type]


def test_none_is_malformed_noop():
    game = module.ThetaGame(1)
    before = game.observe(0)
    game.step([None, None])
    after = game.observe(0)
    assert after["action_format_correct_last_turn"] is False
    assert after["n_invalid_actions_last_turn"] == 0
    assert after["player_cumulative_profit"] == 0.0
    assert after["player_heavy"] == before["player_heavy"]
    assert after["player_light"] == before["player_light"]


def test_illegal_moves_are_counted_and_skipped():
    game = module.ThetaGame(EXAMPLE_SEED)  # base 8, neighbours 0, 5, 11
    game.step(
        [[(8, 0, "light", 5), (0, 3, "light", 5), (8, 0, "heavy", 11), (8, 8, "heavy", 1)], []]
    )
    obs = game.observe(0)
    assert obs["action_format_correct_last_turn"] is True
    assert obs["n_invalid_actions_last_turn"] == 3
    assert obs["player_light"][0] == 5 and obs["player_light"][3] == 0
    assert obs["player_heavy"][8] == 10


def test_idle_scores_zero():
    game = module.ThetaGame(4)
    _play(game, [DoNothing(0), DoNothing(1)])
    assert game.scores() == [0.0, 0.0]


@pytest.mark.parametrize("baseline", package.BASELINES)
def test_baselines_play_legally(baseline):
    for seed in range(3):
        game = module.ThetaGame(seed)
        seen = _play(game, [baseline(0), baseline(1)])
        reports = seen[2:]
        assert all(o["action_format_correct_last_turn"] for o in reports)
        assert all(o["n_invalid_actions_last_turn"] == 0 for o in reports)


def test_greedy_beats_idle_and_random():
    for opponent in (DoNothing, RandomStrategy):
        wins = 0
        for seed in range(4):
            for seat in range(2):
                game = module.ThetaGame(seed)
                players = [opponent(0), opponent(1)]
                players[seat] = GreedyScout(seat)
                _play(game, players)
                wins += game.scores()[seat] > game.scores()[1 - seat]
        assert wins >= 7, (opponent.__name__, wins)
