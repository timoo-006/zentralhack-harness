#!/usr/bin/env bash
# The freeze gate: everything that must hold for the commit we hand in. Run it on a clean,
# committed checkout of exactly that commit; it exits 1 on the first failure.
#
#   bash scripts/freeze_check.sh            # tests + Docker run (task A) + conformance check
#   bash scripts/freeze_check.sh --with-b   # also a Docker run of task B (~10 min, ~$0.35)
#
# Costs real money (~$0.02 for A). Needs Docker, uv, and .env with OPENAI_API_KEY.
set -euo pipefail
cd "$(dirname "$0")/.."

with_b=${1:-}
fail() { echo "FREEZE CHECK FAILED: $*" >&2; exit 1; }
step() { printf '\n=== %s ===\n' "$*"; }

step "1/5 clean, committed tree"
[[ -z $(git status --porcelain --untracked-files=no) ]] || fail "uncommitted changes (the evaluation runs the commit, not the tree)"
commit=$(git rev-parse HEAD)
echo "commit $commit"

step "2/5 unit tests"
uv run pytest -q tests/ || fail "tests"
if [[ $(uname -s) == MINGW* || $(uname -s) == MSYS* ]] && command -v wsl >/dev/null; then
    wsl -d "${WSL_DISTRO:-Ubuntu-24.04}" -- bash -lc "cd '$(pwd -W | sed 's#^\(.\):#/mnt/\L\1#')' && PYTHONPATH=\$PWD/src /root/venv-harness/bin/python -m pytest -q tests/" \
        || fail "Linux tests (WSL)"
fi

[[ -f .env ]] || fail ".env missing"
[[ -d tasks/beta-A ]] || uv run python scripts/make_tasks.py --games beta --profiles A B

run_and_check() {  # TASK: Docker run like the evaluation, then arena's conformance check
    local task=$1
    step "Docker run of $task (like the evaluation)"
    local out
    out=$(./run.sh "tasks/$task" 2>&1 | tee /dev/stderr | tail -1)
    [[ $out == *"exit code 0"* ]] || fail "$task: harness did not exit 0"
    local run
    run=$(ls -d "runs/$task"/2* | sort | tail -1)
    [[ -f $run/output/strategy.py ]] || fail "$task: no strategy.py"
    step "conformance of $run (arena check: isolated worker, every seat vs idle, 1 s per act)"
    uv run python scripts/evaluate.py ${WSL_FLAG:-} --seeds 4 --no-log "$run" | tee /dev/stderr \
        | grep -q "| PASS |" || fail "$task: conformance check did not pass"
}
WSL_FLAG=""
if [[ $(uname -s) == MINGW* || $(uname -s) == MSYS* ]]; then WSL_FLAG="--wsl"; fi

step "3/5 task A"
run_and_check beta-A
if [[ $with_b == --with-b ]]; then
    step "4/5 task B"
    run_and_check beta-B
else
    step "4/5 task B skipped (pass --with-b)"
fi

step "5/5 done"
echo "FREEZE CHECK PASSED for $commit"
