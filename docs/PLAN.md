# Plan: our harness for Zentral Hack 2026

> The design document, written before the build. Update the status column in §7 when a
> milestone lands. Where a guess was replaced by a measurement, a note says so and points to
> the numbers: results are in [`STATUS.md`](STATUS.md), how to run things is in
> [`DEVELOPING.md`](DEVELOPING.md), the module contracts are in
> [`ARCHITECTURE.md`](ARCHITECTURE.md).
> Sources: [`docs/TASK.md`](TASK.md) (official task), [`README.md`](../README.md) (template
> contract), and the `arena` / `arena-games-dev` repositories (installed into `.venv` by
> `uv sync`: `.venv/lib/python3.13/site-packages/arena/` and `.../arena_games_dev/`).

## 1 Goal

Build a harness that, unattended and on a game it has never seen (same family as alpha/beta),
turns `spec.md` into a strategy that **never forfeits** and **wins as many matches as
possible** against the other teams' strategies, within each run's budget and deadline.

Score = Σ over the three tasks of weight × Σ (wins − losses) of our strategies. Task C counts
55%, B 35%, A 10%. Margins do not count, only results.

## 2 Facts that drive the design

### 2.1 Evaluation

| task | models                     | budget/run | deadline/run | weight |
|------|----------------------------|-----------:|-------------:|-------:|
| A    | `gpt-6-luna`               |      $0.03 |        3 min |    10% |
| B    | `gpt-6-luna`, `gpt-6.1-sol`|      $0.50 |       10 min |    35% |
| C    | `gpt-6.1-sol`, `gpt-6-astra`|    $10.00 |       30 min |    55% |

- 2 unseen games × 3 tasks × 5 runs = **30 runs, 30 strategies**.
- **Conformance:** every strategy plays every seat against idle opponents and must never
  forfeit.
- **Tournament** per game and task: all teams' strategies, multiple seeds, both seats; our own
  strategies never meet each other; no reference strategies. Win +1, draw 0, loss −1. Per the
  dev specs a match is a draw if the scores differ by ≤ 1% of |score₀| + |score₁|.
- The dev games' `spec.md` describe Bradley–Terry ratings; the official task says +1/0/−1 sums.
  The official task wins. Either way: maximise the probability of winning each match.
- Ties are broken by an expert panel judging the presentations and approaches, so `report.md`
  and the presentation matter.

### 2.2 Harness runtime contract (template `README.md`)

- The `harness` script (`[project.scripts]`) runs **without arguments**. It reads
  `/task/spec.md` and `/task/task.json` and writes `/output/<strategy_file>` (logs welcome in
  `/output`). `task.json` holds `strategy_file`, `strategy_class`, `budget_usd`, `deadline_s`,
  `allowed_models`, `prices`, `game_name`, `contract_version`.
- **Deadline:** wall clock from container start. SIGTERM at `deadline_s`, SIGKILL 30 s later.
  Python exits on SIGTERM by default, so install a handler.
- **Sandbox:** 2 CPUs, 4 GB RAM, no swap, read-only root file system. Only `/output` and `/tmp`
  (tmpfs, 1 GB) are writable. Runs as an unprivileged user. No network except the OpenAI
  endpoint.
- **LLM:** Responses API with function tools only. No hosted tools, no stored responses
  (`previous_response_id`), at most 4 concurrent calls. Allowed models and budget are enforced;
  over-spending is recorded and may be penalised. Cost per call:
  `harness.pricing.cost(response.usage, task["prices"][model])`.
- **Build:** the organisers use *their* Dockerfile with `uv sync --frozen --no-dev`. Runtime
  dependencies only via `pyproject.toml` / `uv.lock`. `arena` and `arena-games-dev` are dev
  dependencies and are **not available at evaluation**.

### 2.3 Strategy contract

- One file, standard library only, bare Python 3.13, own process, no network, no files. It must
  not import other files from `/output`.
- `Strategy(player_id)` and `act(obs)`. New instance per match. Limits: 10 s start-up and
  `__init__`, 1 s per `act`, 2 GB memory, replies ≤ 1 MB.
- Raising, exceeding a limit or crashing **forfeits**: the strategy plays `None` from then on
  and loses the match whatever the score.
- Return plain literals in exactly the format the spec requires. The dev games want a `list` of
  3-`int` `tuple`s; lists, bools or numpy ints are malformed and the whole action is ignored.
  `None` is a no-op in every game.

### 2.4 Prices and what a run can afford

USD per 1M tokens (from `task.json`; long-context prices above 272k input tokens, avoid):

| model         | input | cached input | output |
|---------------|------:|-------------:|-------:|
| `gpt-6-luna`  |  0.10 |         0.01 |   0.50 |
| `gpt-6.1-sol` |  2.00 |         0.10 |  10.00 |
| `gpt-6-astra` | 10.00 |         1.00 |  50.00 |

Rough capacity for a call of ~8k input + ~4k output tokens (reasoning counts as output; specs
are ~3–5k tokens):

- **A:** luna ≈ $0.003 per call → about 10 calls in 3 min.
- **B:** sol ≈ $0.06 per call → about 6–7 sol calls, or luna for most work.
- **C:** sol ≈ $0.06 per call (about 150), astra ≈ $0.28 per call (about 35). The 30 min
  deadline binds before the money does.

Keep the stable prefix (instructions + spec) first in every call so it gets cached.

> Measured later (calibration, `STATUS.md`): the real cost per call and per effort level.
> Every call reserves its worst case, which is well above its real cost, so on B a run can
> end with money left (see the budget rescue in `ARCHITECTURE.md` §8).

### 2.5 The game family

What the dev games show (hidden games: "same family", own rules and simulator):

- 2 players, discrete turns (dev: 100), simultaneous moves. Units on graph nodes, nodes carry
  demand, contested demand is split by capacity share, units move at most one edge per turn.
- **alpha:** fixed 9-node map, fixed demand, 20 drones of capacity 5.
- **beta:** random 10–16-node map per match (given in the observation), growing and drifting
  demand with an exact 5-turn forecast, 30 drones, upkeep 2 per deployed drone per turn,
  scores can be negative (doing nothing scores 0).
- Every spec contains a copyable Python constants block, exact observation and action
  formats, rules for malformed vs illegal actions, and **worked examples with exact
  observations**. Those examples are our unit tests for a generated simulator.
- Expect new mechanics in hidden games (other unit types, costs, hidden information, capture,
  a different number of turns). Nothing in the harness may assume alpha/beta specifics.

## 3 What the template does and lacks

The template read the spec, always used the cheapest allowed model, gave it one `bash` tool
and looped up to 30 steps, logging the transcript and spend. (Its `agent.py` and `prompt.md`
are gone: the pipeline in `src/harness/pipeline.py` replaced them.)

It lacked budget and deadline control, a SIGTERM handler, a fallback strategy, a conformance
check, a simulator, any evaluation or selection of strategies, model choice per task,
parallel calls and retries. The harness now has all of these.

## 4 Design principles

1. **Never forfeit.** A broken strategy loses every match it plays. 100% conformance comes
   before strength.
2. **Always have a valid strategy.** `/output/<strategy_file>` holds a valid strategy from the
   first second. Every improvement replaces it atomically (`tmp` + `os.replace`) and only after
   passing checks.
3. **The LLM writes code, not moves:** the simulator, heuristics, the strategy. Research (Code
   World Models, DeepMind, ICLR 2026) shows code plus search clearly beats the LLM as a policy.
4. **Verify before trusting.** A simulator must reproduce the spec's worked examples; a
   strategy must pass a conformance run.
5. **Select by self-play.** Play candidates on our simulator, in both seats, over many seeds,
   against a pool (earlier champions and simple bots). Promote only on a clear win-rate margin.
   Static, weak opponents lead to overfitting.
6. **Spend by task.** Plan from `task.json` (budget, deadline, models), never exceed the
   budget, keep safety margins on time and money.
7. **Optimise for winning:** own score minus the opponent's. Denying the opponent demand is
   worth as much as earning it.
8. **General, not game-specific.** No code paths or prompt content tied to alpha/beta; the dev
   games are only for testing.

## 5 Pipeline (one run)

Times are fractions of `deadline_s`; task-specific settings are in §6.

0. **t = 0, no LLM call:** start the clock, install the SIGTERM handler, write a fallback
   strategy that returns `None`, read `task.json`, pick the task profile.
1. **Understand** (cheap model): extract constants, observation schema, action schema, turn
   order, scoring and worked examples into a JSON "game card" in `/tmp`.
2. **Baseline strategy:** a simple greedy strategy, wrapped in the guard, checked against mock
   observations (from the worked examples) and later the simulator. Promote it.
3. **Simulator** (in parallel with 2): the LLM writes `game.py` with an arena-like interface
   (`__init__(seed)`, `observe(player_id)`, `step(actions)`, `is_over()`, `scores()`). Test it
   against the worked examples, then random-play fuzzing (no exceptions; invariants such as
   unit counts conserved). Fix loop on failure.
4. **Improve** (while time and budget allow): up to 4 candidates in parallel with different
   approaches (greedy marginal value, lookahead/search using an embedded copy of the
   simulator, opponent-aware allocation). Round-robin against the champion and the pool, both
   seats, many seeds. Feed lost matches back to the LLM. Promote on a clear improvement.
5. **Finalise** at deadline minus a margin: final conformance check of the champion, write it,
   exit 0.

**Guard wrapper** (owned by the harness, wraps any LLM-written strategy logic):

- `signal.setitimer(ITIMER_REAL, ~0.7)` with a SIGALRM handler, plus `try/except` falling back
  to a safe action (`None`, or a last known valid action).
- A time budget for internal search (~0.4 s per turn, since the tournament machine may be
  busier than ours), and a cheap `__init__`.
- Verified: an `act()` that sleeps 5 s passes `arena check` when wrapped.

> **As built** (contracts: [`ARCHITECTURE.md`](ARCHITECTURE.md) §0, §5, §8). Step 1 became a
> deterministic parse of the spec without an LLM (`specdoc.py`); there is no game card, every
> call carries the full spec. Step 2 became the "safe" draft, which is also the guard's
> fallback core. Steps 3 and 4 run as planned, with extras: a strategic analysis call (large
> budgets only) and, after the last LLM call, more self-play seeds ("polish"). The guard's
> hard limit is 0.70 s per `act` and the strategies' search budget is 0.25 s.

## 6 Task profiles (initial guesses: tune by measurement)

> **Replaced by measurements.** The three profiles below were the starting guesses. The
> profiles the harness runs are derived from `task.json` (budget, deadline, models) and are
> documented in [`README.md`](../README.md), "Settings"; the calibration and experiments
> behind them are in [`STATUS.md`](STATUS.md). The main differences from the guesses: a
> `tiny`/`mid`/`large` tier instead of a task letter; the strong model at low effort for
> drafts (as good as medium effort, much faster); the cheapest model only at low effort; in B
> the simulator and the fixes use the strong model too; the spending limit is 90% of the
> budget.

- **A ($0.03, 3 min, luna only):** no long agent loop. One parallel batch of 2–3 strategy
  candidates and 1 simulator attempt (low/medium reasoning). If the simulator reproduces the
  worked examples, pick the best candidate by quick self-play; otherwise the one that passes
  conformance on mock observations. Done by ~2:30.
- **B ($0.50, 10 min, luna + sol):** luna for parsing, simulator and fixes; sol for 2–4
  strategy candidates and one improvement round. Done by ~9:00.
- **C ($10, 30 min, sol + astra):** the full loop. astra for strategy design and loss analysis
  (few calls), sol for simulator, fixes and variants. Several improvement rounds and a larger
  evaluation (more seeds). Done by ~28:30.

## 7 Milestones

State at Sat 24 Oct, 14:15. Everything is built; experiments 1-4 are done; the freeze is today
at 15:30. Numbers and dates are in [`STATUS.md`](STATUS.md).

| #  | milestone | done when | status |
|----|-----------|-----------|--------|
| M0 | **Setup:** private repo from the template, team and `nkaslin` invited, `uv sync`, tasks for alpha and beta per task profile, `./run.sh` works | template runs in Docker on alpha | done: `uv sync`, tasks and Docker runs work (A, B and a C-lite run in Docker on beta). To confirm: `nkaslin` has access (STATUS.md, freeze checklist) |
| M1 | **Never forfeit:** t=0 fallback, SIGTERM handler, deadline and budget manager, guard wrapper, own conformance check, atomic promotion, task profiles, up to 4 parallel calls | 20/20 runs across tasks and dev games pass `arena check`; no budget overrun; every run ends before its deadline | done, on `main`. All 24 runs of experiment 4 pass `arena check` with 0 forfeits, within budget ($0.31-0.40 of $0.50) and deadline; the midpoint evaluation had 10/10 valid runs |
| M2 | **Strong first attempt:** system prompt with a playbook for this game family, personas, model per role (the planned game card became a deterministic parse, §5) | beats all dev baselines on alpha and beta in tasks B and C | done: Docker beta-B 32-0-0 and beta C-lite 32-0-0 against beta's baselines; calibration of models and efforts in STATUS.md |
| M3 | **Simulator:** generate, verify against worked examples, fuzz, fix loop | reproduces the worked examples in ≥ 90% of runs; agrees with the real `game.py` on random rollouts | done: the simulator passed its checks first try in 18 of 18 runs (experiment 1) and 7 of 7 (experiment 3). A schema-check bug that rejected correct simulators of games with hidden information was fixed (`4fa0036`) |
| M4 | **Self-play selection and improvement:** candidates, opponent pool, statistics, promotion | new harness version beats the previous one in a harness-vs-harness tournament | done: full pipeline beats the one-shot baseline 48-0-0 (beta) and 46-1-1 (gamma). Experiments 1-4 kept or rejected every feature and variant by a rule fixed beforehand |
| M5 | **Generalisation suite:** 2–4 own test games of the family with new mechanics (`spec.md` + `game.py` + baselines) | harness reaches M1–M4 criteria on them too | done: 6 games (gamma, delta, epsilon, zeta, eta, theta); zeta and theta are held out and only measured |
| M6 | **Freeze:** `report.md`, presentation, final `./run.sh` tests in Docker for every task | freeze commit sent to the organisers | open, due today 15:30: `report.md` written, presentation draft in `docs/presentation/`, final Docker runs passed for A, B and C-lite; the freeze hash still has to be sent |

Original split of the work: (1) harness core, budget and deadline (M1); (2) simulator and
verification (M3); (3) strategy wrapper, playbook and selection (M2, M4); (4) test games and
evaluation pipeline (M5).

## 8 How we evaluate the harness

How to set up, run and measure is in [`DEVELOPING.md`](DEVELOPING.md). The method:

- Tasks per profile, e.g.
  `uv run arena make-task --game arena_games_dev.alpha --budget-usd 0.03 --deadline-s 180 --model gpt-6-luna --out tasks/alpha-A`
  (B: `--budget-usd 0.5 --deadline-s 600 --model gpt-6-luna --model gpt-6.1-sol`;
  C: `--budget-usd 10 --deadline-s 1800 --model gpt-6.1-sol --model gpt-6-astra`).
- Run every profile several times (the evaluation uses 5 runs). Record conformance pass rate,
  spend, wall time and results.
- Check each strategy with `arena check`, play it against the baselines with `arena run`, and
  play harness versions against each other with `arena tournament` (a job file whose `tables`
  keep runs of the same harness apart), scored +1/0/−1.
- Keep results in a log so changes are compared on data, not impressions. The decision rule of
  an experiment is written down before it runs. Tune on alpha, beta, gamma, delta, epsilon and
  eta; zeta and theta are held out (measure only).
- Development runs are paid from the team's own OpenAI budget: iterate on A/B profiles, use C
  sparingly (a C-lite task: `--budget-usd 2 --deadline-s 900`).

## 9 Deliverables checklist

- [ ] Private repository; team and organiser `nkaslin` invited (access to be confirmed)
- [ ] Freeze commit pushed and reported to the organisers (today 15:30)
- [x] `report.md` written (to be final at the freeze commit)
- [x] Short presentation: draft in `docs/presentation/slides.md`
- [x] Final Docker runs for the task profiles pass (A, B, C-lite; see STATUS.md)

## 10 Open questions for the organisers

- ~~May `arena` become a runtime dependency (its match runner, isolation and conformance
  check)? Does the evaluation build have network access for git dependencies?~~ Not needed: we
  wrote our own match runner and isolation (`sandbox.py`, `runner_child.py`), which follow
  arena's worker (strategy classes loaded fresh for every match, similar limits, no
  site-packages). `arena` and `arena-games-dev` stay dev dependencies and the harness imports
  neither.
- ~~When exactly is the freeze?~~ Today, Sat 24 Oct, 15:30 (moved from 16:00).
- Do hidden games keep the dev games' conventions (100 turns, `None` as no-op, list of
  tuples)? Partly answered: on the hidden game h1 of the midpoint evaluation, all 5 of our runs
  were valid. Not confirmed for the two final games, so the harness takes every format from
  the spec and assumes none.
- Do the 5 runs per task run in parallel on one machine (CPU contention during self-play)?
  Still open. Self-play uses 2 worker processes and wall-clock budgets, so a slower machine
  plays fewer matches in the same time.

## 11 Background research

- **Code World Models for General Game Playing** (DeepMind, ICLR 2026): the LLM writes the
  simulator plus value and inference functions, MCTS plays; per the abstract it outperforms or
  matches Gemini 2.5 Pro in 9 of the 10 games considered. <https://arxiv.org/abs/2510.04542>
- **GIF-MCTS** (NeurIPS 2024): generate, improve and fix code world models against
  trajectories; code is public. <https://arxiv.org/abs/2405.15383>
- **Agents of Change / HexMachina** (Catan): separate environment discovery from strategy
  improvement; evolve a compiled player through code and simulation.
  <https://arxiv.org/abs/2506.04651>
- **FAMOU co-evolution:** add champions to the opponent pool, evaluate deeply, weight the
  hardest opponents up; evaluator design mattered more than the starting strategy.
  <https://arxiv.org/abs/2606.10389>
- **Code-Space Response Oracles** (DeepMind): the LLM writes best-response code against the
  population mixture. <https://arxiv.org/abs/2603.10098>
- **CodeClash:** LLMs improve bot codebases over tournament rounds; weak strategic reasoning,
  codebases get messy. <https://arxiv.org/abs/2511.00839>
