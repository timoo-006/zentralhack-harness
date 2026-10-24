# scripts/: the offline measure loop (dev only)

| script | what |
|--------|------|
| `make_tasks.py` | `tasks/<game>-<profile>/` for the dev and test games (A, B, C, C-lite, custom) |
| `run_harness.sh` | N parallel harness runs under the task's deadline, with `run_meta.json` |
| `ablate.sh` | an experiment: arms (`HARNESS_ABLATE` values) x tasks x runs |
| `evaluate.py` | arena check, panel vs baselines, cross-tournament, spend and time |
| `export_runs.py` | copy the reusable part of experiment runs into `experiments/` |

How to use them, step by step: [`docs/DEVELOPING.md`](../docs/DEVELOPING.md).
