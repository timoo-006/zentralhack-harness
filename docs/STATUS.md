# Status: read this first

> Living handoff page for the team. Updated whenever something lands. Branch:
> **`main`** (harness merged at 10:45; development on `feat/harness-v1`). Last update: Sat 24 Oct, ~10:40.
> Freeze: **today 16:00**.

## TL;DR

- `main` = the harness, built on Timo's `PLAN.md`: a deterministic pipeline that
  has the LLM write **several strategies + a simulator**, verifies both, picks the winner by
  **self-play**, improves it, and wraps it in a **never-forfeit guard**.
- How the modules fit together: [`ARCHITECTURE.md`](ARCHITECTURE.md) (module contracts).
  Why: [`PLAN.md`](PLAN.md). What we measured: below.
- **OpenAI money:** we have ~150 CHF in total. ~$6.60 spent so far (calibration, first runs,
  experiment 1). Rules below.
- **It works end to end**, also in Docker exactly like the evaluation (see "First results").

## Branches

- **`main` = the harness** (fast-forwarded from `feat/harness-v1` at 10:45; the planned
  shoot-out is not needed: nothing else was built on `main`).
- Development continues on **`feat/harness-v1`**; `main` is fast-forwarded to it at each
  checkpoint (always a tested, committed state). The freeze commit will be on `main`.
- **Teammates:** `git pull` on `main`, put documents and the presentation under `docs/`
  (or `presentation/`), commit and push to `main` as usual. Please do not edit
  `src/harness/`, `tests/` or `scripts/` without telling us: those are tested together.

## What is built (and by whom)

| part | files | state |
|------|-------|-------|
| entry point, SIGTERM, t=0 fallback | `src/harness/__main__.py` | done, runs in Docker |
| pipeline + task profiles + ablation switches | `src/harness/pipeline.py` | done, tuned on first runs |
| deadline clock (counts from container start) | `src/harness/clock.py` | done + reviewed (7 fixes) |
| budget (worst-case reservation per call) | `src/harness/budget.py` | done + reviewed |
| LLM client (4 concurrent, retries, transcript) | `src/harness/llm.py` | done + reviewed |
| spec parser (example observations, schema) | `src/harness/specdoc.py` | done + reviewed (5 fixes) |
| never-forfeit guard (assembles the final file) | `src/harness/guard.py` | done + reviewed (8 fixes) |
| sandbox + match runner (subprocess, stdlib only) | `src/harness/sandbox.py`, `runner_child.py` | done + reviewed |
| self-play tournament (incremental, robust ranking) | `src/harness/tournament.py` | done + reviewed |
| prompts + playbook (the "lessons file") | `src/harness/prompts/*` | done + reviewed |
| our own test games (6, different mechanics) | `testgames/{gamma,delta,epsilon,zeta,eta,theta}` | done, each verified by an independent simulator |
| dev tooling (make tasks, run, evaluate, ablate) | `scripts/*` | done + reviewed (9 fixes) |
| report (deliverable) | `report.md` | draft |

Tests: 199/199 on Linux (WSL), incl. real `arena check` (isolated) of deliberately broken
strategies (raise, sleep, infinite loop, numpy import, print flood, non-literal output).

The template's `agent.py` / `prompt.md` (one model, one bash tool) are replaced by the
pipeline and will be removed at integration.

## First results

| run | where | time | spend | outcome |
|-----|-------|-----:|------:|---------|
| beta-A | WSL | 146 / 180 s | $0.017 / 0.03 | simulator OK first try; check PASS; **39-1-8 vs beta's 4 baselines** |
| beta-A | **Docker** (like the evaluation) | 155 / 180 s | $0.020 / 0.03 | exit 0; simulator repaired once from its failed selftest |
| beta-B | WSL | 530 / 600 s | $0.33 / 0.50 | best_response won self-play (0.94); first challenger won every pairing (1.00) and was promoted |

Fixed from these runs: a float residue in the budget made a run sit idle for ~285 s; token
caps were 3-5x the real output (blocked parallel calls); the sparring draft was repaired
with the expensive model.

## Experiment 1: is each feature worth it? (done, 10:35)

Same tasks, same commit (`61610da`), one feature switched off per arm (`HARNESS_ABLATE`),
task profile B, 2 runs per arm and game, 24 runs, $5.31 in total. Cross-tournament between the
arms' strategies (+1/0/-1 per match, both seats, 6 seeds, an arm never meets itself):

| game | full | no sparring | no improvement | oneshot (baseline) |
|------|-----:|------------:|---------------:|-------------------:|
| beta (net per strategy) | **+48.0** | **+48.0** | -48.5 | -47.5 |
| gamma (net per strategy) | **+32.5** | +28.5 | -14.5 | -46.5 |
| epsilon (net per strategy) | -17.0 | **+36.5** | +2.0 | -21.5 |
| **total over 3 games** | +63.5 | **+113** | -61 | -115.5 |

Head-to-head, full vs: oneshot **48-0-0** (beta), **46-1-1** (gamma); no improvement
**48-0-0**, **30-6-12**; no sparring 24-0-24, 24-2-22.

- **The pipeline is worth it**: against the one-shot baseline (one sol draft + guard, what a
  typical team ships) it wins almost every match.
- **Improvement rounds are worth it**: they are the biggest single gain.
- **The sparring draft is switched off**: no gain on beta and gamma, and on epsilon it made
  selection worse (full lost 11-1-36 to the arm without it): weak opponents in the pool
  reward strategies that crush naive play but lose to strong play.
- Caveat: 2 runs per arm is noisy (which personas happen to come out strong varies); we trust
  directions that agree across games, not single cells.
- Mean spend per B run: full $0.34, no improvement $0.14, oneshot $0.03. Several full runs
  ended early because the money ran out, not the time.

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

## Against overfitting (rules for everyone tuning prompts or code)

We are scored on two games nobody has seen, so every change is judged by how it transfers.

| set | games | use |
|-----|-------|-----|
| tuning | alpha, beta, gamma, delta, epsilon, eta | run, read the failures, change prompts/code |
| **held out** | **zeta, theta** | only measure; never read their transcripts to fix prompts |

- **No game-specific content** in `src/harness/` (enforced by `tests/test_prompts.py` for
  the prompts): a lesson goes into the playbook only if it is phrased for the whole family and
  helped on at least two tuning games.
- **Compare on data, not one run**: a change is kept if it improves (or keeps) the
  cross-tournament result over all tuning games, 2-3 runs each, and does not hurt the
  held-out games. Single-run differences are noise (the evaluation itself averages 5 runs).
- **In a run** (built into `pipeline.py`):
  - candidates are ranked by their **mean score per opponent** (every opponent weighs the
    same), then their **worst pairing**, then the margin;
  - a challenger replaces the champion only if it is better by a margin and does not lose
    the head-to-head (incumbent protection against noisy self-play);
  - the pool always contains **idle** (never lose to doing nothing), the **safe** persona,
    and a cheap **sparring** draft that stands in for the naive part of the real field;
  - both seats and several seeds (= several random maps) per pairing;
  - the simulator is trusted only after it reproduces the spec's worked examples; strategies
    are written from the spec, never from the simulator's code, so they cannot be tuned to
    its bugs.

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
