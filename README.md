# harness

Our Zentral Hack 2026 harness. Given a game's written specification, it has the LLM write
**several candidate strategies and a simulator of the game**, verifies both, picks the best
candidate by **self-play**, improves it with the strongest allowed model while time and money
last, and delivers it wrapped in a **never-forfeit guard**. A valid `strategy.py` is on disk
from the first second, and the harness always exits 0.

| read | for |
|------|-----|
| [`docs/STATUS.md`](docs/STATUS.md) | **start here**: what is built, what we measured, open decisions, the freeze checklist |
| [`docs/MAC.md`](docs/MAC.md) | continue on a Mac: setup, running, evaluating |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | the contract of every module in `src/harness/` |
| [`docs/PLAN.md`](docs/PLAN.md) | why it is built this way |
| [`scripts/README.md`](scripts/README.md) | the offline measure loop: make tasks, run, evaluate, compare |
| [`experiments/README.md`](experiments/README.md) | the exported runs behind every decision, and how to re-evaluate them |
| [`docs/TASK.md`](docs/TASK.md), [`docs/TASKS.md`](docs/TASKS.md) | the official task; open work packages |

```
src/harness/
  __main__.py     # the `harness` command: paths, SIGTERM handler, t=0 fallback file, exit 0
  pipeline.py     # one run: profiles from task.json, stages, ablation switches, constants
  clock.py        # deadline clock, counted from container start
  budget.py       # worst-case reservation per LLM call, 90% of budget_usd
  llm.py          # Responses API client: 4 concurrent, retries, transcript, extract_code
  pricing.py      # what a call costs, as the evaluation counts it
  specdoc.py      # deterministic spec parse: example observations, observation keys, constants
  guard.py        # assembles the delivered file: core + fallback, timers, literal check
  sandbox.py      # probe and match runner for untrusted code (subprocesses, stdlib only)
  runner_child.py # the stdlib-only child script that sandbox.py starts
  tournament.py   # incremental round-robin self-play, robust ranking, text report
  prompts/        # system.md, playbook.md (lessons), simulator.md, strategy.md, repair_*.md,
                  # improve.md, analysis.md, personas.json
tests/            # unit tests per module; tests/fixtures/ has broken strategies and sims
testgames/        # our own games (gamma, delta, epsilon, zeta, eta, theta): spec, simulator
                  # and baselines each; zeta and theta are held out (never tuned on)
scripts/          # dev only: make_tasks.py, run_harness.sh, ablate.sh, evaluate.py, export_runs.py
experiments/      # exported experiment runs (strategies, events, task.json): the evidence
docs/             # STATUS, MAC, ARCHITECTURE, PLAN, TASK, TASKS
tasks/ runs/ runs-exp/  # local and gitignored: tasks, run outputs, experiment outputs
run.sh            # run it on a task in Docker the way the evaluation does
Dockerfile        # the sandbox your harness runs in at evaluation time
.vscode/launch.json  # run or debug it without Docker in VS Code
```

## The contract

At evaluation time your harness runs unattended, on a game it has never seen. Three things are
fixed; everything else in this repository is yours to change.

1. **The build.** We build your repository at the freeze commit with **our** copy of the
   `Dockerfile`, which runs `uv sync --frozen` on your `pyproject.toml` and `uv.lock`.
2. **The run.** We start the container, which runs the `harness` script your `pyproject.toml`
   defines (`[project.scripts]`), **with no arguments**, as an unprivileged user, with:

   | path              | what                                                                    |
   |-------------------|-------------------------------------------------------------------------|
   | `/task/spec.md`   | the game's complete written specification (read-only)                  |
   | `/task/task.json` | `strategy_file`, `strategy_class`, budget, deadline, allowed models, prices |
   | `/output/`        | write `<strategy_file>` here, defining `<strategy_class>`; logs welcome |
   | `/tmp/`           | scratch space (tmpfs, 1 GB)                                             |

   Nothing else is writable: not the root file system, not your code or its virtualenv. There
   are CPU, memory and process limits (2 CPUs, 4 GB of RAM, no swap).
3. **The result.** When the harness exits, or at `deadline_s` (wall clock from container
   start), we take `/output/<strategy_file>`. At the deadline your harness gets SIGTERM, and
   SIGKILL 30 seconds later (Python exits on SIGTERM by default: install a handler to use those
   30 seconds). So write a fallback strategy early and improve it.

`./run.sh` does exactly this on your machine (see [Run it locally](#run-it-locally)).

More about what goes in and out:

- After the run, only `<strategy_file>` is copied into the tournament, on its own: it must not
  import other files from `/output`.
- The strategy is a single Python file: standard library only, a class with
  `__init__(self, player_id)` and `act(self, obs)`. It does not need to import anything from us.
- LLM access: the OpenAI Responses API only (see [Models and budget](#models-and-budget)). The
  container has no other network access: no PyPI, no web.
- The evaluation builds the image with **its own copy** of the `Dockerfile`. Add Python
  dependencies through `pyproject.toml` / `uv.lock` (`uv add ...`); other changes to the
  `Dockerfile` have no effect at evaluation time.

## Models and budget

`task.json` gives the `allowed_models`, their `prices` and a `budget_usd`. **At evaluation both
are enforced**: other models are refused, and once the budget is spent every further call
fails (spending past it is recorded and may be penalised). **During development nothing
enforces them**, so develop as if it did: keep to the allowed models and track your spend.
`harness.pricing.cost(response.usage, task["prices"][model])` gives what a call costs;
`harness.budget.Budget` adds it up for every call and the harness logs the spend.

Use the openai SDK as the template does: `OpenAI()` reads the key and endpoint from the
environment (your own key in development, one for the run at evaluation), so the same code
works in both. Only the Responses API with function tools is available: no hosted tools (web
search, code interpreter, ...), no stored responses (`previous_response_id`), at most 4 calls
at a time.

## Run it locally

You need git, Docker, [uv](https://docs.astral.sh/uv/) and an OpenAI key. Everything runs from
your clone of this repository: `uv sync` also installs, for development only,
[arena](https://github.com/zentralhack-2026-agentic-ai-harness/arena) (the `arena` command and
the strategy contract) and
[arena-games-dev](https://github.com/zentralhack-2026-agentic-ai-harness/arena-games-dev) (the
dev games: spec, simulator and baselines). Read their code on GitHub; you do not need to clone
them.

```bash
git clone <your repository> harness
cd harness
uv sync

# 1. your key, for the harness (gitignored)
echo "OPENAI_API_KEY=sk-..." > .env

# 2. a task for the dev game alpha: exactly what the harness gets as /task
uv run arena make-task --game arena_games_dev.alpha --budget-usd 1 --deadline-s 600 \
    --model gpt-6-luna --model gpt-6.1-sol --out tasks/alpha

# 3. run the harness on it, as the evaluation does
./run.sh tasks/alpha
```

`run.sh` builds the image and runs it with the evaluation's mounts, limits and deadline. Only
the budget is not enforced (see above). It works from Linux, macOS and Git Bash on Windows. Each
run gets its own directory `runs/alpha/<time>/`; the newest is `runs/alpha/latest/`, with the
strategy and logs (`logs/events.jsonl`, `transcript.jsonl`, `candidates/`, `tournament.txt`) in
`output/` and the container's output in `harness.log`.

Then check the strategy and play it against the game's baselines:

```bash
uv run arena check runs/alpha/latest/output/strategy.py:Strategy --game arena_games_dev.alpha
uv run arena run --game arena_games_dev.alpha \
    --strategies mine=runs/alpha/latest/output/strategy.py:Strategy \
    --panel arena_games_dev.alpha.baselines:DoNothing \
            arena_games_dev.alpha.baselines:RandomStrategy \
            arena_games_dev.alpha.baselines:ProportionalSpread \
    --seeds 10 --workers 8
```

Both options take any `[name=]path/to/file.py:Class` or `package.module:Class`. Without
`--panel`, the `--strategies` play each other, so you can compare any strategies, e.g. two runs
of your harness. The other dev games work the same way (see `arena-games-dev`'s README).

### Without Docker, and in the debugger

For quicker iterations, run the same agent directly on your machine:

```bash
uv run --env-file .env harness tasks/alpha
```

It writes into `runs/alpha/<time>-<pid>/` (the pid keeps parallel runs of one task apart; the
commands above work on it too), with `output/` and the working directory `work/`; the log goes
to your terminal. `scripts/run_harness.sh tasks/alpha 3` starts several runs in parallel (see
[`scripts/README.md`](scripts/README.md)). To step
through it, open this repository in VS Code (with the Python extension), set breakpoints, and
start **harness (local)** in Run and Debug (F5); it asks for the task directory.

This is not the evaluation's environment, so check with `run.sh` before you rely on it:

- The strategies and simulators the LLM writes run on your machine, as you, in subprocesses
  with a time limit but no container around them.
- No CPU or memory limits, and your machine's Python and OS rather than the image's. The
  deadline clock counts from the process start, not from a container start. The guard's timer
  and arena's isolated runner need Linux or macOS; on Windows use WSL or Docker for anything
  real.

### Updating arena and the dev games

When we announce a fix to arena or arena-games-dev, run this in your repository:

```bash
uv sync --upgrade-package arena --upgrade-package arena-games-dev
```

It moves both to their latest `main` and records the new commits in `uv.lock`; commit that
change. Neither is part of the evaluation image (it installs without the dev dependencies), so
this only affects your local runs. You can also read the installed code offline in
`.venv/lib/python3.13/site-packages/arena/` and `.../arena_games_dev/`.

## Settings

The evaluation runs `harness` without arguments (`harness TASK_DIR` is only for local runs, see
above), so there are no command-line options and no configuration file. What a run does is
decided by `task.json`, by constants in code and, for development only, by one environment
variable.

**Profile tiers, derived from `task.json`.** `make_profile` in `src/harness/pipeline.py` picks a
plan from the task's `budget_usd`, `deadline_s` and `allowed_models`, never from a task name.
The spending limit is always the task's own budget, hard-capped at 90% of it: every call
reserves its worst case before it is sent and is refused if that does not fit.

| tier | picked when | typical task | drafts | simulator | improvement rounds |
|------|-------------|--------------|--------|-----------|--------------------|
| `tiny` | budget < $0.10 or deadline < 300 s | A ($0.03, 3 min) | 3x strongest @ low | cheapest @ low | 2 parallel @ low |
| `mid` | budget < $1.50 | B ($0.50, 10 min) | 3x strongest @ low | strongest @ low | strongest @ medium, one at a time |
| `large` | budget >= $1.50 | C ($10, 30 min) | 4: strongest, cheapest, cheapest, strongest @ low | cheapest @ medium | 3 parallel @ medium, after an analysis call |

"Cheapest" and "strongest" are the least and most expensive of `allowed_models`. The numbers
behind these choices are in [`docs/STATUS.md`](docs/STATUS.md).

**Development switches: `HARNESS_ABLATE`.** A comma-separated list that switches features off
(`improve_low` changes one), so each feature is kept only on measured evidence. The evaluation
sets no such variable, so it always runs the full harness. Unknown names are ignored.

```bash
HARNESS_ABLATE=oneshot uv run --env-file .env harness tasks/beta-B
```

| switch | effect |
|--------|--------|
| `improve` | no improvement rounds |
| `selfplay` | no simulator, so no self-play and no improvement: the first valid draft wins |
| `repair` | no repair calls for strategies or simulators |
| `analysis` | no strategic analysis call (only the `large` tier makes one) |
| `robust_rank` | rank by raw standings: no per-opponent ranking, no incumbent protection |
| `oneshot` | baseline: one draft, no simulator, no repair; the guard stays on |
| `polish` | no extra self-play seeds in the time left after the last LLM call |
| `improve_low` | variant: improvement calls at low effort (cheaper, more rounds) |

`scripts/ablate.sh` runs whole experiments over these switches.

**Constants at the top of `pipeline.py`.**

| constant | value | meaning |
|----------|-------|---------|
| `FINAL_TIME_BUDGET_S` | 0.25 | search time per `act` in the delivered file (the strategy's `TIME_BUDGET`) |
| `FINAL_ACT_LIMIT_S` | 0.70 | hard limit per `act` in the delivered file (arena allows 1.0 s); self-play uses the same two |
| `PROMOTE_MARGIN` | 0.03 | a challenger needs this much more mean per-opponent score and must not lose the head-to-head |
| `CLEAR_MARGIN` | 0.12 | ... or this much more, whatever the head-to-head says |
| `MAX_POLISH_SEEDS` | 24 | cap on the extra self-play seeds from leftover time |
| `SELFPLAY_CLASS` | `Strategy` | class name of internal self-play files; only the delivered file uses `strategy_class` |

The rest are fields of `Profile`, filled in by `make_profile`: models, efforts, token caps,
parallel calls, seeds, repair counts, and the flags `polish` and `robust_rank`.

**Where the logs go.** In the container `/output/logs/`; locally
`runs/<task>/<time>-<pid>/output/logs/`:

| file | content |
|------|---------|
| `events.jsonl` | every pipeline decision, with time and spend (start, stages, promotions, errors) |
| `transcript.jsonl` | every LLM call: model, effort, status, usage, cost, spend so far, input and output |
| `candidates/` | all generated strategy and simulator sources, by id |
| `tournament.txt` | the last self-play report |

`harness.log` next to `output/` holds the container's (or, with `run_harness.sh`, the
process's) output. Evaluate runs with `scripts/evaluate.py`, see
[`scripts/README.md`](scripts/README.md).
