"""The money: reserve a call's worst-case cost before making it, settle with the real cost.

No LLM call is made unless `Budget.reserve` accepted its worst case, so concurrent calls can
never overspend together. Real costs come from `harness.pricing.cost`, the formula the
evaluation's gateway charges by. Everything is thread-safe.
"""

import itertools
import math
import threading
from dataclasses import dataclass

from harness.pricing import cost


@dataclass
class Reservation:
    model: str
    amount: float  # USD reserved (worst case)
    id: int


class Budget:
    """`limit` = `limit_usd * safety_frac`; `spent` + `reserved` never exceed it via reserve."""

    def __init__(
        self,
        limit_usd: float | None,
        prices: dict[str, dict],
        *,
        safety_frac: float = 0.90,
        default_limit_usd: float = 1.0,
    ) -> None:
        try:
            total = float(limit_usd) if limit_usd is not None else float(default_limit_usd)
        except (TypeError, ValueError):
            total = float(default_limit_usd)
        if not math.isfinite(total) or total < 0:
            total = float(default_limit_usd)
        self.limit_usd = total
        self.limit: float = total * safety_frac
        self.prices = prices or {}
        self.spent: float = 0.0
        self.reserved: float = 0.0
        self._open: dict[int, Reservation] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def worst_case(self, model: str, input_tokens: int, max_output_tokens: int) -> float:
        """An upper bound of `pricing.cost` for a call with at most these token counts.

        Every input token at the highest input-side price (uncached, cached or cache write:
        a cache write can cost more than plain input), every output token at the output price;
        above `long_context.above_input_tokens` the higher of both price tiers. Unknown model:
        infinity (never affordable), as for a malformed price entry: this never raises.
        """
        try:
            price = self.prices.get(model)
            if not price:
                return math.inf
            tiers = [price]
            long = price.get("long_context")
            if long and input_tokens > long.get("above_input_tokens", math.inf):
                tiers.append(long)
            p_in = max(
                max(t["input"], t.get("cached_input", t["input"]), t.get("cache_write", t["input"]))
                for t in tiers
            )
            p_out = max(t["output"] for t in tiers)
            return (max(0, input_tokens) * p_in + max(0, max_output_tokens) * p_out) / 1e6
        except Exception:
            return math.inf

    def reserve(self, model: str, input_tokens: int, max_output_tokens: int) -> Reservation | None:
        """Reserve the worst case of a call; None if it does not fit in what is left."""
        amount = self.worst_case(model, input_tokens, max_output_tokens)
        if not math.isfinite(amount):
            return None
        with self._lock:
            if self.spent + self.reserved + amount > self.limit:
                return None
            r = Reservation(model=model, amount=amount, id=next(self._ids))
            self._open[r.id] = r
            self.reserved += amount
            return r

    def settle(self, r: Reservation, usage) -> float:
        """Book the real cost of a call that was made and free its reservation; the cost.

        `usage` None (or unpriceable) keeps the whole reservation as spent: a failed or
        timed-out call may still have been billed. Settling twice books nothing twice.
        """
        actual = r.amount
        if usage is not None:
            try:
                actual = float(cost(usage, self.prices[r.model]))
                if not math.isfinite(actual) or actual < 0:
                    actual = r.amount
            except Exception:
                actual = r.amount
        with self._lock:
            if self._open.pop(r.id, None) is None:
                return 0.0
            # Exactly 0 when nothing is open: float residue (1e-17) must not look like a call
            # in flight to anyone waiting for reservations to settle.
            self.reserved = max(0.0, self.reserved - r.amount) if self._open else 0.0
            self.spent += actual
        return actual

    def release(self, r: Reservation) -> None:
        """Free a reservation whose call made no (billable) request at all."""
        with self._lock:
            if self._open.pop(r.id, None) is not None:
                self.reserved = max(0.0, self.reserved - r.amount) if self._open else 0.0

    def remaining(self) -> float:
        """USD still available: limit - spent - reserved."""
        with self._lock:
            return self.limit - self.spent - self.reserved

    def can_afford(self, model: str, input_tokens: int, max_output_tokens: int) -> bool:
        """Would `reserve` accept this call right now?"""
        return self.worst_case(model, input_tokens, max_output_tokens) <= self.remaining()

    def in_flight(self) -> int:
        """Number of open reservations (calls reserved but not yet settled or released)."""
        with self._lock:
            return len(self._open)

    def summary(self) -> dict:
        """Numbers for logs."""
        with self._lock:
            return {
                "limit_usd": self.limit_usd,
                "limit": self.limit,
                "spent": self.spent,
                "reserved": self.reserved,
                "open_reservations": len(self._open),
            }


def estimate_tokens(text: str) -> int:
    """A conservative token count for `text`: ceil(len / 3) + 50."""
    return math.ceil(len(text) / 3.0) + 50
