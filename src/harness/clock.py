"""The run's clock: how much of the deadline is left, measured from container start.

The deadline counts wall clock from container start (SIGTERM at `deadline_s`), not from when
Python got around to importing the harness. So the clock starts at the moment the container's
init process (PID 1) started, read from /proc; locally (`harness TASK_DIR`) it starts with our
own process. Everything uses `time.monotonic()`, never the wall clock.
"""

import os
import signal
import sys
import threading
import time
from collections.abc import Callable

IMPORT_MONO: float = time.monotonic()  # when this module was imported

MAX_AGE_S = 120.0  # process ages beyond this are clamped: a sane bound for container start-up


def process_age_s(pid: str = "self") -> float:
    """How long ago process `pid` started, at the moment this module was imported.

    Linux only: uptime (/proc/uptime) minus the process start time (/proc/<pid>/stat, field
    22, in clock ticks since boot). In the container PID 1 is its init, so `'1'` measures from
    container start; locally use `'self'` (PID 1 is the OS init there). 0.0 on any error or
    on other platforms. Clamped to [0, MAX_AGE_S].
    """
    if not sys.platform.startswith("linux"):
        return 0.0
    try:
        with open("/proc/uptime", encoding="ascii") as f:
            uptime = float(f.read().split()[0])
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            stat = f.read()
        # comm (field 2) is in parentheses and may contain spaces or ')': split after the last.
        fields = stat[stat.rindex(")") + 2 :].split()
        start_ticks = int(fields[19])  # field 22 overall; fields[0] here is field 3 (state)
        age_now = uptime - start_ticks / os.sysconf("SC_CLK_TCK")
        age = age_now - (time.monotonic() - IMPORT_MONO)
    except Exception:
        return 0.0
    if age != age:  # NaN
        return 0.0
    return min(max(age, 0.0), MAX_AGE_S)


class Clock:
    """Time since container start and time left until the deadline (minus a safety margin)."""

    def __init__(
        self,
        deadline_s: float | None,
        *,
        margin_s: float = 12.0,
        default_deadline_s: float = 600.0,
        container: bool = False,
    ) -> None:
        self.deadline_s: float = _positive(deadline_s, default_deadline_s)
        self.margin_s = float(margin_s)
        self.container = container
        self.start: float = IMPORT_MONO - process_age_s("1" if container else "self")

    def elapsed(self) -> float:
        """Seconds since container start (best estimate)."""
        return time.monotonic() - self.start

    def remaining(self) -> float:
        """Seconds left before the deadline minus the margin; may be negative."""
        return self.deadline_s - self.margin_s - self.elapsed()

    def hard_remaining(self) -> float:
        """Seconds left before the deadline itself (SIGTERM), without the margin."""
        return self.deadline_s - self.elapsed()

    def frac(self) -> float:
        """The fraction of the deadline used so far."""
        return self.elapsed() / self.deadline_s

    def __repr__(self) -> str:
        return (
            f"Clock(deadline_s={self.deadline_s}, margin_s={self.margin_s}, "
            f"elapsed={self.elapsed():.2f})"
        )


def _positive(value, default: float) -> float:
    """`value` as a positive finite float, else `default`."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float(default)
    if v != v or v <= 0 or v == float("inf"):
        return float(default)
    return v


def install_sigterm_handler(on_term: Callable[[], None], *, timeout_s: float = 15.0) -> None:
    """On SIGTERM: call `on_term()` (best effort, exceptions swallowed), then `os._exit(0)`.

    `on_term` runs in a helper thread and gets at most `timeout_s` (SIGKILL follows 30 s after
    SIGTERM): the signal interrupts the main thread anywhere, possibly while it holds a lock
    `on_term` needs, so waiting for it without a bound could hang. A second SIGTERM while
    `on_term` runs is ignored. Must be called from the main thread; a no-op where impossible.
    """
    state = {"busy": False}

    def run() -> None:
        try:
            on_term()
        except BaseException:
            pass

    def handler(signum, frame) -> None:
        if state["busy"]:
            return
        state["busy"] = True
        try:
            t = threading.Thread(target=run, name="sigterm", daemon=True)
            t.start()
            t.join(timeout_s)
        except BaseException:
            pass
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except BaseException:
                pass
        os._exit(0)

    try:
        if threading.current_thread() is not threading.main_thread():
            return
        signal.signal(signal.SIGTERM, handler)
    except Exception:
        pass
