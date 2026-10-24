"""Fixture: a different failure every turn (NaN, numpy-like ints, sys.exit, huge reply, ...)."""

import sys


class FakeInt(int):
    def __repr__(self):
        return f"np.int64({int(self)})"


class Strategy:
    def __init__(self, player_id):
        self.turns = 0

    def act(self, obs):
        self.turns += 1
        kind = self.turns % 7
        if kind == 0:
            return [(0, 1, float("nan"))]
        if kind == 1:
            return [(FakeInt(0), FakeInt(1), FakeInt(1))]
        if kind == 2:
            sys.exit(3)
        if kind == 3:
            return "x" * 2_000_000
        if kind == 4:
            raise KeyboardInterrupt
        if kind == 5:
            return self.act(obs) if self.turns > 0 else None  # RecursionError
        return []
