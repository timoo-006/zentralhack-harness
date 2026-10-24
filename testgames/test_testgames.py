"""Tests for our own test games: the specs' worked examples and constants must match game.py
exactly, and the games must be robust and deterministic like the dev games.

    uv run pytest -q testgames/test_testgames.py
"""

import ast
import importlib
import itertools
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


# ---------------------------------------------------------------- adversarial review tests


def _mutate(value):
    """Scribble over every mutable container inside an observation (an in-process strategy may)."""
    if isinstance(value, list):
        for item in value:
            _mutate(item)
        value.clear()
    elif isinstance(value, dict):
        for item in list(value.values()):
            _mutate(item)
        value.clear()


@pytest.mark.parametrize("name", PACKAGES)
def test_observation_mutation_does_not_leak(name):
    package = _package(name)
    random_cls = next(b for b in package.BASELINES if b.__name__ == "RandomStrategy")
    game = package.GAME(5)
    players = [random_cls(0), random_cls(1)]
    for _ in range(10):
        clean = [game.observe(p) for p in range(2)]
        actions = [players[p].act(game.observe(p)) for p in range(2)]
        for p in range(2):
            _mutate(game.observe(p))
        assert [game.observe(p) for p in range(2)] == clean
        game.step(actions)


def _fuzz_matches(package, seeds):
    """Random baselines with every 7th action replaced by garbage; yields (game, obs list)."""
    random_cls = next(b for b in package.BASELINES if b.__name__ == "RandomStrategy")
    for seed in seeds:
        game = package.GAME(seed)
        players = [random_cls(0), random_cls(1)]
        history = []
        while not game.is_over():
            observations = [game.observe(p) for p in range(2)]
            history.append(observations)
            actions = [players[p].act(observations[p]) for p in range(2)]
            if game.turn % 7 == 3:
                actions[game.turn % 2] = GARBAGE[game.turn % len(GARBAGE)]
            game.step(actions)
        history.append([game.observe(p) for p in range(2)])
        yield game, history


def test_gamma_invariants_fuzzed():
    from testgames.gamma import game as gamma

    for game, history in _fuzz_matches(_package("testgames.gamma"), range(12)):
        for before, after in itertools.pairwise(history):
            obs, nxt = before[0], after[0]
            for v in range(obs["n_nodes"]):
                assert 0.0 <= nxt["stock"][v] <= obs["stock_max"][v]
                assert str(nxt["stock"][v]) != "-0.0"
            for v in game.bases:
                assert nxt["stock"][v] == 0.0
            # Revenue never exceeds the stock that was there, and running cost is bounded.
            revenue = [0.0, 0.0]
            for p, seat in enumerate(after):
                fleet_cost = sum(
                    gamma.UNIT_TYPES[kind]["cost"]
                    for kind, node in seat["player_units"].values()
                    if node != seat["player_base"]
                )
                revenue[p] = seat["player_profit_last_turn"] + fleet_cost
                assert revenue[p] >= -1e-9
            assert sum(revenue) <= sum(obs["stock"]) + 1e-9
            assert nxt["player_cumulative_profit"] == pytest.approx(
                obs["player_cumulative_profit"] + nxt["player_profit_last_turn"]
            )
        assert game.scores() == [history[-1][p]["player_cumulative_profit"] for p in range(2)]


def test_delta_invariants_fuzzed():
    from testgames.delta import game as delta

    for game, history in _fuzz_matches(_package("testgames.delta"), range(12)):
        for observations in history:
            for p, obs in enumerate(observations):
                drones = obs["player_drones"]
                assert all(d >= 0 for d in drones) and sum(drones) <= delta.MAX_DRONES
                assert obs["player_money"] == delta.START_MONEY + obs["player_cumulative_profit"]
                # Fog: visible exactly within VISION_RANGE of the base or of an own drone.
                dist = delta.hop_distances(obs["n_nodes"], obs["edges"])
                sources = [obs["player_base"]] + [v for v, d in enumerate(drones) if d > 0]
                for v, seen in enumerate(obs["opponent_drones"]):
                    visible = any(dist[s][v] <= delta.VISION_RANGE for s in sources)
                    assert (seen is not None) == visible, (obs["turn"], p, v)
                    if visible:
                        assert seen == observations[1 - p]["player_drones"][v]
        assert game.scores() == [history[-1][p]["player_cumulative_profit"] for p in range(2)]


def test_delta_served_matches_spec_numbers():
    from testgames.delta.game import served

    assert served(1, 0, 10.0) == 4  # 1 drone alone
    assert (served(2, 1, 10.0), served(1, 2, 10.0)) == (8, 2)  # 2 against 1
    assert (served(3, 2, 10.0), served(2, 3, 10.0)) == (10.0, 0)  # 3 against 2
    assert served(2, 2, 10.0) == 5.0  # equal fleets split in proportion
    assert served(0, 3, 10.0) == 0.0 and served(3, 0, 0.0) == 0.0
    assert served(1, 1, 10.0) == 4.0  # equal fleets, capacity below demand: full capacity


def test_delta_build_rules():
    from testgames.delta import game as delta

    game = delta.DeltaGame(2)
    base = game.bases[0]
    neighbour = game.graph[base][0]
    # 2 drones cost 30.0 > 20.0: illegal, nothing bought, the moves still apply.
    game.step([{"moves": [(base, neighbour, 1)], "build": 2}, None])
    obs = game.observe(0)
    assert obs["n_invalid_actions_last_turn"] == 1
    assert sum(obs["player_drones"]) == delta.START_DRONES
    assert obs["player_drones"][neighbour] == 1
    # 1 drone for 15.0 <= money: bought, on the base, no upkeep for it.
    money = obs["player_money"]
    game.step([{"moves": [], "build": 1}, None])
    obs = game.observe(0)
    assert obs["n_invalid_actions_last_turn"] == 0
    assert obs["player_drones"][base] == delta.START_DRONES  # 9 left + 1 bought
    revenue = obs["player_profit_last_turn"] + delta.UPKEEP * 1 + delta.BUILD_COST
    assert revenue == pytest.approx(min(delta.DRONE_CAPACITY, game.demand[1][neighbour]))
    assert obs["player_money"] == pytest.approx(money + obs["player_profit_last_turn"])
    # Negative money: no purchase possible; negative and oversized builds are illegal too.
    game.cumulative_profit[0] = -100.0
    for build in (1, -1, delta.MAX_DRONES):
        game.step([{"moves": [], "build": build}, None])
        obs = game.observe(0)
        assert obs["n_invalid_actions_last_turn"] == 1, build
        assert sum(obs["player_drones"]) == delta.START_DRONES + 1
    # MAX_DRONES is a hard cap even with plenty of money.
    game.cumulative_profit[0] = 10_000.0
    room = delta.MAX_DRONES - sum(game.drones[0])
    game.step([{"moves": [], "build": room + 1}, None])
    assert game.observe(0)["n_invalid_actions_last_turn"] == 1
    game.step([{"moves": [], "build": room}, None])
    assert game.observe(0)["n_invalid_actions_last_turn"] == 0
    assert sum(game.drones[0]) == delta.MAX_DRONES
    # Bought drones cannot move on the turn they are bought.
    game.cumulative_profit[1] = 10_000.0
    on_base = game.drones[1][game.bases[1]]
    hop = game.graph[game.bases[1]][0]
    game.step([None, {"moves": [(game.bases[1], hop, on_base + 1)], "build": 1}])
    assert game.observe(1)["n_invalid_actions_last_turn"] == 1
    assert game.drones[1][game.bases[1]] == on_base + 1


def test_gamma_speed_rules():
    from testgames.gamma import game as gamma

    game = gamma.GammaGame(9)  # the worked example's map; base 0 is node 0
    dist = game.dist
    two = next(v for v in range(game.n_nodes) if dist[0][v] == 2)
    three = next(v for v in range(game.n_nodes) if dist[0][v] == 3)
    one = next(v for v in range(game.n_nodes) if dist[0][v] == 1)
    moves = {"T0": one, "T1": two, "S0": two, "S1": three, "S2": 0, "Q1": one, "t0": one}
    game.step([{"moves": moves}, {"moves": {}}])
    obs = game.observe(0)
    assert obs["n_invalid_actions_last_turn"] == 4  # T1, S1 too far; Q1, t0 unknown ids
    units = obs["player_units"]
    assert units["T0"] == ("truck", one) and units["T1"] == ("truck", 0)
    assert units["S0"] == ("scooter", two) and units["S1"] == ("scooter", 0)
    assert units["S2"] == ("scooter", 0)
    # A unit on the opponent's base pays its cost and earns nothing.
    opp = game.bases[1]
    game.positions[0] = dict.fromkeys(game.positions[0], 0)
    game.positions[0]["S3"] = opp
    game.step([{"moves": {}}, {"moves": {}}])
    assert game.observe(0)["player_profit_last_turn"] == -gamma.UNIT_TYPES["scooter"]["cost"]
