"""Tests for Zeta: the spec's worked example and constants must match game.py exactly, and the
game must be deterministic per seed and robust to any action.

    uv run pytest -q testgames/zeta/test_game.py
"""

import ast
import random
import re
from collections import OrderedDict, defaultdict
from pathlib import Path

from testgames import zeta
from testgames.zeta import game as zeta_game
from testgames.zeta.baselines import DoNothing, GreedyOrders, RandomStrategy
from testgames.zeta.game import ZetaGame, generate_instance, hop_distances

SPEC = Path(zeta.SPEC).read_text(encoding="utf-8")
EXAMPLE_SEED = 49

GARBAGE = [
    None,
    [],
    (),
    0,
    1.5,
    "x",
    True,
    {},
    {8: {}},
    {8: None},
    {8: [9, 1]},
    {"8": {9: 1}},
    {8: {"9": 1}},
    {8: {9: 1.0}},
    {8: {9: True}},
    {True: {9: 1}},
    {8.0: {9: 1}},
    [(8, 9, 1)],
    {8: {9: -1}, -1: {0: 1}, 10**30: {0: 1}},
    {8: {9: 10**30, 8: 1, 3: 1}},
    {0: {1: 0}, 1: {0: 0}},
]


def _python_blocks(spec: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", spec, flags=re.DOTALL)


def _example_observations() -> list[dict]:
    found = []
    for block in _python_blocks(SPEC):
        try:
            value = ast.literal_eval(block.strip())
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, dict) and "turn" in value:
            found.append(value)
    return found


def _example_actions() -> list[list[dict]]:
    """The actions the worked example plays, as [[p0, p1] per turn], parsed from the spec."""
    played = [ast.literal_eval(a) for a in re.findall(r"plays `(\{.*?\})`\.", SPEC)]
    assert len(played) % 2 == 0
    return [played[i : i + 2] for i in range(0, len(played), 2)]


def _play(game, strategies) -> list[dict]:
    seen = []
    while not game.is_over():
        observations = [game.observe(p) for p in range(2)]
        seen.extend(observations)
        game.step([s.act(o) for s, o in zip(strategies, observations, strict=True)])
    return seen


def test_worked_example_matches_game():
    examples = _example_observations()
    turns = _example_actions()
    assert len(examples) == 3 and len(turns) == 2
    game = ZetaGame(EXAMPLE_SEED)
    assert game.observe(0) == examples[0]
    p1 = game.observe(1)
    assert p1["you"] == examples[0]["rival"] and p1["rival"] == examples[0]["you"]
    for actions, expected in zip(turns, examples[1:], strict=True):
        game.step(actions)
        assert game.observe(0) == expected
    # Player 1's illegal move 5 -> 9 on turn 0 is reported on turn 1.
    replay = ZetaGame(EXAMPLE_SEED)
    replay.step(turns[0])
    assert replay.observe(1)["feedback"] == {"format_ok": True, "rejected": 1}


def test_spec_hop_distances():
    inst = generate_instance(EXAMPLE_SEED)
    dist = hop_distances(inst.n_nodes, inst.edges)
    assert f"`{dist[8]}`" in SPEC.replace("\n", " ")
    assert f"`{dist[5]}`" in SPEC.replace("\n", " ")


def test_spec_constants_match_game():
    block = next(b for b in _python_blocks(SPEC) if "N_TURNS =" in b)
    constants = {}
    for node in ast.parse(block).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            constants[node.targets[0].id] = ast.literal_eval(node.value)
    assert len(constants) >= 15
    for key, value in constants.items():
        assert getattr(zeta_game, key) == value, key


def test_deterministic_per_seed():
    for seed in range(4):
        runs = []
        for _ in range(2):
            game = ZetaGame(seed)
            seen = _play(game, [GreedyOrders(0), RandomStrategy(1)])
            runs.append((seen, game.scores()))
        assert runs[0] == runs[1]
        for obs in runs[0][0]:
            assert ast.literal_eval(repr(obs)) == obs
    assert generate_instance(0) != generate_instance(1)


def test_map_constraints():
    for seed in range(50):
        inst = generate_instance(seed)
        dist = hop_distances(inst.n_nodes, inst.edges)
        assert all(d >= 0 for row in dist for d in row)
        assert dist[inst.bases[0]][inst.bases[1]] >= zeta_game.MIN_BASE_DISTANCE
        assert inst.edges == sorted(set(inst.edges)) and all(u < v for u, v in inst.edges)
        assert zeta_game.N_NODES_RANGE[0] <= inst.n_nodes <= zeta_game.N_NODES_RANGE[1]
        assert all(row[b] == 0.0 for row in inst.orders for b in inst.bases)


def test_forecast_rows():
    game = ZetaGame(2)
    while not game.is_over():
        obs = game.observe(0)
        assert len(obs["orders"]) == min(3, zeta_game.N_TURNS - obs["turn"])
        assert all(len(row) == obs["graph"]["nodes"] for row in obs["orders"])
        game.step([{}, {}])


def test_garbage_actions_never_raise():
    game = ZetaGame(3)
    for k in range(game.N_TURNS + 3):  # also past the end
        game.step([GARBAGE[k % len(GARBAGE)], GARBAGE[(k * 7 + 3) % len(GARBAGE)]])
        assert isinstance(game.observe(0)["feedback"]["format_ok"], bool)
    assert len(game.scores()) == 2


def test_malformed_and_illegal_reporting():
    game = ZetaGame(EXAMPLE_SEED)
    before = game.observe(0)
    game.step([None, [(5, 6, 1)]])
    after = game.observe(0)
    assert after["feedback"] == {"format_ok": False, "rejected": 0}
    assert after["you"]["units"] == before["you"]["units"]
    assert after["you"]["score"] == 0.0
    game.step([{8: {9: 30, 2: 2, 6: 1, 8: 1}}, {5: {6: 0}}])
    obs = game.observe(0)
    assert obs["feedback"] == {"format_ok": True, "rejected": 3}  # 9: too many, 6: no link, 8: self
    assert obs["you"]["units"][2] == 2 and obs["you"]["units"][9] == 0
    assert game.observe(1)["feedback"] == {"format_ok": True, "rejected": 0}


def test_serve_limit_and_distance_upkeep():
    game = ZetaGame(EXAMPLE_SEED)
    game.step([{8: {9: 9}}, {}])  # 9 units at node 9 (1 hop): only 6 serve, all 9 pay 1.5
    orders = generate_instance(EXAMPLE_SEED).orders[0][9]
    assert abs(game.observe(0)["you"]["last_profit"] - (min(24, orders) - 9 * 1.5)) < 1e-9


def test_idle_scores_zero_and_random_loses():
    game = ZetaGame(4)
    _play(game, [DoNothing(0), DoNothing(1)])
    assert game.scores() == [0.0, 0.0]
    game = ZetaGame(4)
    _play(game, [RandomStrategy(0), DoNothing(1)])
    assert game.scores()[0] < 0


def test_baselines_play_legally():
    for baseline in zeta.BASELINES:
        for seed in range(3):
            seen = _play(ZetaGame(seed), [baseline(0), baseline(1)])
            assert all(o["feedback"] == {"format_ok": True, "rejected": 0} for o in seen[2:])


def test_greedy_beats_idle_and_random():
    for opponent in (DoNothing, RandomStrategy):
        wins = 0
        for seed in range(4):
            for seat in range(2):
                game = ZetaGame(seed)
                players = [opponent(0), opponent(1)]
                players[seat] = GreedyOrders(seat)
                _play(game, players)
                wins += game.scores()[seat] > game.scores()[1 - seat]
        assert wins >= 7, (opponent.__name__, wins)


class _RaisingInt(int):
    def __le__(self, other):
        raise RuntimeError("compared")

    __lt__ = __ge__ = __gt__ = __le__


class _RaisingDict(dict):
    def items(self):
        raise RuntimeError("iterated")


def test_subclasses_are_malformed_and_never_raise():
    defaults = defaultdict(dict)
    defaults[8][9] = 1
    for action in (
        defaults,
        OrderedDict({8: {9: 1}}),
        {8: OrderedDict({9: 1})},
        {_RaisingInt(8): {9: 1}},
        {8: {9: _RaisingInt(1)}},
        _RaisingDict({8: {9: 1}}),
        {8: _RaisingDict({9: 1})},
    ):
        game = ZetaGame(EXAMPLE_SEED)
        game.step([action, {}])
        obs = game.observe(0)
        assert obs["feedback"] == {"format_ok": False, "rejected": 0}, action
        assert obs["you"]["units"][8] == 24
    game = ZetaGame(EXAMPLE_SEED)
    game.step([{u: dict(t) for u, t in defaults.items()}, {}])  # the conversion the spec gives
    assert game.observe(0)["you"]["units"][9] == 1


def _junk(rng: random.Random, depth: int = 0):
    scalars = [None, 0, 1, -1, 3, 10**30, -(10**30), 1.5, 3.0, float("nan"), "8", True, (), []]
    if depth > 2 or rng.random() < 0.3:
        return rng.choice([*scalars, rng.randrange(-3, 25)])
    if rng.random() < 0.7:
        keys = [rng.randrange(-3, 22), 10**30, "x", 2.0, False, None]
        return {rng.choice(keys): _junk(rng, depth + 1) for _ in range(rng.randrange(4))}
    return [_junk(rng, depth + 1) for _ in range(rng.randrange(3))]


def test_fuzz_20_seeds_never_raises_and_conserves_units():
    for seed in range(20):
        rng = random.Random(seed)
        game = ZetaGame(seed)
        n = game.n_nodes
        while not game.is_over():
            actions = []
            for _ in range(2):
                if rng.random() < 0.5:
                    actions.append(_junk(rng))
                else:  # well-formed, mostly illegal: random ids and counts
                    actions.append(
                        {
                            rng.randrange(-2, n + 2): {
                                rng.randrange(-2, n + 2): rng.randrange(-3, 30) for _ in range(3)
                            }
                            for _ in range(3)
                        }
                    )
            game.step(actions)
            for p in range(2):
                obs = game.observe(p)
                assert ast.literal_eval(repr(obs)) == obs
                assert sum(obs["you"]["units"]) == 24 and min(obs["you"]["units"]) >= 0
