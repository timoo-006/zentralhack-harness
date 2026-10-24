# Status: read this first

> Living handoff page for the team. Updated whenever something lands. Branch:
> **`main`** (harness merged at 10:45; development on `feat/harness-v1`). Last update: Sat 24 Oct, ~12:55.
> Freeze: **today 15:30** (moved from 16:00).

## TL;DR

- `main` = the harness, built on Timo's `PLAN.md`: a deterministic pipeline that
  has the LLM write **several strategies + a simulator**, verifies both, picks the winner by
  **self-play**, improves it, and wraps it in a **never-forfeit guard**.
- How the modules fit together: [`ARCHITECTURE.md`](ARCHITECTURE.md) (module contracts).
  Why: [`PLAN.md`](PLAN.md). What we measured: below.
- **OpenAI money:** we have ~150 CHF in total. ~$37 spent so far (calibration, first runs,
  experiments 1-4, held-out exam, task-C check). Rules below.
- **It works end to end**, also in Docker exactly like the evaluation (see "First results").

## Handover: continue on any machine (Mac guide: [`MAC.md`](MAC.md))

**State at 11:35:** all experiments are done and exported to [`experiments/`](../experiments/)
(re-evaluable anywhere); every decision is recorded on this page; `main` is a tested state
that can be frozen as it is. Nothing depends on Elias' laptop any more.

**For the next Claude / person, in this order:**

1. **Setup** on the Mac: [`MAC.md`](MAC.md) steps 1-5 (`brew install uv coreutils`, Docker
   Desktop, `uv sync`, `.env` with the team key, `uv run pytest -q`, `make_tasks.py`).
2. **Freeze (mandatory, by 15:15):**
   ```bash
   git checkout main && git pull
   uv run pytest -q                       # all pass (a few Linux-only tests skip on macOS)
   ./run.sh tasks/beta-A                  # Docker, like the evaluation: exit 0, strategy written (~$0.02)
   uv run python scripts/evaluate.py --seeds 4 --no-log runs/beta-A/<that run>   # check PASS
   git rev-parse HEAD                     # send the full hash to the organisers (TASKS.md T5)
   ```
   `nkaslin` must have access to the repository; `report.md` is already complete.
3. **Optional, only with measurement** (same rules as experiments 1-2: fix the decision rule
   first, compare with `scripts/ablate.sh` on tuning games only, never on zeta/theta):
   the open proposals in "Failure mining of experiment 1" (numbered by expected value);
   tasks T2/T3 on [`TASKS.md`](TASKS.md).
4. **Do not** change `src/harness/` in the last hour without a full test run and one Docker
   run afterwards.

## Cleanup (12:00, Timo): simpler code, macOS fixes; no change in how a run plays

- Removed: the template's `agent.py` / `prompt.md`; the sparring draft (off since experiment 1:
  its persona, profile flag, ablation switch and every branch for it); unused helpers
  (`BudgetExceeded`, `specdoc.describe_*`); the `SELFPLAY_*` aliases of `FINAL_*`.
- macOS: arena's worker sets `RLIMIT_AS`, which macOS rejects, so every isolated worker died
  (`BrokenPipeError`). `scripts/evaluate.py` now drops only that cap on macOS (verified:
  C-lite beta run PASS, 16-0-0); the one test that runs the `arena check` CLI skips on macOS;
  `runner_child` sets each rlimit on its own, so the file-size cap holds on macOS too.
- `tests/test_profiles.py`: for tasks A, B, C and every `HARNESS_ABLATE` switch, the cheapest
  model (luna) only ever runs at low effort, and only allowed models are used.
- Ruff: `experiments/` is excluded (generated strategies), Markdown is never reformatted
  (`ruff format .` would otherwise rewrite the prompts' code blocks).

## Fix (14:00): the simulator check rejected correct simulators (hidden information)

Found in the experiment-4 logs (per-run analysis): in 6 of 24 runs (delta and theta, every
arm) the simulator never passed, so the run had no self-play and no improvement, delivered
the provisional first draft and ended after 116-178 s of 600. The cause was ours: the
generic schema check compared a list's first element only with the spec example's first
element. Delta's spec types `opponent_drones` as `list[int | None]` (None on unseen nodes, as
its own example shows), so a correct simulator failed whenever node 0 was hidden, and the
repairs chased a non-bug ($0.08 each). Now a value may take any kind the spec's examples show
at that position; None only where an example shows None. Replay of the 51 saved simulators
of experiments 3-4 (offline, no LLM): delta 0 -> 3 fully verified (the other 4 still fail
their own selftest), held-out theta 0 -> 6 (measured only), all other games unchanged.
This is the most likely kind of failure behind weak runs on unseen games with hidden
information (h1's 29% and 38% runs: not provable without their logs).
Real check (Docker, delta-B, commit `4fa0036`): simulator verified first try at 41 s,
self-play, 2 improvement rounds, 25 polish seeds; 560/600 s, $0.393, check PASS; against
the 3 pre-fix delta strategies of experiment 4 it wins **44-1-15** (10 seeds, both seats).
The fix acts in every profile (A, B and C check simulators the same way); runs: B only.

## Experiment 4 (done, 13:45): game plan first, more drafts -> both rejected

Midpoint report (B, beta + hidden h1): our best h1 strategy was the best of the whole field
(94.8%), but 2 of our 5 h1 runs were weak (29%, 38%): the problem is variance between runs.
Two variants (commit `faa028d`), each against `main` (`full`): `plan` (the strong model writes
a game plan with `analysis.md` at medium effort first; the drafts wait for it, at most 150 s,
and are written from it) and `drafts5` (5 drafts, not 3). Task B, all 8 games x 1 run per arm,
24 runs, $8.74. Evaluated on macOS (isolated): check, panel 4 seeds, cross 6 seeds.

| net per strategy | alpha | beta | gamma | delta | epsilon | eta | **tuning** | zeta | theta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `full` (= `main`) | 0 | 0 | +2 | 0 | +16 | 0 | **+18** | 0 | -6 |
| `plan` | 0 | 0 | -4 | -10 | -1 | 0 | -15 | 0 | **+24** |
| `drafts5` | 0 | 0 | +2 | +10 | -15 | 0 | -3 | 0 | -18 |

All 24 strategies pass `arena check`, 0 forfeits, all within budget ($0.31-0.40) and deadline.
Panel vs baselines: `full` lost 3 of 200 matches, `plan` 5, `drafts5` 7 (all on delta, zeta,
theta). The plan is fast: sol wrote it in about 26 s for about $0.02.

Decision rule (fixed before the runs): a variant replaces `main` only if (1) zero check
failures and forfeits, (2) net >= `full` on at least 4 of the 6 tuning games and in total,
(3) zeta + theta together not worse, (4) within budget and deadline. `plan`: >= on 3 of 6,
tuning total -15 vs +18: **rejected** (despite theta, 12-0-0 against both arms). `drafts5`:
>= on 5 of 6 but total -3 vs +18, held-out worse: **rejected**. `main` is also the best arm
over all 8 games (+12 vs +9 and -21). Both switches were removed again (the code is in
`faa028d`). With 1 run per game the single cells are noisy; the plan's held-out result is
the one worth a follow-up with more runs after the hackathon.

## Experiment 3 (done, 12:50): budget rescue (open proposal 1) -> adopted, weak evidence

When nothing is in flight and the money left no longer covers a full improvement call, send it
at low effort with the drafts' token cap (`Profile.rescue`, switch `HARNESS_ABLATE=rescue`).
Commit `2ff8548`, task B, beta/gamma/epsilon x 2 runs per arm, held-out zeta x 1; 14 runs,
$4.93. Evaluated on macOS (isolated): check, panel 4 seeds, cross-tournament 6 seeds.

| | beta | gamma | epsilon | **total** | zeta (held out) |
|---|---:|---:|---:|---:|---:|
| net per strategy, rescue on | 0.0 | -0.5 | **+4.0** | **+3.5** | 0.0 |
| net per strategy, rescue off (= old `main`) | 0.0 | **+0.5** | -4.0 | -3.5 | 0.0 |
| head-to-head on vs off | 24-0-24 | 22-3-23 | 27-2-19 | 73-5-66 | 6-0-6 |

| per run | rescue on (7 runs) | rescue off (7 runs) |
|---|---|---|
| `arena check` | 7/7 PASS, 0 forfeits | 7/7 PASS, 0 forfeits |
| panel vs baselines | 100% (zeta 96%) | 100% (zeta 96%) |
| spend, mean (max) | $0.369 ($0.402) of 0.50 | $0.335 ($0.373) |
| wall, max | 568 s of 600 (one ended at 341 s: polish seed cap reached) | 570 s |
| improvement calls completed | 16 (3 of them rescues) | 17 |
| rescue attempts / completed | 13 / 3 (10 still `skipped_budget`) | - |
| simulator verified | 7/7 first try | 7/7 first try |

Decision by the rule fixed beforehand: criteria 1-4 hold (beta tie, epsilon better, gamma
worse; total +3.5 vs -3.5; zeta tie; all within budget and deadline), so **rescue is on in
`main`**. Honest reading: the mechanism rarely fires. With $0.06-0.11 left, even a low-effort
sol call (real cost $0.059-0.072, mostly input) usually does not fit; the epsilon gain comes
from a run where the rescue never fired, so most of the difference is noise. In one run
(gamma) a rescue candidate became the champion. Risk is bounded: it only acts when a full call
cannot be made, and its candidate must beat the champion under incumbent protection. No
effect on A (same effort and cap). On C it can act near the end of a run: in the C-lite check
(13:00) an astra call went out at low effort with $0.81 left instead of being skipped. The remaining lever for B money is
prompt caching (open proposal 2), not measured.

## Failure mining of experiment 1 (11:40): applied fixes and open proposals

Two read-only agents analysed all experiment-1 logs (tuning games only) and replayed every
valid candidate (93 strategies) in a round robin on the real games. Key facts: 98 of 99
candidates passed their first probe, 18 of 18 simulators passed first try, 0 forfeits or
malformed actions in 92 final-tournament rows; self-play ranking vs the later cross-tournament:
Spearman 0.88 (it picks well); 8 of 27 challengers promoted (round 1: 6 of 11; later rounds:
2 of 16).

**Applied at 11:45** (defects, with evidence):

- *Provisional champion was protected like an elected one*: the first draft to finish kept
  the crown on exact ties, so 2 of 18 runs delivered the weaker draft (cross 15 vs 48 and
  33 vs 66). Now only a champion chosen by self-play gets incumbent protection
  (`Run.elected`).
- *Task-B call timeout 200 s was too short*: legitimate improvement calls took up to 195 s;
  the one timeout cost $0.15 (a third of the budget) and returned nothing. Now 300 s.
- *Task A lost its selection when luna's simulator needed two repairs*: one repair was
  allowed and the next was gated on 90 s left, so 85 s went unused with 3 valid drafts
  and no tournament. Now 2 repairs and a 60 s gate. Docker check afterwards: repaired
  simulator, tournament, improvement round, polish; 141/180 s, $0.018, exit 0.
- Selection logic is covered by `tests/test_selection.py`.

**Open proposals (not measured; only with an ablation on tuning games, rule fixed first):**

1. *Budget stranding*: 8 of 12 runs ended on `skipped_budget` with $0.07-0.14 and 160-290 s
   left, because the worst-case reservation (~$0.15) is far above the real cost (~$0.086).
   Idea: on `skipped_budget`, retry the improvement with a smaller cap / low effort / the
   cheaper model instead of repeating the same call.
2. *Prompt caching*: the spec is in `input`, so fix/improve calls only hit the 4.3k-token
   system prefix and pay 1.25x on 5-7k spec tokens. Idea: append the spec to `instructions`
   (about $0.03-0.04 per run saved). Verify with two probe calls first.
3. *Plan routes, not targets*: the winning round-1 challengers all planned whole per-unit
   routes (wait, harvest en route, return) plus scenario-wise opponent models. Idea: a
   family-general playbook paragraph or an itinerary-planner persona, so drafts start there.
4. *More drafts*: draft quality varies hugely within a persona (best_response on gamma: +114
   to -106 cross); a draft costs $0.03 vs $0.09 per improvement round, and rounds 2+ rarely
   promote. Idea: 5 drafts in the mid tier (adding lookahead and adaptive), at most 2 rounds.
5. *Give the improver evidence*: 21 of 27 improvement diagnoses said the report shows no
   failure. Idea: include the source of the champion's hardest opponent in `improve.md`.
6. *5xx accounting*: a 520 error after 11 s was booked at the full worst case ($0.10).
   Releasing instead of booking is only right if the gateway does not bill such calls.

Evidence and scripts: the agents' reports are summarised here; their scratch scripts are not
in git.

## Want to help? → [`TASKS.md`](TASKS.md)

Claimable work packages (presentation, a fresh exam game, a read-only risk review, README,
freeze logistics), each with its own paths and a ready-to-paste prompt for Claude Code.

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

Tests: 380/380 on Linux (Docker, 12:00; macOS: 370 pass, 10 skip), incl. real `arena check` (isolated) of deliberately broken
strategies (raise, sleep, infinite loop, numpy import, print flood, non-literal output).

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

## Experiment 2 (done, 11:30): polish and cheaper improvement calls

Commit `aa0297c` (self-play under tournament conditions, sparring off), task B, 2 runs per
arm and game, 18 runs. Cross-tournament net per strategy:

| arm | beta | gamma | epsilon |
|-----|-----:|------:|--------:|
| **full** (polish on, improvement @ medium) | 0.0 | **+6.0** | **+22.0** |
| no polish | 0.0 | -5.5 | -15.5 |
| improvement @ low effort | 0.0 | -0.5 | -6.5 |

Decisions (rules fixed before the results): **polish stays on** (the no-polish arm beats full
on no game), **improvement stays at medium effort** (low beats full on no game). No code
change. Beta is a three-way tie (24-0-24 everywhere: the strategies are nearly equal there).
All 18 strategies pass `arena check`, all runs within budget and deadline, and the full arm
wins 100% of its matches against the baselines on all three games.

## Held-out exam (done, 11:20): does it transfer to untuned games?

zeta and theta were never used for tuning. Task B, 2 runs per arm, commit `aa0297c`:

| game | full vs oneshot (head-to-head, +1/0/-1 matches) | panel vs baselines: full / oneshot |
|------|------------------------------------------------|-----------------------------------|
| theta (hidden demand, surges, typed units) | **44-1-3** | 100% / 93% |
| zeta (renamed fields, nested-dict actions) | **32-0-16** | 94% / 89% |

All 8 strategies pass `arena check`; all runs within budget and deadline. The pipeline's
advantage over the one-shot baseline holds on games it was not tuned on.

## Task C check (C-lite: $2, 900 s, sol + astra; done, 11:25)

| game | wall | spend | improvement rounds | champion | check | vs baselines |
|------|-----:|------:|---:|----------|-------|-------------:|
| beta | 869 / 900 s | $1.11 | 2 | `improve1` (a sol challenger) | PASS | 100% |
| gamma | 874 / 900 s | $0.96 | 1 | `improve1` (a sol challenger) | PASS | 100% |

The large profile works end to end; time binds before money (at the real task C, 1800 s and
$10, there is room for about twice the rounds). In both runs the winning challenger came from
sol, not astra (n = 2, consistent with the calibration: astra is not stronger than sol here).
Astra stays in for diversity: at the evaluation its cost is not ours.

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
deadline, allowed models), never from the task letter. **The tier only picks the plan; the
spending limit is always the task's own `budget_usd`**, hard-capped at 90% of it ($0.027 for
A, $0.45 for B, $9 for C): every call reserves its worst case before it is sent and is refused
if that does not fit. Measured: B runs spend $0.31-0.40, never above $0.45.

| tier | picked when (task.json) | evaluation task | drafts | simulator | improvement |
|------|-------------------------|-----------------|--------|-----------|-------------|
| tiny | budget < $0.10 or deadline < 5 min | A ($0.03, 3 min) | 3x cheapest @ low | cheapest @ low | 2 parallel @ low, while time lasts |
| mid | budget $0.10-1.50 | B ($0.50, 10 min) | 3x strongest @ low | strongest @ low | strongest @ medium, while money lasts |
| large | budget >= $1.50 | C ($10, 30 min) | astra + 2x sol + astra @ low | sol @ medium | 3 parallel per round (astra + 2x sol) |

After the last LLM call, the time that is left plays more self-play seeds (less noisy
selection, no money). Self-play uses exactly the delivered file's settings (0.25 s search per
turn, 0.70 s hard limit), so what wins self-play is what plays in the tournament.

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
  - the pool always contains **idle** (never lose to doing nothing) and the **safe** persona
    (the sparring draft was measured in experiment 1, did not help and was removed);
  - both seats and several seeds (= several random maps) per pairing;
  - the simulator is trusted only after it reproduces the spec's worked examples; strategies
    are written from the spec, never from the simulator's code, so they cannot be tuned to
    its bugs.

## How to run things

Linux or macOS (on a Mac follow [`MAC.md`](MAC.md)): the guard's timer (`signal.setitimer`)
and arena's isolated runner (`os.killpg`) do not exist on Windows, so there use WSL or Docker.

```bash
uv sync                                        # dev env (.venv)
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

Steps 1-4 of the original list (integration, first runs, lessons loop, Docker runs of all
profiles, report, presentation draft) are done. Left: the freeze steps in "Handover" above.

## Open questions / decisions for the team

- Is anyone else changing `src/harness/` right now? If so, tell us before merging.
- Who presents? Draft: `docs/presentation/slides.md`; material: this page, `report.md`.
- Has `nkaslin` been invited to the repo yet (Settings → Access)?
