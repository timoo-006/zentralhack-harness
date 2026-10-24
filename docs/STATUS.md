# Status: read this first

> Living handoff page for the team. Updated whenever something lands. Branch:
> **`feat/harness-v1`** (not yet merged into `main`). Last update: Sat 24 Oct, ~09:45.
> Freeze: **today 16:00**.

## TL;DR

- `main` = the template + Timo's docs (`TASK.md`, `PLAN.md`, `AGENTS.md`). No harness code.
- `feat/harness-v1` = the harness built on Timo's `PLAN.md`: a deterministic pipeline that
  has the LLM write **several strategies + a simulator**, verifies both, picks the winner by
  **self-play**, improves it, and wraps it in a **never-forfeit guard**.
- How the modules fit together: [`ARCHITECTURE.md`](ARCHITECTURE.md) (module contracts).
  Why: [`PLAN.md`](PLAN.md). What we measured: below.
- **OpenAI money:** we have ~150 CHF in total. ~$0.80 spent so far (calibration). Rules below.

## Branches, and how we pick the freeze

- **`main`**: yours, use it however you like.
- **`feat/harness-v1`**: the pipeline harness described here, developed separately.
- **~15:00 shoot-out.** Every contender runs on the same tasks, and the results decide what
  goes into `main`:
  1. `scripts/make_tasks.py` (same task dirs for everyone: beta-B, alpha-B, two test games,
     beta-A), 3 runs per harness per task (`scripts/run_harness.sh TASK N`).
  2. `arena check` on every produced strategy: **any forfeit disqualifies a harness**.
  3. `scripts/evaluate.py --cross --group v1='runs/...' --group main='runs/...'`:
     cross-tournament between the two harnesses' strategies, scored +1/0/-1 like the
     official rules. Same-harness pairings are excluded, as at the evaluation.
  4. The winner is merged into `main` (or `main` is reset to it). The freeze commit is
     pushed by 15:45 and sent to the organisers.
- Ideas from the losing side can still be ported if there is time (e.g. prompts).

## What is built (and by whom)

| part | files | state |
|------|-------|-------|
| entry point, SIGTERM, t=0 fallback | `src/harness/__main__.py` | written |
| pipeline + task profiles | `src/harness/pipeline.py` | written, not yet run end-to-end |
| deadline clock (counts from container start) | `src/harness/clock.py` | being built + reviewed |
| budget (worst-case reservation per call) | `src/harness/budget.py` | being built + reviewed |
| LLM client (4 concurrent, retries, transcript) | `src/harness/llm.py` | being built + reviewed |
| spec parser (example observations, schema) | `src/harness/specdoc.py` | being built + reviewed |
| never-forfeit guard (assembles the final file) | `src/harness/guard.py` | being built + reviewed |
| sandbox + match runner (subprocess, stdlib only) | `src/harness/sandbox.py`, `runner_child.py` | being built + reviewed |
| self-play tournament (incremental, win rate) | `src/harness/tournament.py` | being built + reviewed |
| prompts + playbook (the "lessons file") | `src/harness/prompts/*` | written, under review |
| our own test games (6, different mechanics) | `testgames/{gamma,delta,epsilon,zeta,eta,theta}` | being built + verified |
| dev tooling (make tasks, run, evaluate) | `scripts/*` | being built |
| report (deliverable) | `report.md` | draft |

The template's `agent.py` / `prompt.md` (one model, one bash tool) are replaced by the
pipeline and will be removed at integration.

## How one run works

```
t=0     write a fallback strategy (guard, plays None)  -> /output/strategy.py is ALWAYS valid
        parse the spec (no LLM): example observations, observation keys, constants
batch   simulator (LLM) | 3-4 strategies with different personas (LLM)  -- in parallel
verify  probe each strategy on the spec's example observations, then play it vs idle on the
        simulator; repair from tracebacks / malformed-action counts. The simulator must pass
        its own selftest of the spec's worked examples + generic checks, else repair/escalate.
select  round robin on the simulator (candidates + idle, both seats, several seeds)
improve strongest model gets the champion + tournament report -> challengers -> re-rank
finish  promote = guard.assemble(champion, fallback=safe candidate), atomic replace
```

The profile (how many calls, which model, which effort) is derived from `task.json` (budget,
deadline, allowed models), never from the task letter:

| tier | when | drafts | simulator | improvement |
|------|------|--------|-----------|-------------|
| tiny (task A) | budget < $0.10 or deadline < 5 min | 3x cheapest @ low | cheapest @ low | 2 parallel @ low, while time lasts |
| mid (task B) | budget < $1.50 | 3x strongest @ low | strongest @ low | strongest @ medium |
| large (task C) | otherwise | astra + 2x sol + astra @ low | sol @ medium | 3 parallel per round (astra + 2x sol) |

## What we measured (calibration, beta spec, one-shot strategy, Oct 24)

| model | effort | seconds | output tokens | USD | wins vs field (128 matches) |
|-------|--------|--------:|--------------:|----:|-----:|
| gpt-6-luna | low | 19 | 1.6k | 0.001 | 32 |
| gpt-6-luna | medium | 104 | 9.5k | 0.006 | 16 |
| gpt-6-luna | high | >456 | >40k (cut off, **no code**) | 0.021 | — |
| gpt-6.1-sol | low | 49 | 2.5k | 0.041 | 104 |
| gpt-6.1-sol | medium | 145 | 6.7k | 0.083 | 104 |
| gpt-6-astra | low | 29 | 2.1k | 0.182 | 106 |
| gpt-6-astra | medium | 149 | 7.2k | 0.439 | 102 |

Field = the other drafts + beta's baselines (GreedyResponse 48, ProportionalSpread 64,
DoNothing 0 wins). Takeaways:

- **Never run luna at high effort**: it reasons until the token cap and returns nothing.
- **sol @ low is as good as sol @ medium and astra** on one-shot drafts, at a fraction of the
  time/cost. Any advantage of astra has to come from the improvement rounds, not the drafts.
- In task A the **deadline** binds (one luna-medium call = 104 of 180 s), not the money.
- The first call writes the prompt cache at **1.25x** the input price; the budget's worst
  case accounts for that.

## Money rules (our own OpenAI key)

- At **evaluation** the organisers' key pays, so task C should use its full $10 there.
  Only our **test runs** cost us.
- Test with A and B profiles; for C use a "C-lite" task (`--budget-usd 2 --deadline-s 900
  --model gpt-6.1-sol --model gpt-6-astra`). At most 1-2 real C runs before the freeze.
- Calibration done, so no more one-off experiments without a reason. Target: < $30 total.

## How to run things

Linux matters: the guard's timer (`signal.setitimer`) and arena's isolated runner
(`os.killpg`) do not exist on Windows. Use WSL or Docker for anything real.

```bash
uv sync                                        # Windows dev env (.venv)
# WSL (Ubuntu-24.04): uv + Python 3.13 venv at /root/venv-harness (already set up on Elias' machine)
#   UV_PROJECT_ENVIRONMENT=/root/venv-harness uv sync --python 3.13

uv run pytest -q                               # unit tests (Linux-only ones skip on Windows)
uv run arena make-task --game arena_games_dev.beta --budget-usd 0.5 --deadline-s 600 \
    --model gpt-6-luna --model gpt-6.1-sol --out tasks/beta-B
uv run --env-file .env harness tasks/beta-B    # local run (no Docker), output in runs/beta-B/<time>/
./run.sh tasks/beta-B                          # like the evaluation (Docker)
```

`scripts/README.md` has the batch loops (make all tasks, N parallel runs, evaluate,
harness-vs-harness). Each run leaves `output/logs/events.jsonl` (every decision, with time
and spend), `transcript.jsonl` (every LLM call), `candidates/` (all generated code) and
`tournament.txt`.

## Next steps (today)

1. Integrate the modules, unit tests green on Windows + WSL.
2. First real runs: beta-A, beta-B (cheap), then alpha, then the test games.
3. Offline lessons loop: classify why runs lose (format, simulator, timeouts, losing to
   idle), fix the harness or the playbook, repeat.
4. Docker runs of all three profiles, `report.md` with numbers, presentation.
5. Merge into `main`, freeze commit to the organisers (`nkaslin` needs repo access).

## Open questions / decisions for the team

- Is anyone else changing `src/harness/` right now? If so, tell us before merging.
- Who prepares the presentation? Material: this page, `report.md`, `runs/eval_log.jsonl`.
- Has `nkaslin` been invited to the repo yet (Settings → Access)?
