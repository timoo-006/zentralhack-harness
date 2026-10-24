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
  happens offline: we ran the harness on the dev games and on our own test games with
  different mechanics (`testgames/`), and distilled what made strategies lose into
  `src/harness/prompts/playbook.md`, which every call carries.

## Measurements

(filled in from `runs/eval_log.jsonl`)

## What we would do next

- Opponent-aware search inside the strategy using the verified simulator code.
- Population-based training across more seeds with a larger opponent pool.
