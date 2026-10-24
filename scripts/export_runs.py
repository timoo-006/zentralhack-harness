"""Copy the small, reusable part of experiment runs into git: experiments/<exp>/...

    uv run python scripts/export_runs.py exp1 exp2h clite      # from runs-exp/<exp>/
    uv run python scripts/export_runs.py --all

Per run it keeps what re-evaluation needs (the delivered strategy, task.json, run_meta.json)
and what explains a run (events.jsonl, tournament.txt, harness.log tail), but not the LLM
transcripts or the candidate sources (megabytes, and in the run's own logs anyway). The
layout stays <arm>/<task>/<run>/, so scripts/evaluate.py works on experiments/ exactly as on
runs-exp/, on any machine.
"""

import argparse
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "runs-exp"
DST = REPO / "experiments"
KEEP = [
    "output/strategy.py",
    "output/logs/events.jsonl",
    "output/logs/tournament.txt",
    "task.json",
    "run_meta.json",
]
LOG_TAIL_LINES = 60


def export(exp: str) -> int:
    runs = 0
    for strategy in sorted((SRC / exp).glob("*/*/*/output/strategy.py")):
        run = strategy.parent.parent
        target = DST / exp / run.relative_to(SRC / exp)
        for rel in KEEP:
            src = run / rel
            if src.is_file():
                (target / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, target / rel)
        log = run / "harness.log"
        if log.is_file():
            tail = log.read_text("utf-8", errors="replace").splitlines()[-LOG_TAIL_LINES:]
            (target / "harness.log").write_text("\n".join(tail) + "\n", "utf-8")
        runs += 1
    return runs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("exps", nargs="*", help="experiment names under runs-exp/")
    parser.add_argument("--all", action="store_true", help="every experiment in runs-exp/")
    args = parser.parse_args()
    exps = sorted(p.name for p in SRC.iterdir() if p.is_dir()) if args.all else args.exps
    for exp in exps:
        print(f"{exp}: {export(exp)} run(s) -> {DST / exp}")


if __name__ == "__main__":
    main()
