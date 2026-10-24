# Report: our harness for Zentral Hack 2026

## In one paragraph

Our harness treats the LLM as a **programmer, not a player**. From the written specification
it has the LLM write several *competing* strategies with different strategic personas and,
in parallel, a **simulator** of the game. The simulator has to reproduce the specification's
worked examples and pass generic checks before the harness trusts it. Every
strategy is probed on the specification's own example observations, played on the simulator,
and repaired from concrete tracebacks and action-validity reports. The survivors play a
**round-robin self-play tournament** (both seats, many seeds, plus an idle opponent), and the
strongest model writes challengers against the champion's measured weaknesses. The whole time
a **harness-owned guard** wraps whatever the LLM wrote: hard per-turn timer, exception and
output checks, and a fallback strategy. A forfeit is therefore impossible by construction.
How much of this a run does depends on its own `task.json`: budget, deadline and models.

## Pipeline

```
t=0     fallback strategy written (guard, plays None)       -> /output/strategy.py
        spec parsed without LLM: example observations, observation keys, constants
batch   simulator (LLM) | strategy x N personas (LLM) | analysis (large budgets only)
verify  probe on example observations -> play vs idle on the simulator -> repair from
        tracebacks / malformed-action counts; simulator: selftest of the worked examples,
        schema + determinism + fuzz checks, repair loop, escalation to the stronger model
select  round robin on the simulator (candidates + idle, both seats, seeds)  -> promote
improve strongest model gets champion code + tournament report + worst pairings
        -> challengers -> incremental tournament -> promote if better
finish  final probe of the delivered file; atomic replace only after checks pass
```

## Design decisions and why

- **Never forfeit, by construction.** The delivered file embeds the LLM's code as strings
  and loads it into private modules at import time, so a syntax or import error cannot break
  the file. A `SIGALRM` timer (a `BaseException`, so `except Exception` in LLM code cannot
  swallow it) limits each turn. Exceptions, timeouts and non-literal outputs all fall
  back to a second, simpler strategy, and then to `None`. Verified with arena's real
  isolated worker on cores that raise, sleep, loop forever, import numpy, or print megabytes.
- **The simulator is verified, not trusted.** Selection by self-play only helps if the
  simulator is right, so it must reproduce every worked example of the specification
  (selftest), match the observation schema extracted from the specification, be
  deterministic per seed and survive garbage actions.
- **Select on wins, not score.** The tournament counts wins, so the harness ranks by win rate
  in a round robin (both seats), with mean margin as the tie-breaker.
- **Budget safety.** Every call reserves its worst-case cost (input at the higher of the
  input and cache-write price, plus `max_output_tokens` at the output price) before it is
  sent. Overspending is impossible as long as the gateway counts as `pricing.cost` does.
- **Deadline safety.** In the container the clock counts from the start of PID 1, so the
  container's own startup is included. The run stops starting work at a margin before the
  deadline, and the process hard-exits, so a hanging HTTP call cannot keep it alive.
- **Calibrated profiles.** Measured on the dev games (see below). The cheap model at high
  effort can reason past 40k tokens without answering, so it is never used at high effort.
  The mid model at low effort writes strategies as strong as at medium effort for half
  the cost, so the mid budget buys more candidates and improvement rounds instead.
- **Lessons file.** Nothing persists between evaluation runs, so "learning across games"
  happens offline: we ran the harness on the dev games and on six test games of our own with
  different mechanics (`testgames/`), and distilled what made strategies lose into
  `src/harness/prompts/playbook.md`, which every call carries.
- **Against overfitting.** We tune on six games and only measure on two held-out ones. In a
  run, every opponent weighs the same in the ranking, the worst pairing breaks ties, and a
  challenger must beat the champion by a margin. Self-play runs with exactly the delivered
  file's time settings, so what wins self-play is what plays in the tournament.

## Measurements

All numbers from our own runs (details and raw tables: `docs/STATUS.md`).

**Calibration** (one-shot strategy from the beta spec, then a round robin with beta's
baselines): the cheapest model is fast but weak, and at high effort it reasoned past a 40k
token cap without answering. The mid model at low effort (49 s, $0.04) was as strong as at
medium effort and as the priciest model (104 vs 104 vs 106 wins of 128). So the profiles buy
more drafts and improvement rounds, not more effort per draft.

**End to end:** in Docker, exactly like the evaluation, a task-A run finishes in 155 of 180 s
for $0.020, and its luna-only strategy goes 39-1-8 against beta's four baselines. Task-B runs
take 300-560 s and spend $0.31-0.40 of $0.50.

**Ablation (experiment 1, 24 task-B runs on three games, one feature switched off per arm):**
cross-tournament of the arms' strategies, +1/0/-1 per match like the official scoring.

| arm | beta | gamma | epsilon | total (net per strategy) |
|-----|-----:|------:|--------:|------:|
| full pipeline | +48.0 | +32.5 | -17.0 | +63.5 |
| without sparring draft | +48.0 | +28.5 | +36.5 | **+113** |
| without improvement rounds | -48.5 | -14.5 | +2.0 | -61 |
| one-shot baseline (one draft + guard) | -47.5 | -46.5 | -21.5 | -115.5 |

The pipeline beats the one-shot baseline in almost every match (48-0-0 on beta, 46-1-1 on
gamma). The improvement rounds are the largest single gain. The sparring draft (a cheap naive
opponent in self-play) did not help and hurt selection on one game, so it was removed. Every
feature that stayed was measured with and without it.

**Experiment 2** (18 more task-B runs) kept two features on the same evidence: playing extra
self-play seeds in the time left after the last LLM call (the arm without it lost on two of
three games), and improvement calls at medium rather than low effort (low lost on two of
three games, tied on the third).

**Experiment 3** (14 task-B runs, incl. the held-out zeta) tested a "budget rescue": task-B
runs often ended with $0.07-0.14 unspent, because a full improvement call reserves its worst
case (~$0.15) while it really costs ~$0.09. When a full call no longer fits, the rescue sends
it at low effort with a smaller token cap. By the decision rule we fixed beforehand it stays
in (better on one tuning game, tied on one, worse on one; better in total; tied on zeta; all
14 strategies pass the check, no forfeits, all within budget and deadline), but the effect is
small: only 3 of 13 rescue attempts fitted, since even a cheap call of the strong model costs
about $0.06, mostly input.

**Experiment 4** (24 task-B runs, all eight games) followed the midpoint evaluation, where our
best strategy on the hidden game was the best of the field but two of our five runs were
weak. Two ideas against that variance were rejected by the decision rule: a game plan by the
strong model before the drafts (worse on the tuning games, though better on one held-out
game) and five drafts instead of three (worse in total). The logs of that experiment showed
the real cause of weak runs: our own simulator check rejected correct simulators of games
with hidden information (a field typed `int | None`), so those runs never had self-play. The
check now accepts exactly the kinds the specification's examples show; replaying all 51 saved
simulators verifies 3 more on delta and 6 more on a held-out game, and none less elsewhere.

**Model policy.** Models and efforts follow from `task.json` (budget, deadline, allowed
models), never from a task name. Task A (cheapest model only): everything at low effort,
because the deadline binds and the cheap model gets weaker and slower with more effort. Task
B (cheap + strong): every call goes to the strong model at low effort (drafts, simulator,
repairs) and medium effort for the improvement rounds, which measurably pay off; the cheap
model is not used, as its drafts lose. Task C (strong models only): four drafts from both
models, the simulator and repairs at medium effort, an analysis call and three parallel
challengers per round, because there time binds before money. A test checks that the cheap
model never runs above low effort in any profile.

**Held-out exam:** on two of our test games that were never used for tuning, the full pipeline
beats the one-shot baseline 44-1-3 (theta: hidden demand, random surges, typed units) and
32-0-16 (zeta: renamed observation fields and a nested-dict action format), and every
strategy passes the conformance check.

## What we would do next

- Opponent-aware search inside the strategy using the verified simulator code.
- Population-based training across more seeds with a larger opponent pool.
