# Continuing on a Mac

Step by step, from a fresh clone to the freeze commit. Read [`STATUS.md`](STATUS.md) first
(what is decided, what is open); this page is only about *how to work from macOS*. Everything
runs from the repository root. Apple Silicon and Intel both work.

## 1. Prerequisites

```bash
# Homebrew (https://brew.sh) first, then:
brew install uv coreutils git bash
brew install --cask docker          # Docker Desktop, only needed for ./run.sh
```

| tool | why |
|------|-----|
| `uv` | Python 3.13 + the locked environment (`uv sync` downloads Python 3.13 itself if missing) |
| `coreutils` | GNU `timeout`, installed as **`gtimeout`**; `scripts/run_harness.sh` uses it for the deadline and refuses to start without it |
| `git` | the dev dependencies `arena` and `arena-games-dev` are git dependencies on GitHub: your git must be able to clone `zentralhack-2026-agentic-ai-harness/*` (log in with `gh auth login` or use your normal GitHub credentials) |
| `bash` | optional: macOS ships bash 3.2 and all scripts work with it; a newer bash changes nothing |
| Docker Desktop | `./run.sh` (the run that is like the evaluation). Start the app once so `docker info` works |

macOS older than 13 has no `realpath`, which `run.sh` uses: `brew install coreutils` and put
`$(brew --prefix)/opt/coreutils/libexec/gnubin` first in `PATH`.

**Apple Silicon:** the evaluation image is Linux. `python:3.13-slim` and the `uv` image in the
`Dockerfile` are multi-arch, so Docker Desktop builds a native `arm64` Linux image; no
emulation, no changes. (Pure-Python code only; the harness has one dependency, `openai`.)

## 2. Get the code

```bash
git clone <repo-url> zentralhack-harness && cd zentralhack-harness   # or: git pull
git checkout main && git pull        # main is always a tested, freezable state
uv sync                              # creates .venv with harness + arena + dev games + pytest
```

## 3. The key (`.env`)

Create `.env` in the repository root (it is gitignored, **never commit it**, never paste it
into a chat or a PR):

```bash
printf 'OPENAI_API_KEY=sk-...\n' > .env
```

It is the team's shared key; ask Elias for it. Every local run spends real money from it (see
the money rules below).

## 4. Tests

```bash
uv run pytest -q
```

All tests should pass or skip. Tests that need Linux specifics (the `/proc` clock tests, and
the one that runs the `arena check` CLI: macOS rejects arena's memory limit) skip on macOS by
themselves. `HARNESS_FAST_TESTS=1 uv run pytest -q` skips the one slow test
(about 4 minutes). A test that fails *only* on the Mac: do not "fix" it blindly, check it
against Docker (step 8) or tell Elias.

## 5. Recreate the tasks

`tasks/` is gitignored, so on a new machine you make it yourself (this costs nothing):

```bash
uv run python scripts/make_tasks.py --profiles A B C-lite     # all 8 games x A, B, C-lite
uv run python scripts/make_tasks.py --games beta --profiles B # just one
uv run python scripts/make_tasks.py --profiles C              # the real C ($10, 30 min), only if needed
```

This gives `tasks/<game>-<profile>/` (`spec.md` + `task.json`) for alpha, beta, gamma, delta,
epsilon, eta and the held-out zeta, theta. Profiles: **A** $0.03 / 180 s, **B** $0.50 / 600 s,
**C** $10 / 1800 s, **C-lite** $2 / 900 s (our cheaper stand-in for C). Custom:
`--profile X=1,300,gpt-6-luna+gpt-6.1-sol`. `scripts/README.md` has more.

## 6. A local run (no Docker)

```bash
bash scripts/run_harness.sh tasks/beta-A 1        # N parallel runs, default 1
```

It starts `.venv/bin/harness` under `gtimeout` with the task's deadline (SIGTERM at
`deadline_s`, SIGKILL 30 s later, like the evaluation), and prints the run directory on stdout,
`runs/beta-A/<time>/`, holding `output/strategy.py`, `output/logs/` (`events.jsonl`,
`transcript.jsonl`, `candidates/`, `tournament.txt`), `harness.log`, `task.json` and
`run_meta.json` (exit code, wall time, commit). Progress goes to stderr. If `HARNESS_BIN` /
`PY` are set they win; otherwise `/root/venv-harness/...` (Elias' WSL) if it exists, else
`.venv/bin/...`.

An experiment (arms x tasks x runs) is `scripts/ablate.sh`; its results land in
`runs-exp/<exp>/<arm>/<task>/<run>/`:

```bash
bash scripts/ablate.sh exp3 "full polish" "beta-B gamma-B epsilon-B" 2 4   # EXP ARMS TASKS RUNS JOBS
```

Costs real money: B is about $0.30-0.40 per run. Arms are values of `HARNESS_ABLATE` (see
`harness.pipeline.ABLATIONS`).

## 7. Evaluate

```bash
uv run python scripts/evaluate.py runs/beta-A                    # check + panel vs baselines + spend/time
uv run python scripts/evaluate.py --cross --seeds 6 --cross-seeds 6 --workers 8 --no-log \
  --group full='experiments/exp1/full/*/*' --group oneshot='experiments/exp1/oneshot/*/*'
```

On macOS **no `--wsl`** (that flag only exists to hop from Windows into WSL). Read the
`| group | W-D-L | net | net/strategy |` rows per game; `scripts/README.md` explains every
column and flag.

**Isolation on macOS:** `scripts/evaluate.py` plays isolated (arena's real worker: 1 s per
act, real forfeits) on macOS too. macOS rejects arena's memory cap (`RLIMIT_AS`: every worker
would die at start-up with `ValueError: current limit exceeds maximum limit`), so on macOS it
plays without that cap; the time limit, forfeits and reply checks are the real ones. Verified:
`experiments/clite/full/beta-C-lite/*` gives `PASS` and 16-0-0. The `arena check` and
`arena run` CLIs cannot drop the cap and fail on macOS with a `BrokenPipeError`: use
`evaluate.py`, or run them in Docker (step 8).

## 8. A run like the evaluation (Docker)

```bash
./run.sh tasks/beta-A                # builds the image, runs it with the evaluation's limits
```

Same image recipe (`Dockerfile`), 2 CPUs, 4 GB, read-only root, no capabilities, deadline from
`task.json`. Output: `runs/beta-A/<time>/` with `output/strategy.py`, `output/logs/` and
`harness.log`; `runs/beta-A/latest` points to it. Success is "exit code 0" and a strategy
file written. Evaluate it like any other run: `uv run python scripts/evaluate.py runs/beta-A/latest`.
The only difference to the evaluation: LLM calls go straight to OpenAI with your `.env` key
(no gateway), so nothing refuses a call after the budget is spent; the harness's own budget
guard still stops at 90% of `budget_usd`.

## 9. Where results go, and into git

| what | where | in git? |
|------|-------|---------|
| single runs | `runs/<task>/<time>/` | no (gitignored) |
| experiments | `runs-exp/<exp>/<arm>/<task>/<run>/` | no (gitignored) |
| tasks | `tasks/` | no (recreate with `make_tasks.py`) |
| eval log | `runs/eval_log.jsonl` | no |
| **exported evidence** | `experiments/<exp>/...` | **yes** |

Export the small, reusable part of experiment runs (strategy, `task.json`, `run_meta.json`,
`events.jsonl`, `tournament.txt`, log tail; no transcripts) and commit it:

```bash
uv run python scripts/export_runs.py exp3          # from runs-exp/exp3/
uv run python scripts/export_runs.py --all
git add experiments && git commit -m "experiments: exp3"
```

`scripts/evaluate.py` works on `experiments/` exactly as on `runs-exp/`, on any machine (see
`experiments/README.md`).

## 10. Money rules (our own OpenAI key)

- At **evaluation** the organisers' key pays; only our **test runs** cost us. We have about 150
  CHF in total; target: stay under $30. Check STATUS.md for what has been spent.
- Iterate with **A and B** tasks. For C use **C-lite** (`$2`, 900 s). At most 1-2 real C
  runs before the freeze.
- No new experiment without a question and a decision rule written down first (STATUS.md has
  the pattern). Several runs per arm, over several games: single runs are noise.
- Do not tune on the held-out games **zeta** and **theta**: measure only.
- Never commit `.env`; never print the key into logs or chats.

## 11. Freeze steps (by 15:15, freeze is 15:30)

Same as the handover section in STATUS.md, for the Mac:

```bash
uv run pytest -q                                   # all pass (Linux-only ones skip)
./run.sh tasks/beta-A                              # Docker, like the evaluation: exit 0, strategy written
git status                                         # clean; .env must not be listed
git checkout main && git merge --ff-only feat/harness-v1 && git push origin main
git rev-parse HEAD                                 # send this full hash to the organisers (T5)
```

(If `main` already has everything, skip the merge; `git log origin/main -1` shows what is
pushed.) Also: `report.md` committed with the "Measurements" filled in from STATUS.md, and the
organiser account **`nkaslin`** has access to the repository (GitHub: Settings, Access).

## Troubleshooting

| symptom | cause / fix |
|---------|-------------|
| `GNU timeout not found` | `brew install coreutils` (gives `gtimeout`) |
| `.venv/bin/harness: not found` | run `uv sync` in the repository root |
| `uv sync` fails cloning `arena` | your git has no access to the GitHub org: log in (`gh auth login`) |
| `.env missing` | step 3 |
| `realpath: command not found` in `run.sh` | macOS before 13: see step 1 |
| `arena check` / `arena run`: `BrokenPipeError` | macOS rejects arena's memory limit: use `scripts/evaluate.py` (step 7) |
| Docker: `Cannot connect to the Docker daemon` | start Docker Desktop and wait for it to be ready |
