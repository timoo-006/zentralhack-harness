"""LLM calls: one `LLM.call` per request, within the allowed models, budget and deadline.

Every call goes through the same gate: allowed model, enough time left, worst-case cost
reserved (`Budget.reserve`), at most `max_concurrency` (<= 4) requests in flight, a few
retries on transient errors, real cost settled, one JSON line in the transcript. `call` never
raises: failures come back as an `LLMResult` with `ok=False`.
"""

import ast
import hashlib
import json
import math
import random
import re
import textwrap
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import openai

from harness.budget import Budget, Reservation, estimate_tokens
from harness.clock import Clock

MAX_CONCURRENCY = 4  # the evaluation's cap on concurrent calls

# Transient errors worth retrying: rate limits, connection problems (incl. timeouts), 5xx.
RETRYABLE: tuple[type[BaseException], ...] = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.InternalServerError,
)

# Every LLM instance shares this too, so the cap holds for the whole process.
_PROCESS_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENCY)


@dataclass
class LLMResult:
    ok: bool  # a response came back (possibly incomplete)
    text: str  # response.output_text ('' on failure)
    model: str
    status: str  # 'completed' | 'incomplete' | 'error' | 'skipped_budget' | 'skipped_time'
    usage: dict | None
    cost: float
    secs: float
    error: str | None
    label: str


class LLM:
    """The gate every LLM request goes through (see the module docstring)."""

    MIN_CALL_S = 20.0  # do not start a request with less time than this left
    CLOCK_SLACK_S = 3.0  # a request must end this long before `clock.remaining()` runs out
    MAX_ATTEMPTS = 3  # first try + 2 retries

    def __init__(
        self,
        task: dict,
        budget: Budget,
        clock: Clock,
        log_path: Path,
        client=None,
        max_concurrency: int = MAX_CONCURRENCY,
    ) -> None:
        self.task = task
        self.budget = budget
        self.clock = clock
        self.log_path = Path(log_path)
        self.prices: dict[str, dict] = task.get("prices") or {}
        allowed = task.get("allowed_models")
        names = list(self.prices) if allowed is None else list(allowed)
        key = self._price_key
        self._models = sorted({m for m in names if m in self.prices}, key=lambda m: (key(m), m))
        self._client_error: str | None = None
        if client is None:
            try:
                client = openai.OpenAI(max_retries=0)  # reads OPENAI_API_KEY / OPENAI_BASE_URL
            except Exception as e:
                client, self._client_error = None, f"no OpenAI client: {_describe(e)}"
        self.client = client
        n = max(1, min(int(max_concurrency), MAX_CONCURRENCY))
        self._slots = threading.BoundedSemaphore(n)
        self._log_lock = threading.Lock()
        self._seen: set[str] = set()  # sha1 of texts already in the transcript
        self.calls = 0
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # ---------------------------------------------------------------- models

    def _price_key(self, model: str) -> float:
        p = self.prices.get(model) or {}
        try:
            return float(p["input"]) + float(p["output"])
        except (KeyError, TypeError, ValueError):
            return math.inf

    def models(self) -> list[str]:
        """The allowed (and priced) models, cheapest first (input + output price, then name)."""
        return list(self._models)

    def cheapest(self) -> str:
        """The cheapest allowed model ('' if there is none)."""
        return self._models[0] if self._models else ""

    def strongest(self) -> str:
        """The most expensive allowed model ('' if there is none)."""
        return self._models[-1] if self._models else ""

    # ---------------------------------------------------------------- calls

    def call(
        self,
        *,
        label: str,
        model: str,
        instructions: str,
        input: str | list,
        effort: str = "medium",
        max_output_tokens: int = 16000,
        timeout_s: float | None = None,
    ) -> LLMResult:
        """One request (with retries on transient errors). Never raises."""
        t0 = time.monotonic()
        rec = {
            "label": label,
            "model": model,
            "effort": effort,
            "max_output_tokens": max_output_tokens,
        }
        try:
            result = self._call(
                rec, t0, label, model, instructions, input, effort, max_output_tokens, timeout_s
            )
        except Exception as e:  # a bug here must not take the run down
            result = LLMResult(
                False, "", model, "error", None, 0.0, time.monotonic() - t0, _describe(e), label
            )
        try:
            self._log(rec, result, instructions, input)
        except Exception:
            pass
        return result

    def _call(
        self, rec, t0, label, model, instructions, input, effort, max_output_tokens, timeout_s
    ) -> LLMResult:
        def done(status, *, ok=False, text="", usage=None, cost=0.0, error=None) -> LLMResult:
            return LLMResult(
                ok, text, model, status, usage, cost, time.monotonic() - t0, error, label
            )

        # 1. allowed models only
        if model not in self._models:
            return done("error", error=f"model {model!r} not allowed (allowed: {self._models})")
        if self.client is None:
            return done("error", error=self._client_error or "no OpenAI client")

        # 2. enough time for a meaningful request
        if self._timeout(timeout_s) < self.MIN_CALL_S:
            return done("skipped_time", error=f"{self.clock.remaining():.1f}s left")

        # 3. reserve the worst case
        n_in = estimate_tokens(instructions + "\n" + _input_text(input))
        rec["est_input_tokens"] = n_in
        r = self.budget.reserve(model, n_in, max_output_tokens)
        if r is None:
            need = self.budget.worst_case(model, n_in, max_output_tokens)
            return done(
                "skipped_budget",
                error=f"worst case ${need:.4f} > ${self.budget.remaining():.4f} left",
            )

        # concurrency slot (wait no longer than the time a request still needs)
        wait = max(0.0, self.clock.remaining() - self.CLOCK_SLACK_S - self.MIN_CALL_S)
        if not self._acquire(wait):
            self.budget.release(r)
            return done("skipped_time", error="no free request slot in time")
        cost = 0.0
        errors: list[str] = []
        # `timeout_s` bounds the whole request incl. retries (from here: the slot wait does
        # not count), so a timed-out attempt is not retried with a fresh `timeout_s`.
        end = time.monotonic() + (timeout_s or math.inf)
        try:
            for attempt in range(1, self.MAX_ATTEMPTS + 1):
                rec["attempts"] = attempt
                timeout = self._timeout(timeout_s, end)
                if timeout < self.MIN_CALL_S:
                    self.budget.release(r)
                    return done("skipped_time" if not errors else "error", cost=cost,
                                error="; ".join(errors) or "out of time")  # fmt: skip
                kwargs = {
                    "model": model,
                    "instructions": instructions,
                    "input": input,
                    "max_output_tokens": max_output_tokens,
                    "store": False,
                    "timeout": timeout,
                }
                if effort:
                    kwargs["reasoning"] = {"effort": effort}
                self.calls += 1
                try:
                    response = self.client.responses.create(**kwargs)
                except Exception as e:
                    errors.append(f"attempt {attempt}: {_describe(e)}")
                    if _billable(e):
                        cost += self.budget.settle(r, None)  # may have been billed: keep it
                    else:
                        self.budget.release(r)  # rejected outright (429, 4xx): not billed
                    if not isinstance(e, RETRYABLE) or attempt == self.MAX_ATTEMPTS:
                        return done("error", cost=cost, error="; ".join(errors))
                    backoff = _backoff(e, attempt)
                    if self._timeout(timeout_s, end) - backoff < self.MIN_CALL_S:
                        return done("error", cost=cost, error="; ".join(errors) + "; no time")
                    time.sleep(backoff)
                    r = self.budget.reserve(model, n_in, max_output_tokens)
                    if r is None:
                        return done("error", cost=cost, error="; ".join(errors) + "; no budget")
                    continue
                try:
                    return self._finish(response, r, cost, errors, done)
                except Exception as e:  # an odd response object: the request was made
                    cost += self.budget.settle(r, None)  # no-op if _finish already settled
                    errors.append(f"attempt {attempt}: bad response: {_describe(e)}")
                    return done("error", cost=cost, error="; ".join(errors))
        finally:
            self._release()
        return done("error", cost=cost, error="; ".join(errors) or "no attempt made")

    def _finish(self, response, r: Reservation, cost: float, errors: list[str], done) -> LLMResult:
        usage = _usage_dict(_get(response, "usage"))
        cost += self.budget.settle(r, usage)  # usage None: the full reservation stays spent
        try:
            text = response.output_text or ""
            if not isinstance(text, str):
                text = str(text)
        except Exception:
            text = ""
        raw = _get(response, "status")
        status = raw if isinstance(raw, str) else "completed"
        if status == "completed":
            return done("completed", ok=True, text=text, usage=usage, cost=cost,
                        error="; ".join(errors) or None)  # fmt: skip
        details = _jsonable(_get(response, "incomplete_details"))
        if status == "incomplete":
            return done("incomplete", ok=True, text=text, usage=usage, cost=cost,
                        error=f"incomplete: {details}")  # fmt: skip
        err = _jsonable(_get(response, "error"))
        return done("error", text=text, usage=usage, cost=cost,
                    error=f"response status {status!r}: {err or details}")  # fmt: skip

    def _timeout(self, timeout_s: float | None, end: float = math.inf) -> float:
        """The request timeout: the caller's (what is left of it by `end`, across retries),
        cut to the time left on the clock."""
        window = self.clock.remaining() - self.CLOCK_SLACK_S
        return min(timeout_s or math.inf, end - time.monotonic(), window)

    def _acquire(self, wait_s: float) -> bool:
        deadline = time.monotonic() + wait_s
        if not self._slots.acquire(timeout=wait_s):
            return False
        if not _PROCESS_SLOTS.acquire(timeout=max(0.0, deadline - time.monotonic())):
            self._slots.release()
            return False
        return True

    def _release(self) -> None:
        _PROCESS_SLOTS.release()
        self._slots.release()

    # ---------------------------------------------------------------- transcript

    def _log(self, rec: dict, result: LLMResult, instructions, input) -> None:
        rec.update(
            t=round(self.clock.elapsed(), 3),
            status=result.status,
            ok=result.ok,
            secs=round(result.secs, 3),
            usage=result.usage,
            cost=result.cost,
            spent_total=self.budget.spent,
            reserved_total=self.budget.reserved,
            error=result.error,
        )
        with self._log_lock:
            rec["instructions"] = self._text_entry(instructions, None)
            if isinstance(input, list):
                rec["input"] = [
                    self._text_entry(_input_text(m), m.get("role") if isinstance(m, dict) else None)
                    for m in input
                ]
            else:
                rec["input"] = [self._text_entry(_input_text(input), "user")]
            rec["output"] = result.text
            line = json.dumps(rec, default=str)
            try:
                with self.log_path.open("a", encoding="utf-8", newline="\n") as f:
                    f.write(line + "\n")
            except OSError:
                pass
        try:
            print(
                f"[llm] {result.label} {result.model} {result.status} {result.secs:.1f}s "
                f"${result.cost:.4f} (spent ${self.budget.spent:.4f})"
                + (f" {result.error[:200]}" if result.error else ""),
                flush=True,
            )
        except Exception:
            pass

    def _text_entry(self, text: str, role: str | None) -> dict:
        """{'sha1', 'chars', 'text'}: the full text only the first time this text is logged."""
        h = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
        entry: dict = {"sha1": h, "chars": len(text)}
        if role:
            entry["role"] = role
        if h not in self._seen:
            self._seen.add(h)
            entry["text"] = text
        return entry


# -------------------------------------------------------------------- helpers


def _input_text(x) -> str:
    """The text of a Responses API input: a string, a message, or a list of either."""
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, (list, tuple)):
        return "\n\n".join(_input_text(i) for i in x)
    if isinstance(x, dict):
        for k in ("content", "text", "output", "arguments"):
            if k in x:
                return _input_text(x[k])
    return json.dumps(x, default=str)


def _get(obj, name: str):
    """`obj.name`, or None if it is missing or its getter raises."""
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _usage_dict(usage) -> dict | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    try:
        return usage.model_dump()
    except Exception:
        pass
    try:
        return dict(vars(usage))
    except Exception:
        return None


def _jsonable(x):
    if x is None:
        return None
    try:
        return x.model_dump()
    except Exception:
        return x if isinstance(x, (str, int, float, bool, dict, list)) else str(x)


def _billable(e: BaseException) -> bool:
    """Might the failed request still have been billed? Not if the server rejected it with a
    4xx (429 included: rate limits reject before any work); otherwise assume it was."""
    status = getattr(e, "status_code", None)
    return not (isinstance(status, int) and 400 <= status < 500)


def _backoff(e: BaseException, attempt: int) -> float:
    """Seconds to wait before retry `attempt + 1`: Retry-After if the server sent one (<= 20)."""
    base = 2.0 if isinstance(e, openai.RateLimitError) else 1.0
    try:
        ra = float(e.response.headers.get("retry-after"))  # type: ignore[attr-defined]
        if 0 <= ra <= 20:
            base = max(base, ra)
    except Exception:
        pass
    return base * attempt + random.uniform(0.0, 0.5)


def _describe(e: BaseException) -> str:
    try:
        msg = str(e)
    except Exception:  # a broken __str__ must not leak the reservation
        msg = "<unprintable>"
    return f"{type(e).__name__}: {msg}"[:2000]


# -------------------------------------------------------------------- code extraction

_PY_LANGS = {"python", "py", "python3", "py3"}
_LANG = re.compile(r"[A-Za-z0-9_+#.-]*")  # the info string's first word


def extract_code(text: str) -> str | None:
    """The Python file in an LLM answer.

    The longest fenced ```python / ```py / bare ``` block, preferring blocks that define a
    class (the file, not a usage snippet: a file with a syntax error still beats a snippet,
    so a repair sees the error), then blocks that parse; an unclosed last fence counts, as in
    a truncated answer, and an indented block (in a list item) is dedented. With no such
    fence: the whole text if it parses as Python and defines a class. None otherwise.
    """
    if not text:
        return None
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [b if _parses(b) else textwrap.dedent(b) for b in _fenced_python(text)]
    if blocks:
        best = max(blocks, key=_rank)
        return best.strip("\n") + "\n"
    stripped = text.strip()
    try:
        tree = ast.parse(stripped)
    except Exception:
        return None
    if any(isinstance(n, ast.ClassDef) for n in ast.walk(tree)):
        return stripped + "\n"
    return None


def _fenced_python(text: str) -> list[str]:
    """Contents of fenced blocks tagged python/py or untagged, in order."""
    blocks: list[str] = []
    fence: str | None = None
    lang = ""
    body: list[str] = []
    for line in text.split("\n"):
        s = line.strip()
        if fence is None:
            if s.startswith("```") or s.startswith("~~~"):
                ch = s[0]
                n = len(s) - len(s.lstrip(ch))
                fence = ch * n
                info = s[n:].strip()
                lang = _LANG.match(info).group(0).lower()  # type: ignore[union-attr]
                body = []
        elif s.startswith(fence) and not s.lstrip(fence[0]).strip():
            if lang in _PY_LANGS or lang == "":
                blocks.append("\n".join(body))
            fence = None
        else:
            body.append(line)
    if fence is not None and (lang in _PY_LANGS or lang == "") and body:
        blocks.append("\n".join(body))  # unclosed: the answer was cut off
    return [b for b in blocks if b.strip()]


def _parses(src: str) -> bool:
    try:
        ast.parse(src)
        return True
    except Exception:
        return False


_CLASS = re.compile(r"^[ \t]*class[ \t]+[A-Za-z_]\w*[ \t]*[:(]", re.MULTILINE)


def _rank(src: str) -> tuple[bool, bool, int]:
    """Sort key of a candidate block: (defines a class, parses, length)."""
    try:
        tree = ast.parse(src)
    except Exception:
        return _CLASS.search(src) is not None, False, len(src)
    return any(isinstance(n, ast.ClassDef) for n in ast.walk(tree)), True, len(src)
