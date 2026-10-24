# scripts/: the offline measure loop (dev only)

Run harness, measure, improve prompts/code, repeat. Run everything from the repository root.
On Windows, start Python with `uv run`. Isolated play (the real 1 s act limit, real forfeits)
only works on Linux, so pass `--wsl` (re-runs in WSL) or call `/root/venv-harness/bin/python`
inside WSL. `run_harness.sh` switches to WSL by itself.

```bash
# 1. tasks/<game>-<profile>/ for alpha beta gamma delta x A B C (skips games that don't import)
uv run python scripts/make_tasks.py
uv run python scripts/make_tasks.py --games beta --profiles B C-lite   # C-lite: $2, 900 s
uv run python scripts/make_tasks.py --profile X=1,300,gpt-6-luna+gpt-6.1-sol   # custom profile

# 2. three parallel B runs on beta (real money: iterate on A/B). SIGTERM at deadline_s and
#    SIGKILL 30 s later, as in the evaluation. Prints the run dirs; each one gets harness.log
#    and run_meta.json (exit code, wall time, sigterm, commit at launch).
bash scripts/run_harness.sh tasks/beta-B 3

# 3. evaluate: arena check, round robin vs the game's baselines, spend/time vs the task
uv run python scripts/evaluate.py --wsl runs/beta-B            # every run of that task
uv run python scripts/evaluate.py --wsl runs/beta-B/latest --seeds 10 --workers 8
bash scripts/run_harness.sh tasks/beta-B 3 | xargs uv run python scripts/evaluate.py --wsl

# 4. compare two harness versions: +1/0/-1 tournament, same group never paired
mkdir -p runs-v1 && mv runs/beta-B runs-v1/       # old runs; then change the harness, re-run
uv run python scripts/evaluate.py --wsl --cross \
    --group v1='runs-v1/beta-B/*' --group v2='runs/beta-B/*'
#    (or split by time: --group old='runs/beta-B/20261024-09*' --group new='runs/beta-B/*-11*')
```

`evaluate.py` prints markdown tables on stdout: check (PASS / FAIL (reason) / MISSING), W-D-L,
net and score against the baselines, results per baseline, the cross tournament per strategy
and per group, and spend/calls/wall time with flags BUDGET, DEADLINE, SIGTERM, FORFEITS(n)
(panel/cross forfeits) and COSTLOG(n) (calls whose usage costs more than the harness logged;
spend is re-priced from usage). Isolated strategies run under the venv's base Python (no
third-party packages, like the evaluation; `--python ''` = the venv's). It appends one JSON
line per strategy to `runs/eval_log.jsonl` (time, eval commit, run commit, all numbers);
`--no-log` skips that. A draw is |s0 - s1| <= 1% of |s0| + |s1|, and a forfeit loses.
Other options: `--game`, `--check-seeds`, `--cross-seeds`, `--seed-start`, `--skip-check`,
`--skip-panel`, `--strict` (exit 1 on a failed check, any forfeit, budget or deadline).
