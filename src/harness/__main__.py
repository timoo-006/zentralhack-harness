"""The entry point: `harness`.

With no arguments it is what the container runs (see the README's contract): the task is in
/task, the strategy goes to /output. `harness TASK_DIR` runs the same pipeline directly on your
machine, without Docker, into runs/<task>/<time>/ (like run.sh): quicker to iterate on, and
you can step through it with a debugger.

This file only connects the paths to the pipeline; what a run does is in pipeline.py.
"""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from harness import clock  # before the heavy imports: in a container it measures from PID 1


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="harness", description="Without TASK_DIR: the container's /task and /output."
    )
    parser.add_argument(
        "task_dir", nargs="?", type=Path, help="run locally on this task (from arena make-task)"
    )
    args = parser.parse_args()
    if args.task_dir is None:
        # The container: /task is read-only, /output and /tmp are the only writable places.
        code = run(Path("/task"), Path("/output"), Path("/tmp/work"), container=True)
    else:
        task_dir = args.task_dir
        if not (task_dir / "spec.md").is_file() or not (task_dir / "task.json").is_file():
            parser.error(f"{task_dir}: no spec.md/task.json")
        run_dir = Path("runs").resolve() / task_dir.resolve().name
        run_dir /= datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True)
        latest = run_dir.parent / "latest"  # runs/<task>/latest: the newest run
        try:
            latest.unlink(missing_ok=True)
            latest.symlink_to(run_dir.name)
        except OSError:
            pass  # no symlinks (Windows without developer mode)
        print(f"running {task_dir} into {run_dir} (no Docker)", flush=True)
        code = run(task_dir, run_dir / "output", run_dir / "work", container=False)
    sys.stdout.flush()
    sys.stderr.flush()
    # Hard exit: an LLM call still in flight must not keep the process alive past the deadline.
    os._exit(code)


def run(task_dir: Path, output: Path, work: Path, *, container: bool) -> int:
    spec = (task_dir / "spec.md").read_text("utf-8")
    task = json.loads((task_dir / "task.json").read_text("utf-8"))
    logs = output / "logs"
    for d in (logs, work):
        d.mkdir(parents=True, exist_ok=True)
    strategy_path = output / task.get("strategy_file", "strategy.py")

    # Before anything that can fail: a valid strategy, and SIGTERM handled (the file is always
    # valid, so on SIGTERM there is nothing left to do but leave).
    from harness import guard

    try:
        fallback = guard.assemble(None, strategy_class=task.get("strategy_class") or "Strategy")
        guard.write_atomic(strategy_path, fallback)
    except Exception:
        traceback.print_exc()
    clock.install_sigterm_handler(lambda: print("SIGTERM: exiting", flush=True))

    try:
        from harness.pipeline import Run

        Run(
            spec=spec, task=task, strategy_path=strategy_path, work_dir=work, log_dir=logs,
            container=container,
        ).run()  # fmt: skip
    except Exception:
        traceback.print_exc()
    print(f"strategy {'written' if strategy_path.exists() else 'MISSING'}: {strategy_path}")
    return 0


if __name__ == "__main__":
    main()
