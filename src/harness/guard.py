"""The never-forfeit guard: wraps LLM-written strategy code into one safe, stand-alone file.

A strategy that raises, times out or crashes forfeits the match, so the file the harness
delivers is never the LLM's code itself. `assemble` embeds the LLM's source (the "core") and an
optional fallback strategy as string literals into a generated file whose class:

- loads each embedded source into a private module at import (under a timer), so a core that
  fails to import just leaves the guard without it;
- runs `core.act(obs)` under a hard SIGALRM timer and checks that the result is a plain Python
  literal; on a timeout, exception or bad result it asks the fallback, and returns `None` (a
  no-op in every game) if that fails too;
- never raises, from import to the last `act`.

The timer raises `_GuardTimeout`, a BaseException, so `except Exception` in LLM code cannot
swallow it; `sanitize` also rewrites the core's bare `except:` and `except BaseException`
clauses to `except Exception` before embedding. The generated file is Python 3.13, standard
library only, and loads under arena's worker (exec of its source in an unregistered module).
"""

import ast
import keyword
import os
import tempfile
from pathlib import Path

MAX_REPLY_CHARS = 900_000  # arena accepts replies up to 1 MB; keep a margin

# Start-up limits inside the generated file. arena gives 10 s for interpreter start, import
# and __init__ together, so these sum to 8.5 s at worst.
IMPORT_LIMIT_S = 4.5  # all embedded sources together
FALLBACK_IMPORT_LIMIT_S = 1.5  # the fallback is loaded first, within IMPORT_LIMIT_S
CORE_INIT_LIMIT_S = 2.5
FALLBACK_INIT_LIMIT_S = 1.5


class _ExceptRewriter(ast.NodeTransformer):
    """`except:` -> `except Exception:` and every `BaseException` name -> `Exception`."""

    def __init__(self) -> None:
        self.changed = False

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> ast.ExceptHandler:
        self.generic_visit(node)
        if node.type is None:
            node.type = ast.Name(id="Exception", ctx=ast.Load())
            self.changed = True
        return node

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if node.id == "BaseException" and isinstance(node.ctx, ast.Load):
            self.changed = True
            return ast.copy_location(ast.Name(id="Exception", ctx=ast.Load()), node)
        return node


def sanitize(src: str) -> str:
    """`src` with handlers that could swallow the guard's timeout rewritten to `Exception`.

    Unchanged if nothing needs rewriting or if the source does not parse (it then fails at
    load time inside the guard, which is handled there).
    """
    try:
        tree = ast.parse(src)
        rewriter = _ExceptRewriter()
        tree = rewriter.visit(tree)
        if not rewriter.changed:
            return src
        ast.fix_missing_locations(tree)
        out = ast.unparse(tree) + "\n"
        compile(out, "<sanitized>", "exec")
        return out
    except Exception:
        return src


def assemble(
    core_src: str | None,
    *,
    strategy_class: str = "Strategy",
    core_class: str = "Strategy",
    fallback_src: str | None = None,
    fallback_class: str = "Strategy",
    act_limit_s: float = 0.80,
    core_share: float = 0.75,
    time_budget_s: float = 0.25,
    header: str = "",
) -> str:
    """Return the source of a self-contained strategy file (stdlib only) wrapping the core.

    `assemble(None)` (no core, no fallback) is the t=0 fallback: its `act` always returns None.
    """
    if not strategy_class.isidentifier() or keyword.iskeyword(strategy_class):
        strategy_class = "Strategy"
    lines = [line if line.startswith("#") else f"# {line}" for line in header.splitlines()]
    head = "\n".join(lines) + "\n" if lines else ""
    config = "\n".join(
        [
            f"_CORE_SRC = {(sanitize(core_src) if core_src is not None else None)!r}",
            f"_CORE_CLASS = {core_class!r}",
            f"_FALLBACK_SRC = {(sanitize(fallback_src) if fallback_src is not None else None)!r}",
            f"_FALLBACK_CLASS = {fallback_class!r}",
            f"_ACT_LIMIT_S = {float(act_limit_s)!r}",
            f"_CORE_SHARE = {float(core_share)!r}",
            f"_TIME_BUDGET = {float(time_budget_s)!r}",
            f"_IMPORT_LIMIT_S = {IMPORT_LIMIT_S!r}",
            f"_FALLBACK_IMPORT_LIMIT_S = {FALLBACK_IMPORT_LIMIT_S!r}",
            f"_CORE_INIT_LIMIT_S = {CORE_INIT_LIMIT_S!r}",
            f"_FALLBACK_INIT_LIMIT_S = {FALLBACK_INIT_LIMIT_S!r}",
            f"_MAX_REPLY_CHARS = {MAX_REPLY_CHARS!r}",
        ]
    )
    cls = (
        f"\n\nclass {strategy_class}(_HzGuard):\n"
        '    """Guarded strategy: never raises, never exceeds its time limit."""\n'
    )
    return f"{head}{_PREAMBLE}\n{config}\n{_RUNTIME}{cls}"


def write_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` atomically: temp file in the same directory, fsync, os.replace."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o644)  # mkstemp creates 0600; the file is read by other users
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_PREAMBLE = '''"""Generated by the harness: a strategy core behind a never-forfeit guard."""

import ast as _ast
import linecache as _linecache
import signal as _signal
import sys as _sys
import threading as _threading
import time as _time
import traceback as _traceback
import types as _types
'''

# The runtime of the generated file. Kept as plain source so the generated file needs nothing
# but the standard library. Frames of the functions in _GUARD_CODES are exempt from the timer:
# an alarm that lands there never raises, so the guard's own bookkeeping cannot be interrupted.
_RUNTIME = r'''
_MIN_FALLBACK_S = 0.05
_REPEAT_S = 0.05  # after the first alarm, re-fire every 50 ms until the call returns
_STATE = {"armed": False}
_GUARD_CODES = set()


class _GuardTimeout(BaseException):
    """Raised by the guard's timer. A BaseException: `except Exception` cannot swallow it."""


class _Sink:
    """Swallows output: strategy code must not flood the worker's stderr."""

    encoding = "utf-8"
    errors = "replace"

    def write(self, s):
        return len(s)

    def writelines(self, lines):
        return None

    def flush(self):
        return None

    def isatty(self):
        return False


_SINK = _Sink()


def _noop_print(*args, **kwargs):
    return None


def _on_alarm(signum, frame):
    if _STATE["armed"] and (frame is None or frame.f_code not in _GUARD_CODES):
        raise _GuardTimeout()


def _can_time():
    return (
        hasattr(_signal, "setitimer")
        and hasattr(_signal, "SIGALRM")
        and _threading.current_thread() is _threading.main_thread()
    )


def _plain(value):
    """(True, plain copy) if value is a literal within the reply limit, else (False, None)."""
    try:
        text = repr(value)
        if len(text) > _MAX_REPLY_CHARS:
            return False, None
        if not text.isascii() and len(text.encode("utf-8", "replace")) > _MAX_REPLY_CHARS:
            return False, None  # arena's reply limit counts bytes
        copy = _ast.literal_eval(text)
        if not bool(copy == value):  # NaN, or a type whose repr is not its value
            return False, None
        return True, copy
    except Exception:
        return False, None


def _call(fn, args, limit, check):
    """fn(*args) under a hard timer: (status, value, error, secs). Never raises.

    status: 'ok', 'timeout', 'exception' or 'bad' (check=True and not a plain literal).
    """
    status = "exception"
    value = None
    error = None
    timed = False
    prev_handler = None
    prev_timer = (0.0, 0.0)
    out, err, inp = _sys.stdout, _sys.stderr, _sys.stdin
    t0 = _time.perf_counter()
    try:
        if limit > 0 and _can_time():
            prev_handler = _signal.signal(_signal.SIGALRM, _on_alarm)
            prev_timer = _signal.setitimer(_signal.ITIMER_REAL, limit, _REPEAT_S)
            timed = True
        _sys.stdout = _sys.stderr = _SINK
        _sys.stdin = None  # arena's protocol pipe: untrusted code must not read it
        _STATE["armed"] = timed
        try:
            result = fn(*args)
            if check:
                ok, result = _plain(result)
                status = "ok" if ok else "bad"
            else:
                status = "ok"
            value = result
        except _GuardTimeout as e:
            status = "timeout"
            error = e
        except BaseException as e:
            status = "exception"
            error = e
    except BaseException as e:
        if status == "exception" and error is None:
            error = e
    finally:
        _STATE["armed"] = False
        if timed:
            try:
                _signal.setitimer(_signal.ITIMER_REAL, 0.0)
                _signal.signal(
                    _signal.SIGALRM, prev_handler if prev_handler is not None else _signal.SIG_DFL
                )
                if prev_timer[0] > 0:
                    spent = _time.perf_counter() - t0
                    _signal.setitimer(
                        _signal.ITIMER_REAL, max(prev_timer[0] - spent, 1e-4), prev_timer[1]
                    )
            except BaseException:
                pass
        _sys.stdout, _sys.stderr, _sys.stdin = out, err, inp
    if isinstance(error, _GuardTimeout):
        status = "timeout"
    return status, value, error, _time.perf_counter() - t0


_GUARD_CODES.add(_call.__code__)  # exempt already while the embedded sources load


def _act_of(strategy, obs):
    """strategy.act(obs): inside the timed call, so a missing or broken `act` is caught too."""
    return strategy.act(obs)


def _describe(status, error, secs, limit):
    if status == "timeout":
        where = ""
        try:
            if error is not None:
                frames = _traceback.extract_tb(error.__traceback__)
                frames = [f for f in frames if f.name not in ("_on_alarm", "_call")][-3:]
                where = "\ninterrupted at:\n" + "".join(_traceback.format_list(frames))
        except BaseException:
            where = ""
        return "timeout: no result within %.2f s (limit %.2f s)%s" % (secs, limit, where[-1500:])
    if status == "bad":
        return "bad output: not a plain Python literal within the reply limit"
    try:
        return "".join(_traceback.format_exception(error))[-4000:]
    except BaseException:
        return "exception (traceback unavailable)"


def _load(src, cls_name, label):
    n = 0
    while "_hz_%s_%d" % (label, n) in _sys.modules:
        n += 1
    name = "_hz_%s_%d" % (label, n)
    filename = "<%s>" % name
    _linecache.cache[filename] = (len(src), None, src.splitlines(True), filename)
    module = _types.ModuleType(name)
    module.__file__ = filename
    module.TIME_BUDGET = _TIME_BUDGET
    module.print = _noop_print
    _sys.modules[name] = module
    try:
        exec(compile(src, filename, "exec"), module.__dict__)
        module.TIME_BUDGET = _TIME_BUDGET
        module.print = _noop_print
        return getattr(module, cls_name)
    except BaseException:
        _sys.modules.pop(name, None)
        raise


_LOAD_ERRORS = {}


def _load_all():
    t0 = _time.perf_counter()
    loaded = {"core": None, "fallback": None}
    parts = (
        ("fallback", _FALLBACK_SRC, _FALLBACK_CLASS, _FALLBACK_IMPORT_LIMIT_S),
        ("core", _CORE_SRC, _CORE_CLASS, None),
    )
    for label, src, cls_name, limit in parts:
        if src is None:
            continue
        left = _IMPORT_LIMIT_S - (_time.perf_counter() - t0)
        limit = min(limit, left) if limit is not None else left
        limit = max(limit, 0.5)
        status, value, error, secs = _call(_load, (src, cls_name, label), limit, False)
        if status == "ok":
            loaded[label] = value
        else:
            _LOAD_ERRORS[label] = _describe(status, error, secs, limit)
    return loaded["core"], loaded["fallback"]


try:
    _CORE_CLS, _FALLBACK_CLS = _load_all()
except BaseException:
    _CORE_CLS, _FALLBACK_CLS = None, None


class _HzGuard:
    def __init__(self, player_id):
        self.player_id = player_id
        self._core = None
        self._fallback = None
        self._stats = {
            "turns": 0,
            "core_ok": 0,
            "core_exceptions": 0,
            "core_timeouts": 0,
            "core_bad_output": 0,
            "fallback_used": 0,
            "fallback_failures": 0,
            "none_used": 0,
            "max_core_s": 0.0,
            "first_core_traceback": _LOAD_ERRORS.get("core"),
            "core_loaded": _CORE_CLS is not None,
            "fallback_loaded": _FALLBACK_CLS is not None,
            "core_init_error": None,
            "fallback_init_error": None,
            "fallback_load_error": _LOAD_ERRORS.get("fallback"),
            "first_fallback_error": None,
        }
        try:
            if _CORE_CLS is not None:
                lim = _CORE_INIT_LIMIT_S
                status, value, error, secs = _call(_CORE_CLS, (player_id,), lim, False)
                if status == "ok":
                    self._core = value
                else:
                    msg = _describe(status, error, secs, lim)
                    self._stats["core_init_error"] = msg
                    if self._stats["first_core_traceback"] is None:
                        self._stats["first_core_traceback"] = msg
            if _FALLBACK_CLS is not None:
                lim = _FALLBACK_INIT_LIMIT_S
                status, value, error, secs = _call(_FALLBACK_CLS, (player_id,), lim, False)
                if status == "ok":
                    self._fallback = value
                else:
                    self._stats["fallback_init_error"] = _describe(status, error, secs, lim)
        except BaseException:
            pass

    def act(self, obs):
        try:
            return self._act(obs)
        except BaseException:
            return None

    def _act(self, obs):
        t0 = _time.perf_counter()
        st = self._stats
        st["turns"] += 1
        core, fallback = self._core, self._fallback
        saved = None
        if core is not None:
            if fallback is not None:
                try:
                    saved = repr(obs)  # the core may mutate obs; the fallback gets a fresh copy
                except Exception:
                    saved = None
            limit = _ACT_LIMIT_S * _CORE_SHARE
            status, value, error, secs = _call(_act_of, (core, obs), limit, True)
            if secs > st["max_core_s"]:
                st["max_core_s"] = secs
            if status == "ok":
                st["core_ok"] += 1
                return value
            if status == "timeout":
                st["core_timeouts"] += 1
            elif status == "bad":
                st["core_bad_output"] += 1
            else:
                st["core_exceptions"] += 1
            if st["first_core_traceback"] is None:
                st["first_core_traceback"] = _describe(status, error, secs, limit)
            error = None
        if fallback is not None:
            fobs = obs
            if saved is not None:
                try:
                    fobs = _ast.literal_eval(saved)
                except Exception:
                    fobs = obs
            left = _ACT_LIMIT_S - (_time.perf_counter() - t0)
            if left >= _MIN_FALLBACK_S:
                status, value, error, secs = _call(_act_of, (fallback, fobs), left, True)
                if status == "ok":
                    st["fallback_used"] += 1
                    return value
                st["fallback_failures"] += 1
                if st["first_fallback_error"] is None:
                    st["first_fallback_error"] = _describe(status, error, secs, left)
        st["none_used"] += 1
        return None

    def guard_stats(self):
        """Counters of this instance: how often the core answered, failed, fell back."""
        try:
            return dict(self._stats)
        except BaseException:
            return {}


for _fn in (_call, _HzGuard.__init__, _HzGuard.act, _HzGuard._act, _HzGuard.guard_stats):
    _GUARD_CODES.add(_fn.__code__)
del _fn
'''
