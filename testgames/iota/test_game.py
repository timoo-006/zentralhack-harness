"""Tests for Iota: the spec's worked example and constants must match game.py exactly, and the
game must be robust and deterministic like the dev games.

    uv run pytest -q testgames/iota/test_game.py
"""

import ast
import random
import re
from pathlib import Path

from testgames.iota import BASELINES, GAME, SPEC
from testgames.iota import game as game_module
from testgames.iota.baselines import DoNothing, GreedyDispatch, RandomStrategy

SPEC_TEXT = Path(SPEC).read_text(encoding="utf-8")

EXAMPLE_SEED = 16
EXAMPLE_ACTIONS = [
    ([(3, 6, 1), (3, 7, 2)], [(5, 8, 2), (8, 6, 2)]),
    ([(6, 3, 1), (7, 0, 1)], [(8, 6, 1)]),
]

GARBAGE = [
    None,
    [],
    (),
    0,
    1.5,
    "moves",
    True,
    {},
    [[0, 1, 5]],
    [(0, 1)],
    [(0, 1, 5, 0)],
    [(True, 1, 1)],
    [(0, 1, 1.0)],
    [(10**30, -(10**30), 10**30), (-1, 0, 1)],
    [(0, 0, 1), (0, 1, -1), (0, 1, 10**30)],
    ((0, 1, 1),),
    [(3, 6, 2), (3, 6, 30)],
    [(5, 8, 1), (8, 5, 1)],
    [None],
    [(0, 1, None)],
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


def _spec_constants(spec: str) -> dict:
    block = next(b for b in _python_blocks(spec) if "N_TURNS =" in b)
    constants = {}
    for node in ast.parse(block).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            constants[node.targets[0].id] = ast.literal_eval(node.value)
    return constants


def _play(game, strategies) -> list[dict]:
    seen = []
    while not game.is_over():
        observations = [game.observe(p) for p in range(2)]
        seen.extend(observations)
        game.step([s.act(o) for s, o in zip(strategies, observations, strict=True)])
    return seen


def test_package_exports():
    assert Path(SPEC).is_file()
    assert GAME.n_players == 2
    assert [b.__name__ for b in BASELINES] == ["DoNothing", "RandomStrategy", "GreedyDispatch"]


def test_spec_constants_match_game():
    constants = _spec_constants(SPEC_TEXT)
    assert len(constants) == 9
    for key, value in constants.items():
        assert getattr(game_module, key) == value, key


def test_worked_example_observations():
    p0, p1 = EXAMPLE_ACTIONS[0]
    assert f"Player 0 plays `{p0!r}`" in SPEC_TEXT
    assert f"Player 1 plays `{p1!r}`" in SPEC_TEXT
    examples = _example_observations(SPEC_TEXT)
    assert len(examples) == 2
    game = GAME(EXAMPLE_SEED)
    assert game.observe(0) == examples[0]
    game.step([p0, p1])
    assert game.observe(0) == examples[1]
    assert game.observe(1)["n_invalid_actions_last_turn"] == 1


def test_worked_example_player_1_view_is_swapped():
    game = GAME(EXAMPLE_SEED)
    game.step(list(EXAMPLE_ACTIONS[0]))
    o0, o1 = game.observe(0), game.observe(1)
    for key, value in o0.items():
        if key.startswith("player_"):
            assert o1["opponent_" + key[len("player_") :]] == value, key
        elif key == "n_invalid_actions_last_turn":
            continue
        elif not key.startswith("opponent_"):
            assert o1[key] == value, key


def test_worked_example_turn_2_table():
    p0, p1 = EXAMPLE_ACTIONS[1]
    assert f"player 0 plays `{p0!r}` and player 1 plays `{p1!r}`" in SPEC_TEXT
    game = GAME(EXAMPLE_SEED)
    for actions in EXAMPLE_ACTIONS:
        game.step(list(actions))
    obs = game.observe(0)
    assert obs["turn"] == 2
    expected = {
        "orders": [(6, 3, 2), (8, 9, 6)],
        "player_units": [1, 0, 0, 10, 0, 0, 0, 1, 0],
        "opponent_units": [0, 0, 0, 0, 0, 10, 1, 0, 1],
        "player_cumulative_profit": 18.0,
        "opponent_cumulative_profit": -4.0,
    }
    for key, value in expected.items():
        assert obs[key] == value, key
        assert f"`{value!r}`" in SPEC_TEXT, key
    assert obs["player_profit_last_turn"] == 9.0
    assert obs["opponent_profit_last_turn"] == -2.0


def test_tie_splits_and_deadline_expires():
    inst = game_module.Instance(
        n_nodes=5,
        edges=[(0, 1), (1, 2), (2, 3), (3, 4)],
        bases=[0, 4],
        orders=[[(2, 8, 2), (1, 5, 1)]] + [[] for _ in range(game_module.N_TURNS - 1)],
    )
    game = GAME(instance=inst)
    game.step([[(0, 1, 1)], [(4, 3, 1)]])  # P0 takes (1, 5, 1); (2, 8, 2) waits
    obs = game.observe(0)
    assert obs["orders"] == [(2, 8, 1)]
    assert obs["player_profit_last_turn"] == 4.0 and obs["opponent_profit_last_turn"] == -1.0
    game.step([[(1, 2, 1)], [(3, 2, 1)]])  # tie on node 2: 4.0 each
    obs = game.observe(0)
    assert obs["orders"] == []
    assert obs["player_profit_last_turn"] == 3.0 and obs["opponent_profit_last_turn"] == 3.0
    game2 = GAME(instance=inst)
    game2.step([[], []])
    assert game2.observe(0)["orders"] == [(2, 8, 1)]  # (1, 5, 1) expired
    game2.step([[], []])
    assert game2.observe(0)["orders"] == []


def test_deterministic_per_seed():
    for seed in range(6):
        a, b = GAME(seed), GAME(seed)
        assert a.instance == b.instance
        for p in range(2):
            obs = a.observe(p)
            assert obs == b.observe(p)
            assert ast.literal_eval(repr(obs)) == obs
        seen_a = _play(a, [RandomStrategy(0), GreedyDispatch(1)])
        seen_b = _play(b, [RandomStrategy(0), GreedyDispatch(1)])
        assert seen_a == seen_b
        assert a.scores() == b.scores()
    assert len({repr(game_module.generate_instance(s)) for s in range(20)}) == 20


def test_map_and_order_constraints():
    for seed in range(100):
        inst = game_module.generate_instance(seed)
        lo, hi = game_module.N_NODES_RANGE
        assert lo <= inst.n_nodes <= hi
        assert inst.edges == sorted(set(inst.edges))
        assert all(0 <= u < v < inst.n_nodes for u, v in inst.edges)
        assert len(inst.edges) == inst.n_nodes - 1 + round(0.4 * inst.n_nodes)
        hops = game_module.hop_distances(inst.n_nodes, inst.edges)
        assert all(d >= 0 for row in hops for d in row)
        assert hops[inst.bases[0]][inst.bases[1]] >= game_module.MIN_BASE_DISTANCE
        assert len(inst.orders) == game_module.N_TURNS
        for new in inst.orders:
            assert 0 <= len(new) <= 2
            for node, reward, deadline in new:
                assert node not in inst.bases and 0 <= node < inst.n_nodes
                assert 3 <= reward <= 12 and 2 <= deadline <= 6


def test_garbage_actions_never_raise():
    rng = random.Random(0)
    for seed in range(5):
        game = GAME(seed)
        for _ in range(game.N_TURNS + 3):
            pool = [
                *GARBAGE,
                [(rng.randrange(-2, 15), rng.randrange(-2, 15), rng.randrange(-3, 20))],
            ]
            game.step([rng.choice(pool), rng.choice(pool)])
            for p in range(2):
                obs = game.observe(p)
                assert ast.literal_eval(repr(obs)) == obs
                assert sum(obs["player_units"]) == game.UNITS_PER_PLAYER
                assert all(c >= 0 for c in obs["player_units"])
        assert len(game.scores()) == 2


def test_none_is_malformed_noop():
    game = GAME(1)
    before = game.observe(0)
    game.step([None, None])
    after = game.observe(0)
    assert after["action_format_correct_last_turn"] is False
    assert after["n_invalid_actions_last_turn"] == 0
    assert after["player_cumulative_profit"] == 0.0
    assert after["player_units"] == before["player_units"]


def test_illegal_moves_are_counted_and_skipped():
    game = GAME(EXAMPLE_SEED)  # base 3; (3, 6) and (6, 8) are edges; 3 and 8 are not adjacent
    moves = [(3, 6, 5), (6, 8, 5), (3, 8, 1), (3, 6, -1), (3, 6, 8), (3, 6, 0), (3, 8, 0)]
    game.step([moves, []])
    obs = game.observe(0)
    assert obs["action_format_correct_last_turn"] is True
    assert obs["n_invalid_actions_last_turn"] == 5
    assert obs["player_units"][3] == 7 and obs["player_units"][6] == 5


def test_idle_scores_zero():
    game = GAME(4)
    _play(game, [DoNothing(0), DoNothing(1)])
    assert game.scores() == [0.0, 0.0]


def test_baselines_play_legally():
    for baseline in BASELINES:
        for seed in range(3):
            game = GAME(seed)
            seen = _play(game, [baseline(0), baseline(1)])
            reports = seen[2:]
            assert all(o["action_format_correct_last_turn"] for o in reports), baseline
            assert all(o["n_invalid_actions_last_turn"] == 0 for o in reports), baseline


def test_greedy_beats_idle_and_random():
    for opponent in (DoNothing, RandomStrategy):
        wins = 0
        for seed in range(4):
            for seat in range(2):
                game = GAME(seed)
                players = [opponent(0), opponent(1)]
                players[seat] = GreedyDispatch(seat)
                _play(game, players)
                wins += game.scores()[seat] > game.scores()[1 - seat]
        assert wins >= 7, (opponent.__name__, wins)


def test_careless_strategy_loses_money():
    below = 0
    for seed in range(10):
        game = GAME(seed)
        _play(game, [RandomStrategy(0), DoNothing(1)])
        below += game.scores()[0] < 0
    assert below >= 5
