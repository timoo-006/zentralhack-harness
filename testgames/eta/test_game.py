"""Tests for Eta: spec.md's constants and worked examples must match game.py exactly, and the
game must be deterministic per seed and robust to any action.

    uv run pytest -q testgames/eta/test_game.py
"""

import ast
import random
import re
from pathlib import Path

import pytest

from testgames import eta
from testgames.eta import game as eta_game
from testgames.eta.baselines import DoNothing, RandomStrategy, SafeGreedy
from testgames.eta.game import EtaGame, hot_phase_for

SPEC = Path(eta.SPEC).read_text(encoding="utf-8")


def _python_blocks() -> list[str]:
    return re.findall(r"```python\n(.*?)```", SPEC, flags=re.DOTALL)


def _example_observations() -> list[dict]:
    found = []
    for block in _python_blocks():
        try:
            value = ast.literal_eval(block.strip())
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, dict) and "turn" in value:
            found.append(value)
    return found


def _example_actions() -> list:
    """The two actions of the worked example, from the "Player p plays `...`" lines."""
    return [ast.literal_eval(a) for a in re.findall(r"Player [01] plays `(.*?)`", SPEC)]


def _play(game, strategies) -> list[list[int]]:
    """Play to the end; returns the number of illegal moves per turn, per seat."""
    invalid = []
    while not game.is_over():
        obs = [game.observe(p) for p in range(2)]
        game.step([s.act(o) for s, o in zip(strategies, obs, strict=True)])
        invalid.append(list(game.n_invalid_actions_last_turn))
        assert game.action_format_correct_last_turn == [True, True]
    return invalid


def test_package_exports():
    assert eta.GAME is EtaGame
    assert Path(eta.SPEC).is_file()
    assert [b.__name__ for b in eta.BASELINES] == ["DoNothing", "RandomStrategy", "SafeGreedy"]


def test_spec_constants_match_game():
    block = next(b for b in _python_blocks() if "N_TURNS =" in b)
    constants = {}
    for node in ast.parse(block).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            constants[node.targets[0].id] = ast.literal_eval(node.value)
    assert len(constants) == 15
    for key, value in constants.items():
        assert getattr(eta_game, key) == value, key


def test_map_is_symmetric_and_matches_table():
    edges = set(eta_game.EDGES)
    assert eta_game.EDGES == sorted(edges)
    assert {(11 - v, 11 - u) for u, v in edges} == edges
    d = eta_game.NODE_DEMANDS
    assert all(d[i] == d[11 - i] for i in range(12))
    dist = eta_game.hop_distances(12, eta_game.EDGES)
    assert dist[0] == [0, 1, 1, 1, 2, 2, 2, 3, 4, 3, 4, 4]
    assert dist[11] == [4, 4, 3, 4, 3, 2, 2, 2, 1, 1, 1, 0]


def test_worked_example_matches_game():
    examples = _example_observations()
    actions = _example_actions()
    assert len(examples) == 2 and len(actions) == 2
    seed = next(s for s in range(100) if hot_phase_for(s) == 0)
    game = EtaGame(seed)
    assert game.observe(0) == examples[0]
    other = game.observe(1)
    assert other["player_base"] == 11 and other["opponent_units"] == [(0, 10)] * 16
    game.step(actions)
    assert game.observe(0) == examples[1]


def test_rover_trip_table():
    """The energy table in "Units": one rover to node 5 and back, on a match with phase 0."""
    rows = re.findall(r"^\| (\d+)\s+\| (\S+ → \S+|stay)\s+\| (\d+)\s+\| (\d+)\s+\| ([\d.]+)\s+\|$",
                      SPEC, flags=re.MULTILINE)  # fmt: skip
    assert len(rows) == 10
    game = EtaGame(hot_phase=0)
    for turn, move, node, energy, served in rows:
        assert int(turn) == game.turn
        action = [] if move == "stay" else [[0, int(move.split(" → ")[1])]]
        game.step([action, []])
        assert game.units[0][0] == [int(node), int(energy)]
        assert game.profit_last_turn[0] == pytest.approx(float(served))


def test_stranding_and_tow_fee():
    game = EtaGame(hot_phase=5)
    game.step([[[0, 2]], []])  # 8
    game.step([[[0, 6]], []])  # 6
    game.step([[[0, 7]], []])  # 4, distance 3: cannot get home any more
    for expected in (3, 2, 1):
        game.step([[], []])
        assert game.units[0][0] == [7, expected]
    game.step([[], []])  # strands
    assert game.units[0][0] == [7, 0]
    assert game.profit_last_turn[0] == -1.0
    game.step([[[0, 6]], []])  # stranded rovers cannot move
    assert game.n_invalid_actions_last_turn[0] == 1
    assert game.units[0][0] == [7, 0]
    assert game.profit_last_turn[0] == -1.0


def test_earlier_illegal_move_does_not_block_rover():
    game = EtaGame(hot_phase=0)
    game.step([[[0, 5], [0, 1]], [[0, 0], [0, 10]]])  # first move of each list is not adjacent
    assert game.n_invalid_actions_last_turn == [1, 1]
    assert game.units[0][0] == [1, 8]
    assert game.units[1][0] == [10, 8]


def test_opponent_base_drains_like_any_other_node():
    game = EtaGame(hot_phase=5)
    for to_node in (2, 6, 9, 11):  # 10 -> 8 -> 6 -> 4 -> 2 on player 1's base
        game.step([[[0, to_node]], []])
    assert game.units[0][0] == [11, 2]
    assert game.profit_last_turn[0] == 0.0  # bases have no demand


def test_hot_turns():
    game = EtaGame(hot_phase=3)
    hot = [t for t in range(100) if eta_game.demand_at(t, 3)[5] == 24]
    assert hot == list(range(3, 100, 10))
    assert game.observe(0)["demand"] == eta_game.NODE_DEMANDS


@pytest.mark.parametrize("seed", range(5))
def test_deterministic_per_seed(seed):
    scores = []
    for _ in range(2):
        game = EtaGame(seed)
        assert ast.literal_eval(repr(game.observe(0))) == game.observe(0)
        _play(game, [SafeGreedy(0), RandomStrategy(1)])
        scores.append(game.scores())
    assert scores[0] == scores[1]
    assert EtaGame(seed).hot_phase == hot_phase_for(seed)


GARBAGE = [
    None,
    (),
    0,
    1.5,
    "moves",
    True,
    {},
    {"moves": []},
    [(0, 1)],
    [[0, 1, 2]],
    [[0]],
    [[True, 1]],
    [[0, 1.0]],
    [[0, 1], (1, 1)],
    [[10**30, -(10**30)], [-1, 1], [0, 10**30], [16, 1], [0, 0], [0, 11]],
    [[0, 1]] * 1000,
]


def test_malformed_and_illegal_actions_never_raise():
    game = EtaGame(3)
    for k in range(game.N_TURNS + 3):  # also past the end
        game.step([GARBAGE[k % len(GARBAGE)], GARBAGE[(k * 7 + 3) % len(GARBAGE)]])
        assert isinstance(game.observe(0)["action_format_correct_last_turn"], bool)
    assert len(game.scores()) == 2


def _junk(rng: random.Random, depth: int = 0):
    """A random action-like value: garbage, near-misses and mostly-legal move lists."""
    k = rng.randrange(10)
    if k == 0 or depth > 2:
        return rng.choice([None, 0, -(10**40), 1.5, float("nan"), True, "x", b"", {}, ()])
    if k == 1:
        return tuple(_junk(rng, depth + 1) for _ in range(rng.randrange(4)))
    if k == 2:
        return [_junk(rng, depth + 1) for _ in range(rng.randrange(4))]
    if k == 3:
        return [[rng.randrange(16), rng.randrange(12), 1]]
    if k == 4:
        return [(rng.randrange(16), rng.randrange(12))]
    return [[rng.randrange(-3, 20), rng.randrange(-3, 14)] for _ in range(rng.randrange(30))]


@pytest.mark.parametrize("seed", range(20))
def test_fuzzed_actions_never_raise_and_obs_are_literals(seed):
    rng = random.Random(seed)
    game = EtaGame(seed)
    while not game.is_over():
        for p in range(2):
            obs = game.observe(p)
            assert ast.literal_eval(repr(obs)) == obs
        game.step([_junk(rng), _junk(rng)])
    assert all(isinstance(s, float) for s in game.scores())


def test_tuple_moves_are_malformed_noop():
    game = EtaGame(1)
    before = game.observe(0)
    game.step([[(0, 1)], None])
    after = game.observe(0)
    assert after["action_format_correct_last_turn"] is False
    assert after["n_invalid_actions_last_turn"] == 0
    assert after["player_units"] == before["player_units"]
    assert game.observe(1)["action_format_correct_last_turn"] is False


def test_idle_scores_zero():
    game = EtaGame(4)
    _play(game, [DoNothing(0), DoNothing(1)])
    assert game.scores() == [0.0, 0.0]


@pytest.mark.parametrize("seat", [0, 1])
def test_baselines_play_legally_and_greedy_wins(seat):
    for seed in range(3):
        strategies = [None, None]
        strategies[seat], strategies[1 - seat] = SafeGreedy(seat), RandomStrategy(1 - seat)
        game = EtaGame(seed)
        invalid = _play(game, strategies)
        assert all(row == [0, 0] for row in invalid)
        assert all(energy > 0 for _, energy in game.units[seat])  # SafeGreedy never strands
        assert game.scores()[seat] > 0 > game.scores()[1 - seat]
