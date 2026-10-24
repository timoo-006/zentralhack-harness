"""Tests for harness.llm with a fake OpenAI client (no network, no API key)."""

import json
import math
import threading
import time
from types import SimpleNamespace

import httpx2
import openai
import pytest

from harness import llm as llm_mod
from harness.budget import Budget, estimate_tokens
from harness.clock import Clock
from harness.llm import LLM, LLMResult, extract_code
from harness.pricing import cost

PRICES = {
    "gpt-6-luna": {"input": 0.1, "cached_input": 0.01, "cache_write": 0.125, "output": 0.5},
    "gpt-6.1-sol": {"input": 2.0, "cached_input": 0.1, "cache_write": 2.5, "output": 10.0},
    "gpt-6-astra": {"input": 10.0, "cached_input": 1.0, "cache_write": 12.5, "output": 50.0},
}
TASK = {"allowed_models": ["gpt-6.1-sol", "gpt-6-luna"], "prices": PRICES}
USAGE = {
    "input_tokens": 1200,
    "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 100},
    "output_tokens": 800,
    "output_tokens_details": {"reasoning_tokens": 300},
    "total_tokens": 2000,
}


def response(text="hello", status="completed", usage=USAGE, **extra):
    u = None if usage is None else SimpleNamespace(model_dump=lambda: dict(usage))
    return SimpleNamespace(output_text=text, status=status, usage=u, **extra)


class FakeClient:
    """`client.responses.create(**kw)`: returns/raises the scripted items in order."""

    def __init__(self, *script, delay=0.0):
        self.script = list(script) or [response()]
        self.calls: list[dict] = []
        self.delay = delay
        self.lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0
        self.responses = SimpleNamespace(create=self.create)

    def create(self, **kw):
        with self.lock:
            self.calls.append(kw)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        try:
            if self.delay:
                time.sleep(self.delay)
            if isinstance(item, BaseException):
                raise item
            return item
        finally:
            with self.lock:
                self.in_flight -= 1


_REQ = httpx2.Request("POST", "https://api.openai.com/v1/responses")


def status_error(cls, code, headers=None):
    return cls(f"status {code}", response=httpx2.Response(code, request=_REQ, headers=headers),
               body=None)  # fmt: skip


def make(tmp_path, client, *, limit=10.0, deadline=1000.0, task=TASK, **kw):
    budget = Budget(limit, PRICES)
    clock = Clock(deadline, margin_s=12.0)
    log = tmp_path / "logs" / "transcript.jsonl"
    return LLM(task, budget, clock, log, client=client, **kw), budget, clock, log


def lines(log):
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(llm_mod, "_backoff", lambda e, attempt: 0.0)


# ------------------------------------------------------------------- models


def test_models_order(tmp_path):
    m, *_ = make(tmp_path, FakeClient())
    assert m.models() == ["gpt-6-luna", "gpt-6.1-sol"]
    assert m.cheapest() == "gpt-6-luna"
    assert m.strongest() == "gpt-6.1-sol"


def test_allowed_none_means_all_priced_models(tmp_path):
    m, *_ = make(tmp_path, FakeClient(), task={"allowed_models": None, "prices": PRICES})
    assert m.models() == ["gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra"]
    assert m.strongest() == "gpt-6-astra"


def test_allowed_models_without_price_are_dropped(tmp_path):
    task = {"allowed_models": ["gpt-6-astra", "mystery"], "prices": PRICES}
    m, *_ = make(tmp_path, FakeClient(), task=task)
    assert m.models() == ["gpt-6-astra"]
    assert m.cheapest() == m.strongest() == "gpt-6-astra"


# ------------------------------------------------------------------- calls


def test_success_and_cost_accounting(tmp_path):
    client = FakeClient(response("```python\nclass S: pass\n```"))
    m, budget, _, log = make(tmp_path, client)
    r = m.call(label="t1", model="gpt-6.1-sol", instructions="sys", input="hi", effort="low",
               max_output_tokens=5000, timeout_s=120)  # fmt: skip
    assert isinstance(r, LLMResult)
    assert r.ok and r.status == "completed" and r.error is None
    assert r.text.startswith("```python")
    assert r.usage == USAGE
    expected = cost(USAGE, PRICES["gpt-6.1-sol"])
    assert math.isclose(r.cost, expected)
    assert math.isclose(budget.spent, expected)
    assert budget.reserved == 0.0
    assert r.label == "t1" and r.model == "gpt-6.1-sol" and r.secs >= 0
    (kw,) = client.calls
    assert kw["model"] == "gpt-6.1-sol"
    assert kw["instructions"] == "sys" and kw["input"] == "hi"
    assert kw["reasoning"] == {"effort": "low"}
    assert kw["max_output_tokens"] == 5000
    assert kw["store"] is False
    assert "tools" not in kw and "previous_response_id" not in kw
    assert kw["timeout"] == 120
    (rec,) = lines(log)
    assert rec["label"] == "t1" and rec["status"] == "completed"
    assert math.isclose(rec["cost"], expected) and math.isclose(rec["spent_total"], expected)
    assert rec["output"] == r.text and rec["usage"] == USAGE


def test_timeout_is_cut_to_the_clock(tmp_path):
    client = FakeClient()
    m, _, clock, _ = make(tmp_path, client, deadline=100.0)
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i")
    assert r.ok
    t = client.calls[0]["timeout"]
    assert t <= clock.remaining() - 3 + 0.5
    assert t > 50


def test_incomplete(tmp_path):
    details = SimpleNamespace(reason="max_output_tokens")
    client = FakeClient(response("partial", status="incomplete", incomplete_details=details))
    m, budget, *_ = make(tmp_path, client)
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i")
    assert r.ok and r.status == "incomplete" and r.text == "partial"
    assert "max_output_tokens" in r.error
    assert math.isclose(budget.spent, cost(USAGE, PRICES["gpt-6-luna"]))


def test_failed_response_status(tmp_path):
    client = FakeClient(response("", status="failed", error={"code": "server_error"}))
    m, budget, *_ = make(tmp_path, client)
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i")
    assert not r.ok and r.status == "error" and "failed" in r.error
    assert budget.reserved == 0.0


def test_usage_none_keeps_reservation_spent(tmp_path):
    client = FakeClient(response("ok", usage=None))
    m, budget, *_ = make(tmp_path, client)
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i", max_output_tokens=1000)
    assert r.ok and r.usage is None
    wc = budget.worst_case("gpt-6-luna", estimate_tokens("s\ni"), 1000)
    assert math.isclose(r.cost, wc) and math.isclose(budget.spent, wc)
    assert budget.reserved == 0.0


def test_budget_refusal_makes_no_request(tmp_path):
    client = FakeClient()
    m, budget, _, log = make(tmp_path, client, limit=0.001)
    r = m.call(label="t", model="gpt-6.1-sol", instructions="s", input="i",
               max_output_tokens=16000)  # fmt: skip
    assert not r.ok and r.status == "skipped_budget" and r.cost == 0.0
    assert client.calls == []
    assert budget.spent == 0.0 and budget.reserved == 0.0
    assert lines(log)[0]["status"] == "skipped_budget"


def test_time_refusal_makes_no_request(tmp_path):
    client = FakeClient()
    m, budget, *_ = make(tmp_path, client, deadline=30.0)  # 30 - 12 margin - 3 < 20
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i")
    assert r.status == "skipped_time" and not r.ok
    assert client.calls == []
    assert budget.reserved == 0.0 and budget.spent == 0.0


def test_short_caller_timeout_is_skipped(tmp_path):
    client = FakeClient()
    m, *_ = make(tmp_path, client)
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i", timeout_s=-5)
    assert r.status == "skipped_time" and client.calls == []


def test_disallowed_model_makes_no_request(tmp_path):
    client = FakeClient()
    m, budget, *_ = make(tmp_path, client)
    r = m.call(label="t", model="gpt-6-astra", instructions="s", input="i")
    assert not r.ok and r.status == "error" and "not allowed" in r.error
    assert client.calls == []
    assert budget.reserved == 0.0


def test_retry_on_rate_limit_then_success(tmp_path):
    client = FakeClient(status_error(openai.RateLimitError, 429), response("fine"))
    m, budget, _, log = make(tmp_path, client)
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i")
    assert r.ok and r.text == "fine" and len(client.calls) == 2
    # a 429 is rejected before any work: only the successful attempt is billed
    assert math.isclose(budget.spent, cost(USAGE, PRICES["gpt-6-luna"]))
    assert budget.reserved == 0.0
    rec = lines(log)[0]
    assert rec["attempts"] == 2 and "RateLimitError" in rec["error"]


def test_retry_on_server_error_keeps_failed_attempt_spent(tmp_path):
    client = FakeClient(status_error(openai.InternalServerError, 500), response("fine"))
    m, budget, *_ = make(tmp_path, client)
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i", max_output_tokens=1000)
    assert r.ok and len(client.calls) == 2
    wc = budget.worst_case("gpt-6-luna", estimate_tokens("s\ni"), 1000)
    assert math.isclose(budget.spent, wc + cost(USAGE, PRICES["gpt-6-luna"]))
    assert math.isclose(r.cost, budget.spent)


def test_exception_on_every_attempt(tmp_path):
    client = FakeClient(openai.APIConnectionError(request=_REQ))
    m, budget, _, log = make(tmp_path, client)
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i", max_output_tokens=1000)
    assert not r.ok and r.status == "error" and r.text == ""
    assert len(client.calls) == LLM.MAX_ATTEMPTS == 3
    wc = budget.worst_case("gpt-6-luna", estimate_tokens("s\ni"), 1000)
    assert math.isclose(budget.spent, 3 * wc)  # every attempt may have been billed
    assert math.isclose(r.cost, 3 * wc)
    assert budget.reserved == 0.0
    assert "APIConnectionError" in r.error
    assert lines(log)[0]["status"] == "error"


def test_timeout_error_is_retried(tmp_path):
    client = FakeClient(openai.APITimeoutError(request=_REQ), response("ok"))
    m, *_ = make(tmp_path, client)
    assert m.call(label="t", model="gpt-6-luna", instructions="s", input="i").ok
    assert len(client.calls) == 2


@pytest.mark.parametrize("cls,code", [(openai.BadRequestError, 400),
                                      (openai.AuthenticationError, 401),
                                      (openai.APIStatusError, 408)])  # fmt: skip
def test_no_retry_on_other_4xx(tmp_path, cls, code):
    client = FakeClient(status_error(cls, code), response("never"))
    m, budget, *_ = make(tmp_path, client)
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i")
    assert not r.ok and r.status == "error" and len(client.calls) == 1
    assert budget.spent == 0.0 and budget.reserved == 0.0  # rejected outright: not billed


def test_unexpected_exception_is_not_retried_and_kept_spent(tmp_path):
    client = FakeClient(RuntimeError("weird"))
    m, budget, *_ = make(tmp_path, client)
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i")
    assert not r.ok and r.status == "error" and len(client.calls) == 1
    assert budget.spent > 0 and budget.reserved == 0.0


def test_retry_stops_when_budget_runs_out(tmp_path):
    client = FakeClient(openai.APIConnectionError(request=_REQ))
    m, budget, *_ = make(tmp_path, client, limit=0.0105)  # usable 0.00945
    # worst case of one call: ~0.0080; the second reservation does not fit
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i", max_output_tokens=16000)
    assert not r.ok and len(client.calls) == 1 and "no budget" in r.error
    assert budget.spent <= budget.limit and budget.reserved == 0.0


def test_weird_response_object_never_raises(tmp_path):
    class Weird:
        @property
        def output_text(self):
            raise ValueError("no text")

        usage = None
        status = None

    m, *_ = make(tmp_path, FakeClient(Weird()))
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i")
    assert r.ok and r.text == "" and r.status == "completed"


def test_list_input_and_transcript_dedup(tmp_path):
    client = FakeClient()
    m, _, _, log = make(tmp_path, client)
    spec = {"role": "user", "content": "SPEC " * 100}
    for stage in ("stage one", "stage two"):
        m.call(label=stage, model="gpt-6-luna", instructions="SYSTEM PROMPT",
               input=[spec, {"role": "user", "content": stage}])  # fmt: skip
    a, b = lines(log)
    assert a["instructions"]["text"] == "SYSTEM PROMPT"
    assert (
        "text" not in b["instructions"] and b["instructions"]["sha1"] == a["instructions"]["sha1"]
    )
    assert a["input"][0]["text"] == spec["content"] and "text" not in b["input"][0]
    assert a["input"][1]["text"] == "stage one" and b["input"][1]["text"] == "stage two"
    assert a["input"][0]["role"] == "user"
    assert client.calls[0]["input"][0] is spec
    # the estimate covers the messages' text
    assert a["est_input_tokens"] >= estimate_tokens("SYSTEM PROMPT" + spec["content"])


def test_transcript_lines_are_valid_json_with_odd_objects(tmp_path):
    odd = {"input_tokens": 10, "output_tokens": 5, "weird": {1, 2}, "obj": object()}
    client = FakeClient(response('x\n"quoted" ☃ \ud800', usage=odd))
    m, _, _, log = make(tmp_path, client)
    r = m.call(label="odd", model="gpt-6-luna", instructions="s", input="i")
    assert r.ok
    for _ in range(3):
        m.call(label="skip", model="gpt-6-astra", instructions="s", input="i")
    raw = log.read_bytes()
    assert b"\r\n" not in raw
    recs = lines(log)
    assert len(recs) == 4 and recs[0]["label"] == "odd"


def test_concurrency_never_exceeds_cap(tmp_path):
    client = FakeClient(delay=0.05)
    m, budget, *_ = make(tmp_path, client, max_concurrency=2)
    results = []

    def go(i):
        results.append(m.call(label=f"c{i}", model="gpt-6-luna", instructions="s", input="i"))

    threads = [threading.Thread(target=go, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 10 and all(r.ok for r in results)
    assert client.max_in_flight == 2
    assert math.isclose(budget.spent, 10 * cost(USAGE, PRICES["gpt-6-luna"]))


def test_concurrency_cap_is_at_most_four(tmp_path):
    client = FakeClient(delay=0.05)
    m, *_ = make(tmp_path, client, max_concurrency=16)
    threads = [
        threading.Thread(
            target=m.call, kwargs=dict(label="c", model="gpt-6-luna", instructions="s", input="i")
        )
        for _ in range(12)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(client.calls) == 12
    assert 1 <= client.max_in_flight <= 4


def test_concurrent_calls_never_overspend(tmp_path):
    client = FakeClient(response("ok", usage=None), delay=0.02)  # usage None: worst case kept
    m, budget, *_ = make(tmp_path, client, limit=0.04)  # usable 0.036: 4 luna calls of ~0.008
    results = []
    lock = threading.Lock()

    def go():
        r = m.call(label="c", model="gpt-6-luna", instructions="s", input="i",
                   max_output_tokens=16000)  # fmt: skip
        with lock:
            results.append(r)

    threads = [threading.Thread(target=go) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    made = [r for r in results if r.ok]
    assert len(made) == len(client.calls) == 4
    assert sum(r.status == "skipped_budget" for r in results) == 8
    assert budget.spent <= budget.limit + 1e-12


def test_default_client_is_built_lazily_without_retries(tmp_path, monkeypatch):
    seen = {}

    def fake_openai(**kw):
        seen.update(kw)
        return FakeClient()

    monkeypatch.setattr(llm_mod.openai, "OpenAI", fake_openai)
    m, *_ = make(tmp_path, None)
    assert seen == {"max_retries": 0}
    assert m.call(label="t", model="gpt-6-luna", instructions="s", input="i").ok


def test_client_construction_failure_never_raises(tmp_path, monkeypatch):
    def boom(**kw):
        raise openai.OpenAIError("no api key")

    monkeypatch.setattr(llm_mod.openai, "OpenAI", boom)
    m, budget, *_ = make(tmp_path, None)
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i")
    assert not r.ok and r.status == "error" and "no api key" in r.error
    assert budget.reserved == 0.0


def test_unwritable_log_never_raises(tmp_path):
    budget, clock = Budget(1, PRICES), Clock(1000)
    bad = tmp_path / "file"
    bad.write_text("x")
    m = LLM(TASK, budget, clock, bad / "sub" / "t.jsonl", client=FakeClient())
    assert m.call(label="t", model="gpt-6-luna", instructions="s", input="i").ok


# ------------------------------------------------------------------- extract_code

CLS = "class Strategy:\n    def act(self, obs):\n        return None\n"


@pytest.mark.parametrize("tag", ["python", "py", "Python", "PYTHON", "python3", "", "py title=x"])
def test_extract_fenced(tag):
    text = f"Here you go:\n```{tag}\n{CLS}```\nThat's it."
    assert extract_code(text) == CLS


def test_extract_crlf():
    text = f"intro\r\n```python\r\n{CLS.replace(chr(10), chr(13) + chr(10))}```\r\n"
    assert extract_code(text) == CLS


def test_extract_longest_python_block():
    usage_example = "s = Strategy(0)\nprint(s.act({}))\n"
    text = f"```python\n{CLS}```\n\nUsage:\n```python\n{usage_example}```\n"
    assert extract_code(text) == CLS
    text = f"```py\n{usage_example}```\n```python\n{CLS}```"
    assert extract_code(text) == CLS


def test_extract_ignores_other_languages():
    long_json = "{" + '"a": 1, ' * 200 + '"b": 2}'
    text = f"```json\n{long_json}\n```\n```python\n{CLS}```"
    assert extract_code(text) == CLS
    assert extract_code(f"```json\n{long_json}\n```") is None
    assert extract_code("```bash\nls -la\n```") is None


def test_extract_prefers_block_that_parses():
    broken = "def f(:\n" + "    x = 1\n" * 50
    text = f"```python\n{broken}```\n```python\n{CLS}```"
    assert extract_code(text) == CLS
    assert extract_code(f"```python\n{broken}```") == broken


def test_extract_unclosed_fence():
    text = f"```python\n{CLS}"
    assert extract_code(text) == CLS


def test_extract_four_backticks_and_tilde_fences():
    assert extract_code(f"````python\n{CLS}````\n") == CLS
    assert extract_code(f"~~~python\n{CLS}~~~\n") == CLS
    # a ``` line inside a ```` block does not close it
    inner = 'DOC = """\n```\n"""\n' + CLS
    assert extract_code(f"````python\n{inner}````") == inner


def test_extract_unfenced():
    assert extract_code(CLS) == CLS
    assert extract_code("\n\n" + CLS + "\n\n") == CLS
    assert extract_code("x = 1\nprint(x)\n") is None  # no class
    assert extract_code("Sorry, I can't do that.") is None
    assert extract_code("") is None
    assert extract_code(None) is None  # type: ignore[arg-type]


# ------------------------------------------------------------------- review: adversarial


def test_caller_timeout_bounds_the_whole_call_incl_retries(tmp_path, monkeypatch):
    # A request that times out has used the caller's whole timeout_s: retrying it would make
    # the call take 2-3x timeout_s and count 2-3 worst cases as spent.
    monkeypatch.setattr(LLM, "MIN_CALL_S", 0.05)

    class Slow(FakeClient):
        def create(self, **kw):
            with self.lock:
                self.calls.append(kw)
            time.sleep(kw["timeout"])
            raise openai.APITimeoutError(request=_REQ)

    client = Slow()
    m, budget, *_ = make(tmp_path, client)
    t0 = time.monotonic()
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i",
               max_output_tokens=1000, timeout_s=0.3)  # fmt: skip
    took = time.monotonic() - t0
    assert not r.ok and r.status == "error" and "APITimeoutError" in r.error
    assert len(client.calls) == 1 and took < 0.6
    wc = budget.worst_case("gpt-6-luna", estimate_tokens("s\ni"), 1000)
    assert math.isclose(budget.spent, wc) and budget.reserved == 0.0


def test_retries_share_the_caller_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(LLM, "MIN_CALL_S", 0.01)
    client = FakeClient(openai.APIConnectionError(request=_REQ), response("ok"), delay=0.1)
    m, *_ = make(tmp_path, client)
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i", timeout_s=5.0)
    assert r.ok and len(client.calls) == 2
    first, second = (c["timeout"] for c in client.calls)
    assert first <= 5.0 and second <= first - 0.09  # what the first attempt used is gone


def test_response_whose_usage_raises_frees_the_reservation(tmp_path):
    class Bad:
        output_text = "x"
        status = "completed"

        @property
        def usage(self):
            raise RuntimeError("broken usage")

    m, budget, *_ = make(tmp_path, FakeClient(Bad()))
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i", max_output_tokens=1000)
    assert not r.ok and r.status == "error"
    assert budget.reserved == 0.0  # not leaked: kept as spent (the call was made)
    wc = budget.worst_case("gpt-6-luna", estimate_tokens("s\ni"), 1000)
    assert math.isclose(budget.spent, wc) and math.isclose(r.cost, wc)


def test_exception_with_broken_str_frees_the_reservation(tmp_path):
    class Nasty(Exception):
        def __str__(self):
            raise ValueError("no str")

    m, budget, *_ = make(tmp_path, FakeClient(Nasty()))
    r = m.call(label="t", model="gpt-6-luna", instructions="s", input="i")
    assert not r.ok and r.status == "error" and "Nasty" in r.error
    assert budget.reserved == 0.0 and budget.spent > 0


def test_extract_indented_fence():
    body = "".join("   " + line + "\n" for line in CLS.splitlines())
    text = f"1. The file:\n   ```python\n{body}   ```\n"
    assert extract_code(text) == CLS


def test_extract_keeps_broken_file_over_parsing_snippet():
    # the repair loop must see the file's SyntaxError, not a usage snippet without a class
    broken = CLS + "    def f(:\n        pass\n"
    text = f"```python\n{broken}```\nUsage:\n```python\ns = Strategy(0)\n```\n"
    assert extract_code(text) == broken
    # a complete file still wins over a truncated longer one
    text = f"```python\n{CLS}```\n```python\n{CLS * 3}    def g(:\n"
    assert extract_code(text) == CLS


def test_extract_never_raises_on_odd_input():
    for text in ["```", "```\n```", "``````", "~~~~", "```python```", "\x00```python\n\x00\n```",
                 "```python\n" + "(" * 300 + "\n```", "class A:\n" + " " * 10 + "(" * 300]:
        extract_code(text)
