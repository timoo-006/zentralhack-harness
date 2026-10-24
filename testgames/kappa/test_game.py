"""Tests for Kappa: the spec's worked example and constants must match game.py exactly, and the
game must be robust and deterministic like the dev games.

    PYTHONPATH=. python -m pytest -q testgames/kappa/test_game.py
"""

import ast
import random
import re
from pathlib import Path

from testgames.kappa import BASELINES, GAME, SPEC
from testgames.kappa import game as game_module
from testgames.kappa.baselines import DoNothing, GreedyMajority, RandomStrategy

SPEC_TEXT = Path(SPEC).read_text(encoding="utf-8")

EXAMPLE_SEED = 2
EXAMPLE_ACTIONS = [
    ({"moves": [[0, 4, 3], [0, 5, 2], [0, 5, 1]]}, {"moves": [[1, 9, 3], [9, 3, 1]]}),
    ({"moves": [[5, 9, 2]]}, {"moves": [[9, 5, 1]]}),
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
    {"moves": None},
    {"moves": ()},
    {"moves": [(0, 4, 1)]},
    {"moves": [[0, 4]]},
    {"moves": [[0, 4, 1, 0]]},
    {"moves": [[True, 4, 1]]},
    {"moves": [[0, 4, 1.0]]},
    {"moves": [], "x": 1},
    {"Moves": []},
    {1: []},
    [[0, 4, 1]],
    {"moves": [[10**30, -(10**30), 10**30]]},
    {"moves": [[0, 0, 1], [0, 4, -1], [0, 4, 10**30], [-1, 0, 1]]},
    {"moves": [[0, 4, 3], [4, 0, 1], [0, 4, 1]]},
    {"moves": [[0, 4, 0], [0, 9, 0]]},
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
    assert [b.__name__ for b in BASELINES] == ["DoNothing", "RandomStrategy", "GreedyMajority"]


def test_spec_constants_match_game():
    constants = _spec_constants(SPEC_TEXT)
    assert len(constants) == 8
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


def test_worked_example_player_1_view_is_swapped():
    game = GAME(EXAMPLE_SEED)
    game.step(list(EXAMPLE_ACTIONS[0]))
    o0, o1 = game.observe(0), game.observe(1)
    for key, value in o0.items():
        if key.startswith("player_"):
            assert o1["opponent_" + key[len("player_") :]] == value, key
        elif not key.startswith("opponent_"):
            assert o1[key] == value, key


def test_worked_example_continuation_table():
    p0, p1 = EXAMPLE_ACTIONS[1]
    assert f"player 0 plays `{p0!r}` and player 1 plays `{p1!r}`" in SPEC_TEXT
    game = GAME(EXAMPLE_SEED)
    for actions in EXAMPLE_ACTIONS:
        game.step(list(actions))
    obs = game.observe(0)
    expected = {
        "player_units": [10, 0, 0, 0, 3, 0, 0, 0, 0, 2],
        "opponent_units": [0, 12, 0, 0, 0, 1, 0, 0, 0, 2],
        "player_profit_last_turn": 3.0,
        "opponent_profit_last_turn": 5.5,
        "player_cumulative_profit": 10.5,
        "opponent_cumulative_profit": 7.0,
        "n_invalid_actions_last_turn": 0,
    }
    assert obs["turn"] == 2
    for key, value in expected.items():
        assert obs[key] == value, key
        assert f"`{value!r}`" in SPEC_TEXT, key


def test_deterministic_per_seed():
    for seed in range(6):
        a, b = GAME(seed), GAME(seed)
        assert a.instance == b.instance
        seen_a = _play(a, [RandomStrategy(0), GreedyMajority(1)])
        seen_b = _play(b, [RandomStrategy(0), GreedyMajority(1)])
        assert seen_a == seen_b
        assert a.scores() == b.scores()
        assert all(ast.literal_eval(repr(o)) == o for o in seen_a)
    assert len({repr(game_module.generate_instance(s)) for s in range(20)}) == 20


def test_map_constraints():
    for seed in range(100):
        inst = game_module.generate_instance(seed)
        lo, hi = game_module.N_NODES_RANGE
        assert lo <= inst.n_nodes <= hi
        assert inst.edges == sorted(set(inst.edges))
        assert all(0 <= u < v < inst.n_nodes for u, v in inst.edges)
        assert len(inst.edges) == inst.n_nodes - 1 + round(0.4 * inst.n_nodes)
        assert len(inst.capacities) == len(inst.edges)
        assert all(1 <= c <= 4 for c in inst.capacities)
        hops = game_module.hop_distances(inst.n_nodes, inst.edges)
        assert all(d >= 0 for row in hops for d in row)
        assert hops[inst.bases[0]][inst.bases[1]] >= game_module.MIN_BASE_DISTANCE
        assert all(inst.demand[b] == 0 for b in inst.bases)
        assert all(1 <= d <= 6 for i, d in enumerate(inst.demand) if i not in inst.bases)


def test_garbage_actions_never_raise():
    game = GAME(EXAMPLE_SEED)
    for k in range(game.N_TURNS + 3):
        game.step([GARBAGE[k % len(GARBAGE)], GARBAGE[(k * 7 + 3) % len(GARBAGE)]])
        obs = game.observe(0)
        assert isinstance(obs["action_format_correct_last_turn"], bool)
        assert ast.literal_eval(repr(obs)) == obs
        for side in ("player", "opponent"):
            assert sum(obs[f"{side}_units"]) == game.UNITS_PER_PLAYER
            assert min(obs[f"{side}_units"]) >= 0
    assert len(game.scores()) == 2


def test_random_garbage_fuzz():
    pool = [None, 0, -1, 1, 2, 3, 15, 10**9, True, 1.0, "a", [], (), {}, [0], [0, 1, 2]]
    for seed in range(5):
        rng = random.Random(seed)
        game = GAME(seed)
        while not game.is_over():
            actions = []
            for _ in range(2):
                r = rng.random()
                if r < 0.3:
                    actions.append(rng.choice(pool))
                else:
                    n = game.instance.n_nodes
                    moves = [
                        [rng.randrange(-1, n + 1), rng.randrange(-1, n + 1), rng.randrange(-1, 6)]
                        if rng.random() < 0.8
                        else rng.choice(pool)
                        for _ in range(rng.randrange(6))
                    ]
                    actions.append({"moves": moves})
            game.step(actions)
            for p in range(2):
                obs = game.observe(p)
                assert ast.literal_eval(repr(obs)) == obs
                assert sum(obs["player_units"]) == game.UNITS_PER_PLAYER


def test_none_is_malformed_noop():
    game = GAME(1)
    before = game.observe(0)
    game.step([None, None])
    after = game.observe(0)
    assert after["action_format_correct_last_turn"] is False
    assert after["n_invalid_actions_last_turn"] == 0
    assert after["player_cumulative_profit"] == 0.0
    assert after["player_units"] == before["player_units"]


def test_capacity_and_illegal_moves():
    game = GAME(EXAMPLE_SEED)  # base 0; edge (0, 4) capacity 3, (0, 5) capacity 2; 0-9 no edge
    moves = [
        [0, 4, 2],
        [0, 4, 2],
        [0, 4, 1],
        [0, 5, 3],
        [0, 9, 1],
        [0, 4, -1],
        [4, 2, 1],
        [0, 5, 0],
        [0, 9, 0],
    ]
    game.step([{"moves": moves}, {"moves": []}])
    obs = game.observe(0)
    assert obs["action_format_correct_last_turn"] is True
    # illegal: 2nd (capacity), 4th (capacity), 5th (no edge), 6th (negative), 7th (no units at
    # start), 9th (no edge); [0, 5, 0] is legal
    assert obs["n_invalid_actions_last_turn"] == 6
    assert obs["player_units"][0] == 12 and obs["player_units"][4] == 3
    assert obs["player_profit_last_turn"] == 4 - 0.5 * 3


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
                players[seat] = GreedyMajority(seat)
                _play(game, players)
                wins += game.scores()[seat] > game.scores()[1 - seat]
        assert wins >= 7, (opponent.__name__, wins)
