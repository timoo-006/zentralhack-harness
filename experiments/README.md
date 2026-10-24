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

Re-evaluate on any machine (macOS/Linux: drop `--wsl`):

```bash
uv run python scripts/evaluate.py --cross --seeds 6 --cross-seeds 6 --workers 8 --no-log \
  --group full='experiments/exp1/full/*/*' --group oneshot='experiments/exp1/oneshot/*/*'
```
