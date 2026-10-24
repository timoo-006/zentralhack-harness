TASK: write `sim.py`, an exact simulator of the game in the specification above. The harness
uses it to play thousands of matches between candidate strategies, so it must follow the
rules exactly, never crash, and be fast. Standard library only.

## Interface (all of it is required)

```python
import random

class Game:
    n_players = 2

    def __init__(self, seed: int | None = None) -> None: ...
    def observe(self, player_id: int) -> dict: ...
    def step(self, actions: list) -> None: ...
    def is_over(self) -> bool: ...
    def scores(self) -> list[float]: ...
    def action_report(self, player_id: int) -> dict: ...

def selftest() -> list[str]: ...
```

- `__init__(seed)`: build a full match. If the spec fixes the map and starting state, hard-
  code them from the spec. If the spec describes a random generator (maps, positions, demand
  or other values), implement it faithfully IN DISTRIBUTION with one `self.rng =
  random.Random(seed)` (or `random.Random(f"{{seed}}:map")`-style independent streams): the
  same ranges, the same algorithm steps and retry conditions, the same rounding. The exact
  random calls cannot match the official engine; the distribution must. `seed=None` must also
  work (use seed 0). Any randomness during play also comes from these seeded generators, so
  the same seed and the same actions always give the same match.
- Allow building a game in an arbitrary state, for `selftest()`: keyword-only arguments with
  defaults after `seed` (e.g. a fixed map, positions or values), or a classmethod / helper
  that sets the state. `Game(seed)` with a single argument must keep working.
- `observe(player_id)`: exactly the observation the spec defines for that player: the same
  keys, the same value types (int vs float, list vs tuple, bool), the same ordering, the same
  perspective (fields that are relative to the observing player), and only the information
  the spec allows that player to see. Return fresh plain Python literals (no references to
  internal state, no custom classes). The harness expects these keys: {obs_keys}.
- `step(actions)`: `actions[i]` is player i's action. Apply one full turn exactly in the
  spec's order of operations (validation, movement, payout, costs, captures, turn counter).
  Distinguish malformed actions (usually the whole action is ignored) from illegal moves
  (usually only that move is skipped and counted) exactly as the spec says, including the
  strict type checks (`type(x) is int` style checks where the spec forbids bool or floats,
  list vs tuple). `step` must NEVER raise, whatever the actions are: None, wrong types, huge
  or negative numbers, nested garbage, too few or too many entries, or a non-list `actions`.
  Anything uninterpretable is treated as the spec treats a malformed action.
- `is_over()`: True exactly after the spec's last turn has been played.
- `scores()`: a list of 2 floats, the cumulative scores exactly as the spec computes them.
- `action_report(player_id)`: for the last `step`: `{{"well_formed": bool, "n_illegal":
  int}}` (well_formed False if the action was malformed; n_illegal = number of skipped
  illegal moves). Before the first step: `{{"well_formed": True, "n_illegal": 0}}`.

## Exact rule fidelity

Re-read the spec for each of these and implement it literally:
- order of operations within a turn, and when payouts happen relative to moves;
- legality of moves against the start-of-turn state, and the limit on movement per turn;
- the payout formula, its edge cases (zero demand, zero units, ties, caps) and rounding;
- every cost and when it is charged;
- what the observation shows on the first turn and on the last turns (shorter windows);
- the number of turns and how the game ends; what counts as a draw is NOT the simulator's
  job (just return the scores).

## selftest()

`selftest()` returns a list of failure strings (empty list = everything passed) and never
raises: wrap each check in its own `try/except Exception as e` and append a short message.
It must reproduce EVERY worked example in the spec exactly:
- construct the exact state the example starts from (its map, positions, values, turn);
- apply the example's actions;
- compare the resulting observations (every key, floats with tolerance 1e-6), rewards,
  scores and counters with the values printed in the spec. Hard-code the expected values by
  copying them from the spec text: never recompute them with the simulator's own formula;
- also check every smaller numeric example in the text (e.g. "x units against y units give
  a and b") through the same payout code the simulator uses;
- check the documented malformed and illegal cases: a malformed action is ignored as the spec
  says and reported, an illegal move is skipped and counted, legal moves in the same action
  still apply;
- check invariants on a random match with random (sometimes invalid) actions: the turn
  counter advances by one per step, the game ends after the right number of turns,
  observations stay literal dicts with the right keys, and units are conserved unless the
  spec creates or destroys them.

{examples_note}

## Speed

`step` and `observe` should take well under a millisecond on typical sizes: precompute the
map, avoid deep copies of large structures, no prints, no recursion.

Answer with exactly one fenced ```python block containing the complete `sim.py`.
