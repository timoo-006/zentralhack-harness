#!/usr/bin/env bash
# Run the harness locally (Linux, macOS, or Windows Git Bash via WSL), without Docker, under
# the evaluation's deadline: SIGTERM at deadline_s (from task.json), SIGKILL 30 s later.
#
#   scripts/run_harness.sh TASK_DIR [N]      # N runs in parallel (default 1)
#
# Each run is `harness TASK_DIR` (local mode: runs/<task>/<time>/output/ holds the strategy and
# logs/). After all runs end, this script adds harness.log (the run's stdout+stderr), a copy of
# task.json and run_meta.json (exit code, wall time, deadline, commit) to each run directory,
# and prints the
# run directories on stdout, one per line, for scripts/evaluate.py. Progress goes to stderr.
#
# Portable: Linux and macOS run natively (macOS ships bash 3.2: nothing here needs bash 4; it
# needs GNU timeout, i.e. `brew install coreutils` gives gtimeout). From Windows Git Bash
# (MINGW/MSYS/CYGWIN) it re-executes itself in WSL.
# Env overrides: HARNESS_BIN, PY, WSL_DISTRO, HARNESS_ABLATE.
# Defaults: /root/venv-harness/bin/{harness,python} if they exist (Elias' WSL), else the
# repository's .venv/bin/{harness,python} (uv sync).
set -euo pipefail

case $(uname -s) in
    MINGW* | MSYS* | CYGWIN*)  # Git Bash on Windows: hop into WSL, same repository
        distro=${WSL_DISTRO:-Ubuntu-24.04}
        win_root=$(cd "$(dirname "$0")/.." && pwd -W)  # C:/Users/...
        drive=$(printf '%s' "${win_root:0:1}" | tr '[:upper:]' '[:lower:]')
        root="/mnt/$drive${win_root:2}"
        task_arg=${1:?usage: scripts/run_harness.sh TASK_DIR [N]}
        task_win=$(cd "$task_arg" && pwd -W)  # absolute, C:/...
        task_arg="/mnt/$(printf '%s' "${task_win:0:1}" | tr '[:upper:]' '[:lower:]')${task_win:2}"
        shift
        printf -v rest ' %q' "$@"
        envs=""  # environment variables do not cross into WSL by themselves
        for v in HARNESS_BIN PY HARNESS_ABLATE; do
            if [[ -n ${!v:-} ]]; then envs+=" $v=$(printf '%q' "${!v}")"; fi
        done
        exec wsl -d "$distro" -e bash -lc \
            "cd '$root' &&$envs bash scripts/run_harness.sh '$task_arg'$rest"
        ;;
esac

root=$(cd "$(dirname "$0")/.." && pwd -P)
if [[ -z ${HARNESS_BIN:-} ]]; then
    if [[ -x /root/venv-harness/bin/harness ]]; then
        HARNESS_BIN=/root/venv-harness/bin/harness
    else
        HARNESS_BIN=$root/.venv/bin/harness
    fi
fi
if [[ -z ${PY:-} ]]; then
    if [[ -x /root/venv-harness/bin/python ]]; then
        PY=/root/venv-harness/bin/python
    else
        PY=$root/.venv/bin/python
    fi
fi

task=${1:?usage: scripts/run_harness.sh TASK_DIR [N]}
n=${2:-1}
[[ $n =~ ^[1-9][0-9]*$ ]] || { echo "N must be a positive integer, got $n" >&2; exit 2; }
[[ -f $task/spec.md && -f $task/task.json ]] || { echo "$task: no spec.md/task.json" >&2; exit 2; }
task=$(cd "$task" && pwd -P)  # absolute (realpath is missing on macOS before 13)
cd "$root"  # the harness writes runs/ relative to the working directory
[[ -x $HARNESS_BIN ]] || {
    echo "$HARNESS_BIN: not found (run \`uv sync\` in the repository, or set HARNESS_BIN)" >&2
    exit 2
}
[[ -x $PY ]] || command -v "$PY" >/dev/null 2>&1 || {
    echo "$PY: not found (run \`uv sync\` in the repository, or set PY)" >&2
    exit 2
}
[[ -f .env ]] || { echo ".env missing: put OPENAI_API_KEY=... in it" >&2; exit 2; }

# GNU timeout: `timeout` on Linux, `gtimeout` on macOS with Homebrew coreutils.
if command -v timeout >/dev/null 2>&1; then
    TIMEOUT=timeout
elif command -v gtimeout >/dev/null 2>&1; then
    TIMEOUT=gtimeout
else
    echo "GNU timeout not found: on macOS run \`brew install coreutils\` (provides gtimeout)," \
        "on Linux install coreutils" >&2
    exit 2
fi

# The key, for the harness only; tolerate CRLF (.env edited on Windows). Never printed.
# (eval of the text, not `. <(...)`: sourcing a process substitution fails in bash 3.2.)
set -a
# shellcheck disable=SC1090
eval "$(tr -d '\r' < .env)"
set +a

now() {  # seconds since the epoch, fractional (BSD date has no %N)
    "$PY" -c 'import time; print(time.time())'
}

deadline=$("$PY" -c 'import json, sys; v = json.load(open(sys.argv[1])).get("deadline_s")
print("" if v is None else int(-(-float(v) // 1)))' "$task/task.json")
commit=$(git -c safe.directory='*' rev-parse --short HEAD 2>/dev/null || echo unknown)
# (no git, or not a repository: 0; without the braces pipefail + set -e end the script here)
dirty=$({ git -c safe.directory='*' status --porcelain --untracked-files=no 2>/dev/null || true; } |
    wc -l | tr -d ' ')  # BSD wc pads the number with spaces
runs_root=runs/$(basename "$task")
mkdir -p "$runs_root"
tmp=$(mktemp -d "${TMPDIR:-/tmp}/run_harness.XXXXXX")
on_signal() {  # Ctrl-C / TERM: pass SIGTERM to every run (timeout forwards it to the harness)
    for f in "$tmp"/*.pid; do [[ -f $f ]] && kill -TERM "$(cat "$f")" 2>/dev/null; done
    wait
    exit 130
}
trap on_signal INT TERM

list_runs() {  # run directories of this task, one per line (not the latest link)
    local d name
    for d in "$runs_root"/*/; do
        d=${d%/}
        name=${d##*/}
        if [[ -d $d && ! -L $d && $name != latest ]]; then echo "$name"; fi
    done | LC_ALL=C sort
}

launch() {  # launch I: one harness run in the background, its meta into $tmp/I.*
    local i=$1
    (
        start=$(now)
        set +e
        # timeout runs the harness in a process group of its own and signals the whole group;
        # duration 0 (no deadline in task.json) disables the timeout.
        "$TIMEOUT" --signal=TERM --kill-after=30 "${deadline:-0}" "$HARNESS_BIN" "$task" \
            >"$tmp/$i.log" 2>&1 </dev/null &
        echo $! >"$tmp/$i.pid"
        wait $!
        code=$?
        end=$(now)
        echo "$code $start $end" >"$tmp/$i.exit"
    ) &
}

echo "task $task: $n run(s), deadline ${deadline:-none}${deadline:+ s}, commit $commit" >&2
declare -a dirs
for ((i = 0; i < n; i++)); do
    before=$(list_runs)
    launch "$i"
    # The harness names its run directory by the second it started: find it, and launch the
    # next run in a later second so that two runs never share (or race for) a directory.
    dirs[i]=""
    for _ in $(seq 300); do  # up to 60 s: imports from /mnt/c can be slow
        new=$(LC_ALL=C comm -13 <(echo "$before") <(list_runs) | head -n 1)
        if [[ -n $new ]]; then
            dirs[i]=$runs_root/$new
            break
        fi
        [[ -f $tmp/$i.exit ]] && break  # exited before creating a directory
        sleep 0.2
    done
    echo "run $i: ${dirs[i]:-(no run directory yet)}  log: $tmp/$i.log" >&2
    if ((i + 1 < n)); then sleep 1.1; fi
done
wait

for ((i = 0; i < n; i++)); do
    read -r code start end <"$tmp/$i.exit" || { code=-1; start=0; end=0; }
    # The directory the harness says it writes to ("running ... into DIR"); the listing diff
    # can pick another run's directory if a start took longer than the wait above.
    dir=$(grep -oE '[^ ]*runs/[^ ]+/[0-9]{8}-[0-9]{6}' "$tmp/$i.log" | head -n 1 || true)
    if [[ -n $dir && -d $dir ]]; then
        dir=$("$PY" -c 'import os, sys
print(os.path.relpath(os.path.realpath(sys.argv[1]), os.path.realpath(".")).replace(os.sep, "/"))' "$dir")
    else
        dir=${dirs[i]}
    fi
    if [[ -z $dir ]]; then
        echo "run $i: exit $code, NO run directory; log:" >&2
        tail -n 20 "$tmp/$i.log" >&2
        continue
    fi
    mv "$tmp/$i.log" "$dir/harness.log"
    cp "$task/task.json" "$dir/task.json"  # the exact budget/deadline, for evaluate.py
    "$PY" - "$dir/run_meta.json" "$code" "$start" "$end" "${deadline:-}" "$commit" "$dirty" \
        "$task" <<'EOF'
import json, sys
path, code, start, end, deadline, commit, dirty, task = sys.argv[1:]
code = int(code)
meta = {
    "exit_code": code,
    "sigterm": code == 124,  # timeout: SIGTERM at the deadline, harness exited in the grace
    "sigkill": code == 137,  # still running 30 s after SIGTERM
    "start": float(start),
    "wall_s": round(float(end) - float(start), 2),
    "deadline_s": float(deadline) if deadline else None,
    "commit": commit,
    "dirty_files": int(dirty),
    "task_dir": task,
}
open(path, "w").write(json.dumps(meta, indent=2) + "\n")
EOF
    strategy=missing
    [[ -f $dir/output/strategy.py ]] && strategy=ok
    echo "run $i: exit $code, wall $("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["wall_s"])' "$dir/run_meta.json") s, strategy $strategy" >&2
    echo "$dir"
done
rm -rf "$tmp"
