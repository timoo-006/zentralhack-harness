TASK: fix the simulator `sim.py` below. The harness ran its `selftest()` and generic checks
(construction, observation keys and value types against the spec's examples, random play
without exceptions, determinism, game length, scores) and found the problems listed after it.

## Current sim.py

```python
{code}
```

## Problems found

{problems}

## How to fix

- For each problem, re-read the exact passage of the specification it concerns (order of
  operations, payout and rounding, costs, legality, malformed versus illegal handling, the
  observation's keys, types and perspective, the first and last turns, the number of turns)
  and correct the simulator to match it literally.
- A selftest expectation may only change if it contradicts the spec text; then copy the
  correct value from the spec and say so in a comment. Never delete or loosen a check to make
  it pass, and never replace an expected value by what the simulator computes.
- Exceptions must disappear at their cause; `step` must accept any action value without
  raising (garbage is handled as the spec handles a malformed action).
- Make minimal changes; keep the interface: `class Game` with `n_players = 2`,
  `__init__(seed)`, `observe`, `step`, `is_over`, `scores`, `action_report`, and the
  module-level `selftest() -> list[str]`. Standard library only.

Answer with exactly one fenced ```python block containing the complete fixed `sim.py`.
