TASK: write a stronger strategy than the current champion. It will be added to the pool and
must win the self-play tournament against the champion, the earlier candidates and the
simple bots, in both seats, over many seeds. The real opponents are other teams' generated
strategies, so it must be robust against strong and weak play alike, not tuned to one
opponent.

## Current champion

```python
{champion_code}
```

## Tournament report (self-play on our simulator)

{report}

## What to focus on

{focus}

## Strategic notes from an earlier analysis of this game

{analysis}

## How to work

1. Diagnose first, in comments at the top of the file: from the report and the code, explain
   WHY the champion loses or draws where it does (which opponents, which seat, what its
   allocation, movement, timing or cost handling gets wrong there), and which mechanisms of
   the game it leaves unused (margin and denial, opponent prediction, arrival delay,
   forecasts, the endgame horizon, costs).
2. Then write the improved complete strategy. Keep what works (its action building, its
   legality checks, the parts that win); change the decision logic where the diagnosis
   points. Prefer one or two well-founded improvements over many speculative ones.
3. No regressions in robustness: it must still never raise, never return a malformed or
   illegal action, beat an opponent that does nothing in both seats, and stay fast. Keep the
   module-level `TIME_BUDGET` and read it at call time; any search stops at about
   0.6 * TIME_BUDGET measured with `time.perf_counter()`.
4. Walk through the playbook's bug checklist before answering.

Answer with exactly one fenced ```python block containing the complete file (`class
Strategy`).
