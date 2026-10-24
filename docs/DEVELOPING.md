# Developing: set up, run, measure, freeze

Everything a developer (or a coding agent) needs to work on the harness, on macOS or Linux,
from a fresh clone to the freeze commit. What is decided and measured is in
[`STATUS.md`](STATUS.md); how the modules fit together in [`ARCHITECTURE.md`](ARCHITECTURE.md).
Everything runs from the repository root. All work happens on `main`.

## 1. Prerequisites

```bash
# macOS: Homebrew (https://brew.sh) first, then
brew install uv coreutils git
brew install --cask docker          # Docker Desktop: ./run.sh and Linux-grade experiments
```

| tool | why |
|------|-----|
| `uv` | Python 3.13 and the locked environment (`uv sync` downloads Python 3.13 if missing) |
| `coreutils` (macOS) | GNU `timeout`, installed as `gtimeout`; `scripts/run_harness.sh` needs it for the deadline |
| `git` | `arena` and `arena-games-dev` are git dependencies: your git must be able to clone `zentralhack-2026-agentic-ai-harness/*` (`gh auth login` or your normal GitHub credentials) |
| Docker | `./run.sh` (a run exactly like the evaluation) and experiments in a Linux container (section 7). Start Docker Desktop once so `docker info` works |

macOS before 13 has no `realpath`, which `run.sh` uses: put
`$(brew --prefix)/opt/coreutils/libexec/gnubin` first in `PATH`. On Apple Silicon, Docker
builds a native `arm64` Linux image (the harness is pure Python): no emulation, no changes.
On Windows use WSL; `scripts/run_harness.sh` and `scripts/evaluate.py --wsl` hop into it.

## 2. Code, key, tests

```bash
git clone <repo-url> zentralhack-harness && cd zentralhack-harness
git checkout main && git pull        # main is always a tested, freezable state
uv sync                              # .venv with harness + arena + dev games + pytest
printf 'OPENAI_API_KEY=sk-...\n' > .env   # the team key: gitignored, never commit or paste it
uv run pytest -q                     # all pass or skip
```

`HARNESS_FAST_TESTS=1` skips the one slow test (about 4 minutes). On macOS a few tests skip by
themselves (the `/proc` clock tests, and the one that runs the `arena check` CLI: macOS
rejects arena's memory limit). The full suite on Linux, as the evaluation's platform:

```bash
docker run --rm -v "$PWD":/w -w /w -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_LINK_MODE=copy \
  ghcr.io/astral-sh/uv:python3.13-bookworm bash -c "uv sync -q --frozen && useradd -m u && \
  chown -R u /tmp/venv && su u -c 'cd /w && UV_PROJECT_ENVIRONMENT=/tmp/venv \
  /usr/local/bin/uv run --frozen --no-sync pytest -q -p no:cacheprovider'"
```

(as an unprivileged user, like the evaluation; about 5 minutes). A test that fails only on one
machine: check it in this container before changing anything.

## 3. Tasks

`tasks/` is gitignored; make it yourself (free):

```bash
uv run python scripts/make_tasks.py --profiles A B C-lite      # all 8 games x A, B, C-lite
uv run python scripts/make_tasks.py --games beta --profiles B   # just one
uv run python scripts/make_tasks.py --profile X=1,300,gpt-6-luna+gpt-6.1-sol   # custom
```

This gives `tasks/<game>-<profile>/` (`spec.md` + `task.json`) for alpha, beta (the dev games),
gamma, delta, epsilon, eta (our tuning games) and zeta, theta (held out: measure only).
Profiles: **A** $0.03 / 180 s / luna, **B** $0.50 / 600 s / luna + sol, **C** $10 / 1800 s /
sol + astra, **C-lite** $2 / 900 s / sol + astra (our cheaper stand-in for C).

## 4. A run like the evaluation (Docker)

```bash
./run.sh tasks/beta-A       # builds the image, runs it with the evaluation's limits
```

Same image recipe (`Dockerfile`), 2 CPUs, 4 GB, read-only root, no capabilities, SIGTERM at
`deadline_s` and SIGKILL 30 s later. Output: `runs/beta-A/<time>/` with `output/strategy.py`,
`output/logs/` and `harness.log`; `runs/beta-A/latest` points to the newest run (also to runs
of other scripts on the same task: name the directory when it matters). Success is exit code 0
and a strategy file. The one difference to the evaluation: calls go straight to OpenAI with
your key, so no gateway refuses a call; the harness's own budget still stops at 90% of
`budget_usd`.

## 5. A run without Docker

```bash
uv run --env-file .env harness tasks/beta-A          # one run, in the foreground (debuggable)
bash scripts/run_harness.sh tasks/beta-A 3           # N runs in parallel, with the deadline
```

`run_harness.sh` starts the harness under GNU `timeout` (SIGTERM at `deadline_s`, SIGKILL 30 s
later), adds `harness.log`, `task.json` and `run_meta.json` (exit code, wall time, sigterm,
commit at launch) to each run directory and prints the directories on stdout. `HARNESS_BIN`
and `PY` override the interpreter (default: `/root/venv-harness/...` if it exists, else
`.venv/bin/...`).

## 6. Evaluate

```bash
uv run python scripts/evaluate.py runs/beta-A/<run>                 # check + panel + spend/time
uv run python scripts/evaluate.py --cross --seeds 4 --cross-seeds 6 --workers 2 --no-log \
  --group full='experiments/exp4/full/beta-B/*' --group plan='experiments/exp4/plan/beta-B/*'
```

Per strategy: `check` (arena's conformance check: isolated, every seat against idle, PASS /
FAIL (reason) / MISSING), the panel (round robin against the game's baselines, both seats,
`--seeds`), `--cross` (a tournament between groups, never pairing two of one group, scored
+1/0/-1 like the evaluation; a draw is |s0 - s1| <= 1% of |s0| + |s1|, a forfeit loses), and
spend, calls and wall time against the task, with flags BUDGET, DEADLINE, SIGTERM,
FORFEITS(n) and COSTLOG(n) (calls that cost more than the harness logged; spend is re-priced
from usage). Read the `| group | W-D-L | net | net/strategy |` rows per game. It appends one
JSON line per strategy to `runs/eval_log.jsonl` (`--no-log` skips that). Other options:
`--game`, `--check-seeds`, `--seed-start`, `--skip-check`, `--skip-panel`, `--strict` (exit 1
on a failed check, a forfeit, budget or deadline), `--in-process` (no isolation: fast, no
1 s limit, not for conformance).

Isolated play (arena's real worker: 1 s per act, real forfeits) works on Linux and macOS. macOS
rejects arena's memory cap (`RLIMIT_AS`), so `evaluate.py` drops only that cap there. The
`arena check` / `arena run` CLIs cannot drop it and fail on macOS with a `BrokenPipeError`:
use `evaluate.py`, or run them in the Linux container of section 2.

## 7. Experiments

An experiment runs arms (values of `HARNESS_ABLATE`, see `harness.pipeline.ABLATIONS`;
`full` = none) on tasks, `RUNS` runs per (arm, task), `JOBS` (arm, task) jobs at once:

```bash
bash scripts/ablate.sh exp5 "full polish" "beta-B gamma-B epsilon-B" 2 2   # EXP ARMS TASKS RUNS JOBS
```

Results land in `runs-exp/<exp>/<arm>/<task>/<run>/`. Concurrent runs = RUNS x JOBS; keep
`2 x concurrent runs <= CPU cores`, or self-play slows down and the deadline-bound results are
distorted. Arms of one task start together, so they share the machine's load. On macOS run
experiments in a Linux container (no GNU `timeout` needed, same platform as the evaluation):

```bash
docker run --rm -v "$PWD":/w -w /w -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_LINK_MODE=copy \
  -e HARNESS_BIN=/tmp/venv/bin/harness -e PY=/tmp/venv/bin/python \
  ghcr.io/astral-sh/uv:python3.13-bookworm bash -c 'uv sync -q --frozen && \
  git config --global --add safe.directory "*" && \
  bash scripts/ablate.sh exp5 "full polish" "beta-B gamma-B" 2 2'
```

The harness is installed in editable mode there: do not edit `src/` while an experiment runs,
or later runs pick up the change. Commit before an experiment (a clean tree): `run_meta.json`
records the commit. Keep the Mac awake (`caffeinate -i -s -t SECONDS`, lid open, on power).

The method, as in experiments 1 to 4 (`STATUS.md`): write the question and the decision rule
down before the runs; compare arms with `evaluate.py --cross` per game; tune only on the
tuning games; measure the held-out games (zeta, theta) but never read their transcripts. B
costs about $0.35-0.40 per run.

## 8. Where results go, and into git

| what | where | in git? |
|------|-------|---------|
| single runs | `runs/<task>/<time>-<pid>/` | no |
| experiments | `runs-exp/<exp>/<arm>/<task>/<run>/` | no |
| tasks | `tasks/` | no (recreate with `make_tasks.py`) |
| eval log | `runs/eval_log.jsonl` | no |
| **exported evidence** | `experiments/<exp>/...` | **yes** |

```bash
uv run python scripts/export_runs.py exp5     # strategy, task.json, run_meta, events, tournament
git add experiments && git commit -m "experiments: exp5"
```

`evaluate.py` works on `experiments/` exactly as on `runs-exp/` (see `experiments/README.md`).

## 9. Freeze (by 15:15; freeze 15:30)

```bash
git checkout main && git pull
uv run pytest -q                                # and the Linux suite of section 2
./run.sh tasks/beta-B                           # exit 0, strategy written
uv run python scripts/evaluate.py --seeds 4 --no-log runs/beta-B/<that run>   # check PASS
git status                                      # clean; .env must not be listed
git push origin main
git rev-parse HEAD                              # the full hash goes to the organisers
```

The organiser account `nkaslin` needs access to the repository (GitHub: Settings, Access).

## Troubleshooting

| symptom | cause / fix |
|---------|-------------|
| `GNU timeout not found` | `brew install coreutils` (gives `gtimeout`), or run in the Linux container |
| `.venv/bin/harness: not found` | `uv sync` in the repository root |
| `uv sync` fails cloning `arena` | your git has no access to the GitHub org: log in (`gh auth login`) |
| `.env missing` | section 2 |
| `realpath: command not found` in `run.sh` | macOS before 13: section 1 |
| `arena check` / `arena run`: `BrokenPipeError` on macOS | macOS rejects arena's memory limit: use `scripts/evaluate.py` (section 6) |
| Docker: `Cannot connect to the Docker daemon` | start Docker Desktop and wait until `docker info` works |
| `evaluate.py` row `FAIL (evaluator error, 0/0)` | the run directory is still running or empty (e.g. `latest` points to a run in progress) |
