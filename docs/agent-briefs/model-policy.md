# Brief: model and reasoning-effort policy (Sat 24 Oct, freeze 16:00)

For a coding agent. Read `AGENTS.md` and `docs/STATUS.md` first. This brief is one work package:
change only what it names, measure, report. Results are needed by **14:30**.

## Decision so far (do not re-litigate)

Facts from `docs/STATUS.md` (calibration and experiment 1):

- At evaluation the organisers pay. Unspent budget earns nothing. Allowed models are fixed:
  A = luna, B = luna + sol, C = sol + astra (no luna in C).
- luna @ high returns no code (reasons until the token cap). luna @ medium is 5x slower and
  weaker than luna @ low. So **the cheap model with more thinking does not work here**.
- sol @ low = sol @ medium = astra @ low on first drafts; astra @ low is the fastest (29 s).
- Improvement rounds are the biggest measured gain. B runs often end because the money runs
  out, not the time.

Therefore:

| tier (`make_profile`) | task | policy |
|---|---|---|
| `tiny` | A | unchanged: luna @ low everywhere |
| `large` | C | unchanged: money does not bind and is not ours; astra is as good and faster than sol, sol adds diversity |
| `mid` | B | **test**: move routine roles to luna @ low so the money buys more improvement rounds |

## Work items

### 1. Setup

- Tell Elias before touching `src/harness/`, `tests/`, `scripts/` (team rule in STATUS.md).
- Branch `exp/model-policy` from `origin/feat/harness-v1`. `uv sync`, `uv run pytest -q` green.
- Run experiments on **Linux** (the WSL machine or Docker). `scripts/run_harness.sh` treats any
  non-Linux system as Windows Git Bash, so it does not work on macOS as is.

### 2. Two variant switches (`src/harness/pipeline.py`)

Add to `ABLATIONS` and `apply_ablations` (dev only: the evaluation never sets
`HARNESS_ABLATE`). Both act **only on the `mid` profile** and are no-ops elsewhere. Update the
docstring: "switches features off or selects a variant".

- `luna_routine`: `sim_model = p.cheap`, `repair_model = p.cheap`, `sim_effort = "low"`,
  `repair_effort = "low"`. Drafts and improvement stay on `p.strong`. Keep the token caps.
- `improve_low`: `improve_effort = "low"` (cheaper and faster rounds, so more of them).

### 3. Tests (`tests/test_profiles.py`, new, offline)

- For the three evaluation `task.json`s (A, B, C as in `scripts/make_tasks.py`, prices from
  the tests' price table), no role in `make_profile` uses `gpt-6-luna` with effort `high`,
  `xhigh` or `max`. Use a small fake for `llm` (`cheapest()`, `strongest()`) and `clock`
  (`deadline_s`).
- `luna_routine` and `improve_low` change exactly the intended fields of `mid`, and nothing
  in `tiny` or `large`. Unknown flags are still ignored.

Commit (clean tree) before starting the experiment: `run_meta.json` records the commit.

### 4. Experiment 2 (budget about $7)

```bash
uv run python scripts/make_tasks.py --games beta gamma epsilon zeta --profiles B
scripts/ablate.sh exp2 "full luna_routine luna_routine,improve_low" "beta-B gamma-B epsilon-B" 2 2
scripts/ablate.sh exp2-heldout "full luna_routine luna_routine,improve_low" "zeta-B" 1 3
```

- Concurrent runs = RUNS x JOBS. Keep `2 x concurrent runs <= CPU cores`, otherwise
  self-play is slowed and the time-bound results are distorted.
- Evaluate per game, as in experiment 1:
  `uv run python scripts/evaluate.py --wsl --cross --group full='runs-exp/exp2/full/<game>-B/*' --group luna='runs-exp/exp2/luna_routine/<game>-B/*' --group luna_low='runs-exp/exp2/luna_routine,improve_low/<game>-B/*'`
- Also extract per run from `output/logs/events.jsonl`: spend, number of `improve_round`
  events, simulator verified (yes/no, repairs used), and whether the run stopped for money or
  for time.
- zeta is **held out**: measure it, never read its transcripts to tune anything.
- Stop and report if spend passes $8.

### 5. Decision rule (fixed before looking at results)

Adopt a variant as the new `mid` default only if **all** hold:

1. zero `arena check` failures and zero forfeits;
2. cross-tournament net >= full on at least 2 of the 3 tuning games, and total >= full;
3. simulator verification rate not lower than full;
4. held-out zeta not worse than full.

Otherwise keep the current profile. A tie keeps the current profile (fewer changes before the
freeze). If adopted: set the new values directly in `make_profile`'s `mid` tier (not via the
switch), update its docstring table, `uv run pytest -q`, then one smoke run in Docker:
`./run.sh tasks/beta-B` (exit 0, `arena check` passes, spend <= budget).

### 6. Report

- Add an "Experiment 2" section to `docs/STATUS.md`: setup, a table like experiment 1
  (net per strategy per game and arm, spend per run, improvement rounds per run, simulator
  pass rate), the decision, and the updated money total.
- One paragraph for `report.md` (model policy and why).
- Push the branch; Elias merges it into `feat/harness-v1` and fast-forwards `main`.

## Out of scope

Do not change the `tiny` or `large` profiles, prompts, guard, sandbox, tournament or
budget logic. No luna above low. No C experiments (C costs our money in development and its
policy does not depend on it). Nothing game-specific.
