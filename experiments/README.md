# experiments/: the evidence behind every decision

Exported from `runs-exp/` (local, gitignored) with `scripts/export_runs.py`: per run the
delivered strategy, `task.json`, `run_meta.json`, the pipeline's `events.jsonl`, the last
self-play report and the tail of `harness.log`. Not included: LLM transcripts and candidate
sources (large; they stay in the local run dirs).

| dir | what | results |
|-----|------|---------|
| `exp1/` | ablation: full / oneshot / sparring (= no sparring) / improve (= no improvement), beta-B gamma-B epsilon-B, 2 runs each | `docs/STATUS.md`, "Experiment 1" |
| `exp2/` | full / polish (= no polish) / improve_low, same games | `docs/STATUS.md`, "Experiment 2" |
| `exp2h/` | held-out exam: full / oneshot on zeta-B, theta-B | `docs/STATUS.md`, "Held-out exam" |
| `clite/` | task-C check: full on beta-C-lite, gamma-C-lite | `docs/STATUS.md`, "Task C check" |
| `bot/` | strategy_class "Bot" in task.json (class-name robustness) | passes `arena check ...:Bot` |
| `exp3/`, `exp3-heldout/` | budget rescue: full (rescue on) / rescue (= off), beta-B gamma-B epsilon-B x 2, zeta-B x 1 | `docs/STATUS.md`, "Experiment 3" |
| `exp4/` | full / plan (game plan first) / drafts5 (5 drafts), all 8 games x 1, task B | `docs/STATUS.md`, "Experiment 4" |

Arm names are `HARNESS_ABLATE` values at the time of the run; some switches were removed later
(`sparring`, `plan`, `drafts5`): the exported strategies stay valid evidence.

Re-evaluate on any machine (macOS or Linux; on Windows add `--wsl`):

```bash
uv run python scripts/evaluate.py --cross --seeds 6 --cross-seeds 6 --workers 8 --no-log \
  --group full='experiments/exp1/full/*/*' --group oneshot='experiments/exp1/oneshot/*/*'
```
