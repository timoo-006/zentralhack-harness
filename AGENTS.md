# AGENTS.md

Instructions for coding agents (Claude Code, Codex, Copilot, …) working in this repository.
Read this file fully before changing anything.

## Read first

0. [`docs/STATUS.md`](docs/STATUS.md): what is built, what we measured, money rules, next
   steps. Keep it current when something lands. Claimable work packages, with the paths
   each may touch: [`docs/TASKS.md`](docs/TASKS.md).
1. [`docs/TASK.md`](docs/TASK.md): the official hackathon task (evaluation tasks, scoring,
   deliverables).
2. [`docs/PLAN.md`](docs/PLAN.md): our design and milestones. Follow it, and update its status
   table when a milestone lands.
3. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): the module contracts of `src/harness/`.
   Change an interface there first.
4. [`README.md`](README.md): the template's contract (paths, deadline, budget, local commands).
5. After `uv sync`, the dev games and the match engine are readable in
   `.venv/lib/python3.13/site-packages/arena/` and `.../arena_games_dev/` (each game has
   `spec.md`, `game.py`, `baselines.py`).

## What we are building

This repository is an **agent harness**, not a game bot. At evaluation it runs unattended on a
game it has never seen. It reads `/task/spec.md` and `/task/task.json`, uses an LLM within a
fixed budget and deadline to write, test and improve a strategy, and leaves
`/output/strategy.py`. That file then plays a tournament against the other teams' strategies:
a won match +1, a draw 0, a lost match −1.

The harness runs 30 times: 2 hidden games × 3 tasks × 5 runs.

| task | models                       | budget/run | deadline/run | weight |
|------|------------------------------|-----------:|-------------:|-------:|
| A    | `gpt-6-luna`                 |      $0.03 |        3 min |    10% |
| B    | `gpt-6-luna`, `gpt-6.1-sol`  |      $0.50 |       10 min |    35% |
| C    | `gpt-6.1-sol`, `gpt-6-astra` |     $10.00 |       30 min |    55% |

Everything serves two outcomes, in this order:

1. every generated strategy is valid and **never forfeits**;
2. it **wins** as many matches as possible.

The approach (details in `docs/PLAN.md`): the LLM writes code (a game card, a simulator,
strategies), the harness verifies the simulator against the spec's worked examples, selects
strategies by self-play on that simulator, and always keeps a valid strategy in `/output`.

## Hard constraints: never break these

### The harness (`src/harness/`)

- The entry point is the `harness` script (`harness.__main__:main`), called **with no
  arguments**. `harness TASK_DIR` is for local runs only. Keep both working.
- Writable at evaluation: only `/output` and `/tmp` (1 GB tmpfs). 2 CPUs, 4 GB RAM. No network
  except the OpenAI endpoint. Runs as an unprivileged user.
- SIGTERM arrives at `deadline_s` (wall clock from container start), SIGKILL 30 s later.
  `/output/<strategy_file>` must hold a valid strategy from the first seconds of the run.
  Replace it only atomically (write a temp file, then `os.replace`) and only after it passes
  checks.
- LLM: `OpenAI()` with `client.responses.create` only. Function tools only (no hosted tools),
  `store=False`, no `previous_response_id`, at most 4 concurrent calls, only models in
  `task["allowed_models"]`.
- Account every call with `harness.pricing.cost` and never exceed `task["budget_usd"]`; keep a
  safety margin. At evaluation calls fail once the budget is spent, and over-spending may be
  penalised. Handle API errors and timeouts without crashing.
- Dependencies only via `pyproject.toml` + `uv.lock` (`uv add …`). The evaluation builds with
  its own Dockerfile, so editing ours has no effect there.
- `arena` and `arena-games-dev` are dev dependencies and are **not installed at evaluation**.
  Harness runtime code must not import them (see the open question in `docs/PLAN.md` before
  changing that).
- **No game-specific logic.** The hidden games differ from alpha and beta. Never hard-code
  their maps, constants or rules into the harness or its prompts. Use them only for testing.

### The generated strategy (what the harness outputs)

- One self-contained file: Python 3.13 standard library only. It must not import anything from
  `/output` or anywhere else outside the standard library.
- `class <strategy_class>` with `__init__(self, player_id)` and `act(self, obs)`. A new
  instance is created per match.
- Limits: 10 s for start-up and `__init__`, 1 s per `act`, 2 GB memory, replies ≤ 1 MB.
- Raising, timing out or crashing **forfeits** the match, which counts as a loss. Always wrap
  LLM-written logic in the harness-owned guard: a SIGALRM timer (`signal.setitimer`), `try/except`
  and a safe fallback action.
- Return plain Python literals in exactly the format the spec demands. The dev games want a
  `list` of 3-`int` `tuple`s; lists, bools or numpy ints are malformed. `None` is a no-op in
  every game and is the universal fallback.

### Secrets

Never read, print, log or commit `.env` or API keys. `.env` is gitignored.

## Repository layout

```
src/harness/__main__.py     entry point: t=0 fallback, SIGTERM, wires /task and /output to the pipeline
src/harness/pipeline.py     one run: profiles, stages, selection, promotion
src/harness/clock.py        deadline clock (from container start)
src/harness/budget.py       worst-case reservation per LLM call
src/harness/llm.py          Responses API client: concurrency cap, retries, transcript
src/harness/specdoc.py      deterministic spec parsing (example observations, schema, constants)
src/harness/guard.py        never-forfeit wrapper that assembles the delivered strategy file
src/harness/sandbox.py      runs untrusted code (strategies, simulators) in subprocesses
src/harness/runner_child.py stdlib-only child process: probes, matches, simulator checks
src/harness/tournament.py   incremental self-play round robin, standings
src/harness/prompts/        system prompt, playbook (lessons file), stage prompts, personas
src/harness/pricing.py      cost per call, as the evaluation counts it: do not change the formula
tests/                      unit tests (pytest; Linux-only ones skip on Windows)
testgames/                  our own family games for generalisation tests (dev only)
scripts/                    dev tooling: make tasks, run batches, evaluate (dev only)
run.sh                      run a task in Docker exactly like the evaluation
docs/                       TASK (official), PLAN (design), ARCHITECTURE (contracts), STATUS (now)
tasks/, runs/               generated locally, gitignored
```

New harness modules go in `src/harness/`.

## Commands

```bash
uv sync                                          # install, incl. dev deps (arena, dev games)

# tasks per evaluation profile
uv run arena make-task --game arena_games_dev.alpha --budget-usd 0.03 --deadline-s 180 \
    --model gpt-6-luna --out tasks/alpha-A
uv run arena make-task --game arena_games_dev.beta --budget-usd 0.5 --deadline-s 600 \
    --model gpt-6-luna --model gpt-6.1-sol --out tasks/beta-B
uv run arena make-task --game arena_games_dev.beta --budget-usd 10 --deadline-s 1800 \
    --model gpt-6.1-sol --model gpt-6-astra --out tasks/beta-C

./run.sh tasks/alpha-A                           # like the evaluation: Docker, limits, deadline
uv run --env-file .env harness tasks/alpha-A     # quick local run without Docker

# check a produced strategy and play it against the game's baselines (isolated, also on macOS)
uv run python scripts/evaluate.py runs/alpha-A/latest

# the same with arena directly (Linux or Docker only: macOS rejects arena's memory limit)
uv run arena check runs/alpha-A/latest/output/strategy.py:Strategy --game arena_games_dev.alpha
uv run arena run --game arena_games_dev.alpha \
    --strategies mine=runs/alpha-A/latest/output/strategy.py:Strategy \
    --panel arena_games_dev.alpha.baselines:DoNothing \
            arena_games_dev.alpha.baselines:RandomStrategy \
            arena_games_dev.alpha.baselines:ProportionalSpread \
    --seeds 10 --workers 8

uv run ruff check . && uv run ruff format .
```

Every harness run spends real money from the team's $100 OpenAI budget. Iterate on the A and B
profiles and use C sparingly. Run `./run.sh` (Docker) before relying on a change.

## Working rules

- Make small, focused changes and keep the harness readable. Ruff settings are in
  `pyproject.toml`.
- Before calling a harness change done: run it on alpha and beta (several runs for anything
  that affects reliability), `arena check` every produced strategy, compare it with the
  baselines and with the previous harness version via `arena run`, and confirm that spend
  stayed within budget and the run ended before its deadline. Report numbers, not impressions.
- Log what the harness does to `/output/logs/` (transcript, spend, decisions, evaluation
  results). It helps debugging and the report.
- Keep `docs/PLAN.md` current and collect notes for `report.md`, which is a deliverable at the
  freeze commit.
- If these documents conflict with the code or the official task, say so instead of guessing.

## Definition of done for the freeze

- 100% of runs produce a strategy that passes `arena check`, in all three task profiles, on
  both dev games and on our own test games.
- No run exceeds its budget. Every run ends before its deadline, or survives SIGTERM with a
  valid strategy in place.
- Task-C strategies beat all dev baselines and earlier harness versions.
