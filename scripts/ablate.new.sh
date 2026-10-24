#!/usr/bin/env bash
# An ablation experiment: the same tasks, the harness with and without features.
#
#   scripts/ablate.sh EXP "full oneshot sparring improve" "beta-B gamma-B" RUNS [JOBS]
#
# Each arm is a value for HARNESS_ABLATE (see harness.pipeline.ABLATIONS; "full" = none).
# Every (arm, task) gets RUNS parallel runs; at most JOBS (arm, task) jobs run at once.
# Runs end up in runs-exp/EXP/<arm>/<task>/<run>/, ready for
#
#   uv run python scripts/evaluate.py --wsl --cross \    # (drop --wsl on macOS and Linux)
#       --group full='runs-exp/EXP/full/*/*' --group oneshot='runs-exp/EXP/oneshot/*/*'
#
# Portable: macOS ships bash 3.2, which has no `wait -n`, so the running jobs are tracked by
# pid and polled. Needs scripts/run_harness.sh (and with it GNU timeout: brew install coreutils).
set -euo pipefail
cd "$(dirname "$0")/.."

exp=${1:?usage: scripts/ablate.sh EXP "ARMS" "TASKS" RUNS [JOBS]}
arms=${2:?arms}
tasks=${3:?tasks}
runs=${4:?runs per arm and task}
jobs=${5:-4}
out=runs-exp/$exp
mkdir -p "$out"
echo "experiment $exp: arms [$arms] tasks [$tasks] x$runs, $jobs jobs at once -> $out"

one() {  # one (arm, task) job: run, then move its run dirs under the experiment
    local arm=$1 task=$2 flags=$1 log
    [[ $arm == full ]] && flags=""
    mkdir -p "$out/$arm/$task"
    log=$out/$arm/$task/driver.log
    HARNESS_ABLATE=$flags bash scripts/run_harness.sh "tasks/$task" "$runs" >"$log" 2>&1 || true
    { grep -E '^runs/' "$log" || true; } | sort -u | while read -r dir; do
        [[ -d $dir ]] && mv "$dir" "$out/$arm/$task/"
    done
    echo "done: $arm $task ($(ls -d "$out/$arm/$task"/*/ 2>/dev/null | wc -l | tr -d ' ') runs)"
}

pids=""  # pids of the jobs started and not yet seen finished
nrun=0   # how many of them are still running (set by prune)
prune() {  # drop finished pids from $pids; sets nrun. Runs in the current shell, not in $(...)
    local p alive=""
    nrun=0
    for p in $pids; do
        if kill -0 "$p" 2>/dev/null; then
            alive="$alive $p"
            nrun=$((nrun + 1))
        fi
    done
    pids=$alive
}

for task in $tasks; do
    for arm in $arms; do
        prune
        while ((nrun >= jobs)); do
            sleep 1
            prune
        done
        one "$arm" "$task" &
        pids="$pids $!"
        sleep 2  # run dirs are named by the second (and pid): keep starts apart
    done
done
wait
echo "experiment $exp finished: $out"
