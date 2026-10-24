TASK: fix the strategy file below. The harness ran it and found the problems listed after it
(tracebacks, timeouts, malformed or illegal actions, failed checks). Each of these makes the
strategy forfeit or waste turns in real matches.

## Current file

```python
{code}
```

## Problems found

{problems}

## How to fix

- Find the root cause of every problem: read each traceback to the failing line, and check
  the action format and the legality rules in the spec again for malformed or illegal
  actions. Timeouts mean `act` is too slow: cache map data, cut the search, check
  `time.perf_counter()` against TIME_BUDGET.
- Make the minimal changes that fix the causes. Keep the strategy's approach, structure and
  tuning: do not rewrite what works, do not simplify the strategy into something weaker.
- Do not hide errors with a try/except around all of `act`; fix them. Where a refinement may
  still fail on unusual observations, compute a simple valid action first and keep it if the
  refinement fails.
- Keep the contract: module-level `TIME_BUDGET`, `class Strategy` with `__init__(self,
  player_id)` and `act(self, obs)`, standard library only, exact action types.
- Check the whole file once more against the playbook's bug checklist: the same mistake often
  appears in more than one place.

Answer with exactly one fenced ```python block containing the complete fixed file.
