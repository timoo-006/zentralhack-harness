"""Tests for harness.specdoc: the dev specs, a synthetic spec with unusual blocks, garbage."""

import importlib.util
from pathlib import Path

import pytest

from harness.specdoc import SpecInfo, describe_examples, describe_obs_keys, parse_spec


def _dev_spec(game: str) -> str:
    spec = importlib.util.find_spec(f"arena_games_dev.{game}")
    if spec is None or spec.origin is None:
        pytest.skip("arena_games_dev is not installed")
    return (Path(spec.origin).parent / "spec.md").read_text(encoding="utf-8")


def _contains_ellipsis(v) -> bool:
    if v is Ellipsis:
        return True
    if isinstance(v, dict):
        return any(_contains_ellipsis(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return any(_contains_ellipsis(x) for x in v)
    return False


ALPHA_KEYS = [
    "turn",
    "player_drones",
    "opponent_drones",
    "player_reward_last_turn",
    "opponent_reward_last_turn",
    "player_cumulative_reward",
    "opponent_cumulative_reward",
    "n_invalid_actions_last_turn",
    "action_format_correct_last_turn",
]


def test_alpha() -> None:
    info = parse_spec(_dev_spec("alpha"))
    assert info.obs_keys == ALPHA_KEYS
    assert len(info.example_observations) == 1
    obs = info.example_observations[0]
    assert list(obs) == ALPHA_KEYS
    assert obs["turn"] == 1
    assert obs["player_drones"] == [0, 10, 0, 10, 0, 0, 0, 0, 0]
    assert obs["opponent_drones"] == [0, 0, 6, 0, 12, 0, 0, 0, 2]
    assert obs["player_reward_last_turn"] == 28.0
    assert obs["action_format_correct_last_turn"] is True

    c = info.constants
    assert c["N_NODES"] == 9
    assert c["NODE_DEMANDS"] == [0, 11, 11, 17, 17, 3, 24, 32, 0]
    assert len(c["EDGES"]) == 13 and c["EDGES"][0] == (0, 1)
    assert all(type(e) is tuple for e in c["EDGES"])
    assert c["BASES"] == [0, 8]
    assert c["DRONES_PER_PLAYER"] == 20
    assert c["DRONE_CAPACITY"] == 5
    assert c["N_TURNS"] == 100

    assert [(0, 1, 10), (0, 3, 10)] in info.action_examples
    assert [(8, 4, 12), (8, 2, 6)] in info.action_examples
    assert [(0, 1, 20), (1, 6, 20)] in info.action_examples
    assert [] in info.action_examples
    assert len(info.python_blocks) >= 3
    assert any("graph LR" in b for b in info.code_blocks)  # mermaid blocks are kept too


def test_beta() -> None:
    info = parse_spec(_dev_spec("beta"))
    assert info.obs_keys is not None and len(info.obs_keys) == 14
    assert info.obs_keys[0] == "turn" and "demand_forecast" in info.obs_keys
    assert len(info.example_observations) == 2
    t0, t1 = info.example_observations
    assert (t0["turn"], t1["turn"]) == (0, 1)
    assert set(t0) == set(t1) == set(info.obs_keys)
    # turn 1's "edges": [...] is filled in from turn 0, as a fresh copy
    assert t1["edges"] == t0["edges"]
    assert t1["edges"] is not t0["edges"]
    assert len(t0["edges"]) == 13 and all(type(e) is tuple for e in t0["edges"])
    assert not _contains_ellipsis(t0) and not _contains_ellipsis(t1)
    assert t0["player_drones"][8] == 30 and t1["player_drones"] == [3, 1, 0, 0, 0, 0, 0, 0, 26, 0]
    assert t1["player_profit_last_turn"] == 5.6
    assert len(t0["demand_forecast"]) == 10 and t0["demand_forecast"][0][0] == 13.0

    c = info.constants
    assert c["N_TURNS"] == 100 and c["DRONES_PER_PLAYER"] == 30 and c["UPKEEP"] == 2.0
    assert c["N_NODES_RANGE"] == (10, 16)
    assert c["CURVE_SHAPES"] == ("linear", "early", "late", "s_curve")

    assert [(8, 0, 3), (8, 1, 1)] in info.action_examples
    assert [(3, 4, 4), (3, 6, 1)] in info.action_examples
    assert not any(_contains_ellipsis(a) for a in info.action_examples)

    assert "'demand_forecast'" in describe_obs_keys(info)
    assert "2" in describe_examples(info)


SYNTHETIC = """# Game Gamma

Units carry ids. An action is a dict, e.g. `{"moves": {3: 5, 7: 2}, "build": 1}` or
``{"moves": {}}`` to pass. A list of orders like `[{"unit": 1, "to": 4}]` is also shown,
and `(2, 3)` is a pair. Ranges like `[0, n)` are not literals.

~~~py
MAX_TURNS = 60
UNITS = 2 * 4
SPEED: int = 3
A, B = 1, 2.5
CAP = UNITS * SPEED
BAD = some_function(3)
~~~

1. Observation schema, indented in a list:

   ```python
   {
       "round": int,  # it's a comment with an apostrophe
       "units": list[dict],
       "score": float,
       "hidden": None | int,
   }
   ```

A unit is described by:

```python
{"id": int, "node": int, "kind": str}
```

````markdown
```python
this = is not parsed { as python
```
````

```json
{"round": 0, "units": [{"id": 0, "node": 1, "kind": "scout"}], "score": 0.0, "hidden": null}
```

```python
{
    "round": 1,
    "units": [...],  # unchanged
    "score": 2.5,
    "hidden": 3,
}
{"round": 2, "units": [...], "score": 4.0, "hidden": [...]}
```

```
obs = {"round": 3, "units": [], "score": 1e3, "hidden": None}  # bare block
→ followed by prose that does not parse
```

```python
>>> act(obs)
{"moves": {1: 2}}
```

```python
[(1, 2, 3)]
```

Unclosed fence follows:

```python
X = [1, 2,
"""


def test_synthetic_spec() -> None:
    info = parse_spec(SYNTHETIC)
    c = info.constants
    assert c["MAX_TURNS"] == 60 and c["UNITS"] == 8 and c["SPEED"] == 3
    assert c["A"] == 1 and c["B"] == 2.5 and c["CAP"] == 24
    assert "BAD" not in c and "X" not in c

    # the observation schema wins over the 3-key unit schema; examples come from json,
    # python and bare blocks, with Ellipsis filled from the nearest example without it
    assert info.obs_keys == ["round", "units", "score", "hidden"]
    rounds = [o["round"] for o in info.example_observations]
    assert rounds == [0, 1, 2, 3]
    by_round = {o["round"]: o for o in info.example_observations}
    assert by_round[0]["hidden"] is None  # json null
    assert by_round[1]["units"] == [{"id": 0, "node": 1, "kind": "scout"}]
    assert by_round[2]["units"] == by_round[0]["units"]  # round 1's own value has Ellipsis
    assert by_round[2]["hidden"] == 3  # nearest earlier donor
    assert by_round[3]["score"] == 1000.0
    assert not any(_contains_ellipsis(o) for o in info.example_observations)

    acts = info.action_examples
    assert {"moves": {3: 5, 7: 2}, "build": 1} in acts
    assert {"moves": {}} in acts
    assert [{"unit": 1, "to": 4}] in acts
    assert (2, 3) in acts
    assert {"moves": {1: 2}} in acts  # console transcript output
    assert [(1, 2, 3)] in acts
    assert len(acts) == len({repr(a) for a in acts})

    assert len(info.code_blocks) == 10
    assert any("this = is not parsed" in b for b in info.code_blocks)
    assert ["id", "node", "kind"] in info.schemas


def test_schema_without_examples() -> None:
    info = parse_spec('```python\n{"a": int, "b": list[int], "c": float}\n```\n')
    assert info.obs_keys == ["a", "b", "c"]
    assert info.example_observations == []
    assert "unknown" not in describe_obs_keys(info)
    assert "could not extract" in describe_examples(info)


def test_examples_without_schema() -> None:
    spec = (
        "```python\n{'x': 1, 'y': 2, 'z': 3}\n{'x': 4, 'y': 5, 'z': 6}\n"
        "{'p': 1, 'q': 2, 'r': 3}\n```"
    )
    info = parse_spec(spec)
    assert info.obs_keys == ["x", "y", "z"]
    assert [o["x"] for o in info.example_observations] == [1, 4]


def test_ellipsis_without_donor_is_dropped() -> None:
    spec = (
        "```python\n{'t': 0, 'a': [...], 'b': 1}\n{'t': 1, 'a': [1, ...], 'b': 2}\n"
        "{'t': 2, 'a': [5], 'b': [...]}\n```"
    )
    info = parse_spec(spec)
    assert info.obs_keys == ["t", "a", "b"]
    # t=0 and t=1 take "a" from t=2; t=2 takes "b" from t=1 (the nearest earlier example)
    got = [(o["t"], o["a"], o["b"]) for o in info.example_observations]
    assert got == [(0, [5], 1), (1, [5], 2), (2, [5], 2)]

    info = parse_spec("```python\n{'t': 0, 'a': [...], 'b': 1}\n{'t': 1, 'a': ..., 'b': 2}\n```")
    assert info.obs_keys == ["t", "a", "b"]
    assert info.example_observations == []


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        None,
        12345,
        b"```python\n{'a': 1, 'b': 2, 'c': 3}\n```",
        "```",
        "``````",
        "```python\n{{{{[[[(((\n```",
        "```python\n" + "[" * 5000 + "\n```",
        "`" * 1001,
        "```python\nx = 10 ** 10 ** 10\ny = 9" + "9" * 6000 + "\n```",
        "```python\ns = '\\d \\q'\n{'a': '\\d', 'b': 1, 'c': 2}\n```",
        "\x00\x01\x02 ``` \udcff",
        "random words { [ ( ' \" # ` `[1, 2` `{'a':` ~~~",
    ],
)
def test_garbage_never_raises(garbage, recwarn) -> None:
    info = parse_spec(garbage)
    assert isinstance(info, SpecInfo)
    assert not [w for w in recwarn.list if issubclass(w.category, SyntaxWarning)]
    if isinstance(garbage, bytes):
        assert info.example_observations == [{"a": 1, "b": 2, "c": 3}]


def test_empty_fields() -> None:
    info = parse_spec("no code at all, just `prose`.")
    assert info == SpecInfo()
    assert "unknown" in describe_obs_keys(info)
