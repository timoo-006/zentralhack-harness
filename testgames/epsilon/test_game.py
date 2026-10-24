"""Tests for Epsilon: the spec's worked example and constants must match game.py exactly, and
the game must be robust and deterministic like the dev games.

    uv run pytest -q testgames/epsilon/test_game.py
"""

import ast
import re
from pathlib import Path

from testgames.epsilon import BASELINES, GAME, SPEC
from testgames.epsilon import game as game_module
from testgames.epsilon.baselines import DoNothing, GreedyCapture, RandomStrategy

SPEC_TEXT = Path(SPEC).read_text(encoding="utf-8")

# The worked example: seed, then (player 0's action, player 1's action) for turns 0, 1, 2.
EXAMPLE_SEED = 7
EXAMPLE_ACTIONS = [
    ([(3, 4, 2), (3, 8, 1)], [(6, 9, 3)]),
    ([], [(9, 4, 2)]),
    ([], []),
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
    [(3, 4, 2), (3, 4, 30)],
    [(6, 9, 1), (9, 6, 1)],
]


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


def test_package_exports():
    assert Path(SPEC).is_file()
    assert GAME.n_players == 2
    assert [b.__name__ for b in BASELINES] == ["DoNothing", "RandomStrategy", "GreedyCapture"]


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


def test_worked_example_player_1_view_is_swapped():
    game = GAME(EXAMPLE_SEED)
    for actions in EXAMPLE_ACTIONS[:2]:
        game.step(list(actions))
    o0, o1 = game.observe(0), game.observe(1)
    for key, value in o0.items():
        if key.startswith("player_"):
            assert o1["opponent_" + key[len("player_") :]] == value, key
        elif not key.startswith("opponent_"):
            assert o1[key] == value, key


def test_worked_example_continuation_table():
    """The table of turns 2 and 3 in the spec, field by field."""
    p0, p1 = EXAMPLE_ACTIONS[1]
    assert f"player 0 plays `{p0!r}` and player 1 plays `{p1!r}`" in SPEC_TEXT
    game = GAME(EXAMPLE_SEED)
    game.step(list(EXAMPLE_ACTIONS[0]))
    expected = {
        2: {
            "player_units": [0, 0, 0, 17, 2, 0, 0, 0, 0, 0],
            "opponent_units": [0, 0, 0, 0, 0, 0, 17, 0, 0, 1],
            "player_transits": [(3, 8, 1, 3)],
            "opponent_transits": [(9, 4, 2, 3)],
            "player_lead_turns": [0, 0, 0, 0, 2, 0, 0, 0, 0, 0],
            "opponent_lead_turns": [0, 0, 0, 0, 0, 0, 0, 0, 0, 2],
            "player_profit_last_turn": -1.0,
            "opponent_profit_last_turn": -5.0,
            "player_cumulative_profit": -7.0,
            "opponent_cumulative_profit": -11.0,
        },
        3: {
            "player_units": [0, 0, 0, 17, 2, 0, 0, 0, 1, 0],
            "opponent_units": [0, 0, 0, 0, 2, 0, 17, 0, 0, 1],
            "player_transits": [],
            "opponent_transits": [],
            "player_lead_turns": [0, 0, 0, 0, 0, 0, 0, 0, 1, 0],
            "opponent_lead_turns": [0, 0, 0, 0, 0, 0, 0, 0, 0, 3],
            "player_profit_last_turn": -1.0,
            "opponent_profit_last_turn": -5.0,
            "player_cumulative_profit": -8.0,
            "opponent_cumulative_profit": -16.0,
        },
    }
    for turn, actions in ((2, EXAMPLE_ACTIONS[1]), (3, EXAMPLE_ACTIONS[2])):
        game.step(list(actions))
        obs = game.observe(0)
        assert obs["turn"] == turn
        for key, value in expected[turn].items():
            assert obs[key] == value, (turn, key)
            assert f"`{value!r}`" in SPEC_TEXT, (turn, key)  # the table shows this value
        assert [i for i, o in enumerate(obs["player_owned"]) if o] == [4]
        assert [i for i, o in enumerate(obs["opponent_owned"]) if o] == [9]


def test_deterministic_per_seed():
    for seed in range(6):
        a, b = GAME(seed), GAME(seed)
        assert a.instance == b.instance
        for p in range(2):
            obs = a.observe(p)
            assert obs == b.observe(p)
            assert ast.literal_eval(repr(obs)) == obs
        seen_a = _play(a, [RandomStrategy(0), GreedyCapture(1)])
        seen_b = _play(b, [RandomStrategy(0), GreedyCapture(1)])
        assert seen_a == seen_b
        assert a.scores() == b.scores()
    assert len({repr(game_module.generate_instance(s)) for s in range(20)}) == 20


def test_map_constraints():
    for seed in range(100):
        inst = game_module.generate_instance(seed)
        lo, hi = game_module.N_NODES_RANGE
        assert lo <= inst.n_nodes <= hi
        assert inst.edges == sorted(set(inst.edges))
        assert all(0 <= u < v < inst.n_nodes for u, v in inst.edges)
        assert len(inst.lengths) == len(inst.edges)
        assert all(1 <= length <= 3 for length in inst.lengths)
        hops = game_module.hop_distances(inst.n_nodes, inst.edges)
        assert all(d >= 0 for row in hops for d in row)  # connected
        assert hops[inst.bases[0]][inst.bases[1]] >= game_module.MIN_BASE_DISTANCE
        assert all(inst.values[b] == 0 for b in inst.bases)
        others = [v for i, v in enumerate(inst.values) if i not in inst.bases]
        assert all(1 <= v <= 6 for v in others)


def test_travel_time_uses_lengths():
    edges = [(0, 1, 3), (0, 2, 1), (1, 2, 1)]
    dist = game_module.travel_distances(3, edges)
    assert dist[0][1] == 2  # via node 2, not the direct edge of length 3


def test_garbage_actions_never_raise():
    game = GAME(3)
    for k in range(game.N_TURNS + 3):  # also past the end
        game.step([GARBAGE[k % len(GARBAGE)], GARBAGE[(k * 7 + 3) % len(GARBAGE)]])
        obs = game.observe(0)
        assert isinstance(obs["action_format_correct_last_turn"], bool)
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
    game = GAME(EXAMPLE_SEED)  # base 3; edges (3, 4, 1), (4, 8, 1); 3 and 6 not adjacent
    moves = [(3, 4, 5), (4, 8, 5), (3, 6, 1), (3, 4, -1), (3, 4, 16), (3, 4, 0), (3, 6, 0)]
    game.step([moves, []])
    obs = game.observe(0)
    assert obs["action_format_correct_last_turn"] is True
    assert obs["n_invalid_actions_last_turn"] == 5  # (3, 4, 0) is legal, (3, 6, 0) is not
    assert obs["player_units"][3] == 15 and obs["player_units"][4] == 5


def test_units_conserved_and_lead_counts_exclusive():
    for seed in range(3):
        game = GAME(seed)
        for obs in _play(game, [RandomStrategy(0), GreedyCapture(1)]):
            for side in ("player", "opponent"):
                in_transit = sum(t[2] for t in obs[f"{side}_transits"])
                assert sum(obs[f"{side}_units"]) + in_transit == game.UNITS_PER_PLAYER
                assert all(t[3] > obs["turn"] for t in obs[f"{side}_transits"])
            for a, b in zip(obs["player_lead_turns"], obs["opponent_lead_turns"], strict=True):
                assert a == 0 or b == 0
            for a, b in zip(obs["player_owned"], obs["opponent_owned"], strict=True):
                assert not (a and b)


def test_idle_scores_zero():
    game = GAME(4)
    _play(game, [DoNothing(0), DoNothing(1)])
    assert game.scores() == [0.0, 0.0]


def test_baselines_play_legally():
    for baseline in BASELINES:
        for seed in range(3):
            game = GAME(seed)
            seen = _play(game, [baseline(0), baseline(1)])
            reports = seen[2:]  # turn 0 reports nothing
            assert all(o["action_format_correct_last_turn"] for o in reports), baseline
            assert all(o["n_invalid_actions_last_turn"] == 0 for o in reports), baseline


def test_greedy_beats_idle_and_random():
    for opponent in (DoNothing, RandomStrategy):
        wins = 0
        for seed in range(4):
            for seat in range(2):
                game = GAME(seed)
                players = [opponent(0), opponent(1)]
                players[seat] = GreedyCapture(seat)
                _play(game, players)
                wins += game.scores()[seat] > game.scores()[1 - seat]
        assert wins >= 7, (opponent.__name__, wins)


def test_careless_strategy_loses_money():
    """Random keeps most units away from home: it scores below DoNothing on most seeds."""
    below = 0
    for seed in range(10):
        game = GAME(seed)
        _play(game, [RandomStrategy(0), DoNothing(1)])
        below += game.scores()[0] < 0
    assert below >= 5
