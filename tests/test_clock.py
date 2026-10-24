"""Tests for harness.clock: monotonic time, process age from /proc, the SIGTERM handler."""

import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness import clock
from harness.clock import IMPORT_MONO, Clock, install_sigterm_handler, process_age_s

LINUX = sys.platform.startswith("linux")
SRC = str(Path(__file__).resolve().parents[1] / "src")


def test_import_mono_is_in_the_past():
    assert isinstance(IMPORT_MONO, float)
    assert IMPORT_MONO <= time.monotonic()


def test_elapsed_and_remaining_are_monotonic():
    # a long deadline: the clock counts from import, and the test session may be long by now
    c = Clock(100_000.0, margin_s=10.0)
    e1, r1, h1 = c.elapsed(), c.remaining(), c.hard_remaining()
    time.sleep(0.02)
    e2, r2, h2 = c.elapsed(), c.remaining(), c.hard_remaining()
    assert 0.0 <= e1 <= e2
    assert r2 <= r1 and h2 <= h1
    assert e2 - e1 >= 0.015
    assert 10.0 <= c.hard_remaining() - c.remaining() < 10.01
    assert c.remaining() <= 99_990.0
    assert 0.0 <= c.frac() < 1.0
    assert abs(c.frac() - c.elapsed() / 100_000.0) < 1e-3


def test_remaining_can_go_negative():
    c = Clock(1.0, margin_s=5.0)
    assert c.remaining() < 0
    assert c.hard_remaining() <= 1.0


@pytest.mark.parametrize("bad", [None, 0, -5, "x", float("nan"), float("inf")])
def test_default_deadline(bad):
    c = Clock(bad, default_deadline_s=321.0)
    assert c.deadline_s == 321.0


def test_deadline_from_task_value():
    assert Clock(180).deadline_s == 180.0
    assert isinstance(Clock(180).deadline_s, float)


def test_process_age_self():
    age = process_age_s("self")
    assert isinstance(age, float)
    if LINUX:
        assert 0.0 <= age <= 120.0
    else:
        assert age == 0.0


def test_process_age_pid1_and_errors():
    age1 = process_age_s("1")
    assert isinstance(age1, float) and 0.0 <= age1 <= 120.0
    assert process_age_s("does-not-exist") == 0.0
    assert process_age_s("999999999") == 0.0


def test_clock_start_accounts_for_process_age():
    c = Clock(100.0)
    assert c.start <= IMPORT_MONO
    now = time.monotonic()
    assert c.elapsed() >= now - IMPORT_MONO
    cc = Clock(100.0, container=True)
    assert cc.start <= IMPORT_MONO
    if not LINUX:
        assert cc.start == IMPORT_MONO


@pytest.mark.skipif(not LINUX, reason="/proc is Linux only")
def test_process_age_of_a_fresh_child_is_small():
    # A child that sleeps a bit before importing clock: its own age must reflect the sleep.
    code = (
        "import time; time.sleep(0.5)\n"
        "from harness.clock import process_age_s\n"
        "print(process_age_s('self'))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PYTHONPATH": SRC},
    )
    assert out.returncode == 0, out.stderr
    age = float(out.stdout.strip())
    assert 0.4 <= age <= 10.0


def test_process_age_parses_comm_with_spaces_and_parens(monkeypatch, tmp_path):
    if not LINUX:
        pytest.skip("Linux only")
    uptime = tmp_path / "uptime"
    uptime.write_text("1000.50 2000.00\n")
    stat = tmp_path / "stat"
    # comm "(a b) c)" contains spaces and a ')'; starttime (field 22) = 99000 ticks
    fields = ["S"] + ["0"] * 18 + ["99000"] + ["0"] * 10
    stat.write_text("42 ((a b) c)) " + " ".join(fields) + "\n")
    real_open = open

    def fake_open(path, *a, **k):
        if path == "/proc/uptime":
            return real_open(uptime, *a, **k)
        if path == "/proc/42/stat":
            return real_open(stat, *a, **k)
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr(clock.os, "sysconf", lambda name: 100)
    monkeypatch.setattr(clock.time, "monotonic", lambda: IMPORT_MONO)
    # uptime 1000.5 - 990.0 = 10.5 s
    assert abs(process_age_s("42") - 10.5) < 1e-6


def test_install_sigterm_handler_does_not_crash_in_process():
    import signal

    old = signal.getsignal(signal.SIGTERM)
    try:
        install_sigterm_handler(lambda: None)
    finally:
        signal.signal(signal.SIGTERM, old)


def test_install_sigterm_handler_from_a_thread_is_a_noop():
    errors = []

    def run():
        try:
            install_sigterm_handler(lambda: None)
        except BaseException as e:
            errors.append(e)

    t = threading.Thread(target=run)
    t.start()
    t.join()
    assert errors == []


@pytest.mark.skipif(not LINUX, reason="SIGTERM handlers only run on POSIX")
@pytest.mark.parametrize("raise_in_handler", [False, True])
def test_sigterm_runs_on_term_then_exits_zero(tmp_path, raise_in_handler):
    marker = tmp_path / "marker"
    code = textwrap.dedent(
        f"""
        import os, signal, time
        from harness.clock import install_sigterm_handler

        def on_term():
            open({str(marker)!r}, "w").write("done")
            if {raise_in_handler!r}:
                raise RuntimeError("boom")

        install_sigterm_handler(on_term)
        print("ready", flush=True)
        time.sleep(30)
        os._exit(7)
        """
    )
    p = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PYTHONPATH": SRC},
    )
    assert p.stdout.readline().strip() == "ready"
    p.terminate()  # SIGTERM
    rc = p.wait(timeout=10)
    assert rc == 0, p.stderr.read()
    assert marker.read_text() == "done"


@pytest.mark.skipif(not LINUX, reason="SIGTERM handlers only run on POSIX")
def test_sigterm_exits_even_if_on_term_hangs(tmp_path):
    # on_term needs a lock the interrupted main thread holds: the handler must not hang.
    code = textwrap.dedent(
        """
        import threading, time
        from harness.clock import install_sigterm_handler

        lock = threading.Lock()

        def on_term():
            with lock:
                pass

        install_sigterm_handler(on_term, timeout_s=1.0)
        with lock:
            print("ready", flush=True)
            time.sleep(30)
        """
    )
    p = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PYTHONPATH": SRC},
    )
    assert p.stdout.readline().strip() == "ready"
    t0 = time.monotonic()
    p.terminate()
    assert p.wait(timeout=10) == 0
    assert time.monotonic() - t0 < 5


@pytest.mark.skipif(not LINUX, reason="SIGTERM handlers only run on POSIX")
@pytest.mark.parametrize("block", ["future", "subprocess", "join", "busy"])
def test_sigterm_while_main_thread_waits_like_the_pipeline(tmp_path, block):
    # The pipeline's main thread spends its time in future.result(), subprocess waits and
    # joins while worker threads (LLM calls) run: SIGTERM must still exit 0 promptly.
    marker = tmp_path / "marker"
    code = textwrap.dedent(
        f"""
        import subprocess, threading, time
        from concurrent.futures import ThreadPoolExecutor
        from harness.clock import install_sigterm_handler

        def on_term():
            open({str(marker)!r}, "w").write("done")

        def spin():
            end = time.monotonic() + 30
            while time.monotonic() < end:
                sum(range(1000))

        install_sigterm_handler(on_term)
        pool = ThreadPoolExecutor(max_workers=4)
        futs = [pool.submit(spin) for _ in range(4)]
        print("ready", flush=True)
        if {block!r} == "future":
            futs[0].result()
        elif {block!r} == "subprocess":
            subprocess.run(["sleep", "30"])
        elif {block!r} == "join":
            t = threading.Thread(target=spin)
            t.start()
            t.join()
        else:
            spin()
        """
    )
    p = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PYTHONPATH": SRC},
    )
    assert p.stdout.readline().strip() == "ready"
    time.sleep(0.2)
    t0 = time.monotonic()
    p.terminate()
    rc = p.wait(timeout=20)
    assert rc == 0, p.stderr.read()
    assert time.monotonic() - t0 < 5
    assert marker.read_text() == "done"


def test_watchdog_exits_lead_s_before_the_deadline() -> None:
    fired = threading.Event()
    codes = []

    def fake_exit(code):
        codes.append(code)
        fired.set()

    deadline_in = 4.5  # hard_remaining(): the deadline is 4.5 s away, the lead 4.2 s
    clock.start_watchdog(SimpleNamespace(hard_remaining=lambda: deadline_in), lead_s=4.2,
                         exit=fake_exit)  # fmt: skip
    assert not fired.wait(0.15)  # not before deadline - lead (0.3 s from now)
    assert fired.wait(2.0)
    assert codes == [0]


def test_watchdog_fires_at_once_when_the_deadline_is_past() -> None:
    fired = threading.Event()
    clock.start_watchdog(SimpleNamespace(hard_remaining=lambda: -1.0), exit=lambda c: fired.set())
    assert fired.wait(1.0)
