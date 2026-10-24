"""Tests for harness.budget: worst case bounds pricing.cost, reservations never oversubscribe."""

import math
import random
import threading

import pytest

from harness.budget import Budget, Reservation, estimate_tokens
from harness.pricing import cost

# The shape of arena's prices.json (task.json "prices").
PRICES = {
    "gpt-6-astra": {
        "input": 10.0,
        "cached_input": 1.0,
        "cache_write": 12.5,
        "output": 50.0,
        "long_context": {
            "above_input_tokens": 272000,
            "input": 20.0,
            "cached_input": 2.0,
            "cache_write": 25.0,
            "output": 75.0,
        },
    },
    "gpt-6.1-sol": {
        "input": 2.0,
        "cached_input": 0.1,
        "cache_write": 2.5,
        "output": 10.0,
        "long_context": {
            "above_input_tokens": 272000,
            "input": 4.0,
            "cached_input": 0.2,
            "cache_write": 5.0,
            "output": 15.0,
        },
    },
    "gpt-6-luna": {
        "input": 0.1,
        "cached_input": 0.01,
        "cache_write": 0.125,
        "output": 0.5,
        "long_context": {
            "above_input_tokens": 272000,
            "input": 0.2,
            "cached_input": 0.02,
            "cache_write": 0.25,
            "output": 0.75,
        },
    },
    "plain": {"input": 1.0, "output": 2.0},  # no cache or long-context prices
}


def usage(inp, out, cached=0, written=0):
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": written},
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": inp + out,
    }


def test_limit_and_defaults():
    b = Budget(0.5, PRICES)
    assert math.isclose(b.limit, 0.45)
    assert b.spent == 0.0 and b.reserved == 0.0
    assert math.isclose(b.remaining(), 0.45)
    assert math.isclose(Budget(None, PRICES).limit, 0.9)
    assert math.isclose(Budget(None, PRICES, default_limit_usd=2.0).limit, 1.8)
    assert math.isclose(Budget(10, PRICES, safety_frac=0.5).limit, 5.0)


def test_estimate_tokens():
    assert estimate_tokens("") == 50
    assert estimate_tokens("abc") == 51
    assert estimate_tokens("abcd") == 52
    assert estimate_tokens("x" * 3000) == 1050


@pytest.mark.parametrize("model", list(PRICES))
def test_worst_case_bounds_cost(model):
    b = Budget(100, PRICES)
    rng = random.Random(1234)
    for _ in range(3000):
        max_in = rng.choice([100, 5000, 50_000, 271_999, 272_000, 272_001, 400_000])
        max_out = rng.choice([0, 1, 1000, 16_000, 64_000])
        inp = rng.randint(0, max_in)
        out = rng.randint(0, max_out)
        cached = rng.randint(0, inp)
        written = rng.randint(0, inp - cached)
        if rng.random() < 0.2:  # all of it written to the cache: the most expensive input
            cached, written = 0, inp
        actual = cost(usage(inp, out, cached, written), PRICES[model])
        bound = b.worst_case(model, max_in, max_out)
        assert bound + 1e-12 >= actual, (model, max_in, max_out, inp, out, cached, written)


def test_worst_case_uses_cache_write_and_long_context():
    b = Budget(100, PRICES)
    # cache write (12.5) is pricier than plain input (10)
    assert math.isclose(b.worst_case("gpt-6-astra", 1_000_000 // 1000, 0), 1000 * 12.5 / 1e6)
    # above 272k input: long-context prices
    wc = b.worst_case("gpt-6-astra", 300_000, 1000)
    assert math.isclose(wc, (300_000 * 25.0 + 1000 * 75.0) / 1e6)
    # at exactly the threshold: normal prices
    wc = b.worst_case("gpt-6-astra", 272_000, 1000)
    assert math.isclose(wc, (272_000 * 12.5 + 1000 * 50.0) / 1e6)
    assert math.isclose(b.worst_case("plain", 1000, 1000), (1000 * 1.0 + 1000 * 2.0) / 1e6)


def test_unknown_model_is_never_affordable():
    b = Budget(100, PRICES)
    assert b.worst_case("nope", 10, 10) == math.inf
    assert b.reserve("nope", 10, 10) is None
    assert not b.can_afford("nope", 10, 10)


def test_reserve_settle_release():
    b = Budget(1.0, PRICES)  # limit 0.9
    r = b.reserve("gpt-6.1-sol", 10_000, 10_000)  # 10k*2.5 + 10k*10 = 0.125
    assert isinstance(r, Reservation)
    assert math.isclose(r.amount, 0.125)
    assert math.isclose(b.reserved, 0.125)
    assert math.isclose(b.remaining(), 0.9 - 0.125)
    u = usage(8000, 3000, cached=4000)
    actual = b.settle(r, u)
    assert math.isclose(actual, cost(u, PRICES["gpt-6.1-sol"]))
    assert math.isclose(b.spent, actual)
    assert b.reserved == 0.0
    # settling twice books nothing twice
    assert b.settle(r, u) == 0.0
    assert math.isclose(b.spent, actual)

    r2 = b.reserve("gpt-6.1-sol", 10_000, 10_000)
    b.release(r2)
    b.release(r2)
    assert b.reserved == 0.0
    assert math.isclose(b.spent, actual)


def test_settle_without_usage_keeps_the_reservation_spent():
    b = Budget(1.0, PRICES)
    r = b.reserve("gpt-6-luna", 1000, 1000)
    assert math.isclose(b.settle(r, None), r.amount)
    assert math.isclose(b.spent, r.amount)
    assert b.reserved == 0.0


def test_settle_with_garbage_usage_keeps_the_reservation_spent():
    b = Budget(1.0, PRICES)
    r = b.reserve("gpt-6-luna", 1000, 1000)
    assert math.isclose(b.settle(r, {"input_tokens": "lots"}), r.amount)
    assert b.reserved == 0.0


def test_settle_accepts_sdk_like_usage_objects():
    class U:
        def model_dump(self):
            return usage(100, 100)

    b = Budget(1.0, PRICES)
    r = b.reserve("plain", 100, 100)
    assert math.isclose(b.settle(r, U()), (100 * 1.0 + 100 * 2.0) / 1e6)


def test_reserve_refuses_over_limit():
    b = Budget(0.03, PRICES)  # limit 0.027
    big = b.reserve("gpt-6-luna", 10_000, 60_000)  # 1.25e-3 + 0.03 = 0.03125 > 0.027
    assert big is None
    assert not b.can_afford("gpt-6-luna", 10_000, 60_000)
    ok = [b.reserve("gpt-6-luna", 10_000, 16_000) for _ in range(5)]  # 0.00925 each
    assert sum(r is not None for r in ok) == 2
    assert b.spent + b.reserved <= b.limit + 1e-12


def test_concurrent_reserve_never_oversubscribes():
    b = Budget(1.0, PRICES)
    barrier = threading.Barrier(16)
    got: list[Reservation] = []
    lock = threading.Lock()
    violations = []

    def worker(seed):
        rng = random.Random(seed)
        barrier.wait()
        for _ in range(200):
            r = b.reserve("gpt-6.1-sol", rng.randint(100, 5000), rng.randint(100, 5000))
            with b._lock:  # a consistent snapshot
                if b.spent + b.reserved > b.limit + 1e-9:
                    violations.append((b.spent, b.reserved))
            if r is None:
                continue
            with lock:
                got.append(r)
            if rng.random() < 0.5:
                b.settle(r, usage(rng.randint(0, 100), rng.randint(0, 100)))
            elif rng.random() < 0.5:
                b.release(r)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not violations
    assert b.spent + b.reserved <= b.limit + 1e-9
    for r in got:
        b.settle(r, None)
    assert b.reserved == 0.0 or math.isclose(b.reserved, 0.0, abs_tol=1e-12)
    assert b.spent <= b.limit + 1e-9
    assert b.summary()["open_reservations"] == 0


def test_malformed_prices_are_never_affordable_and_never_raise():
    prices = {
        "flat": 1.0,
        "nolong": {"input": 1.0, "output": 2.0, "long_context": 5},
        "strs": {"input": "x", "output": 2.0},
        "longstr": {"input": 1.0, "output": 2.0, "long_context": {"above_input_tokens": "9"}},
    }
    b = Budget(10.0, prices)
    for model in prices:
        assert b.worst_case(model, 100, 100) == math.inf or b.worst_case(model, 100, 100) >= 0
        b.can_afford(model, 100, 100)
        r = b.reserve(model, 100, 100)
        if r is not None:
            b.settle(r, {"input_tokens": 100, "output_tokens": 100})
    assert b.worst_case("flat", 1, 1) == math.inf and b.reserve("flat", 1, 1) is None
    assert b.reserve("strs", 1, 1) is None
    assert b.reserved == 0.0
