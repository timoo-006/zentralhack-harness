"""Fixture: a tiny stdlib-only simulator for fast sandbox tests.

10 turns. A player scores 1 per turn in which it returned a list. Seed 99 hangs in step(),
seed 98 raises in step(), to exercise the sandbox's error paths.
"""

import time


class Game:
    n_players = 2

    def __init__(self, seed=None):
        self.seed = seed
        self.turn = 0
        self.points = [0.0, 0.0]
        self.last_ok = [True, True]

    def observe(self, player_id):
        return {"turn": self.turn, "edges": [(0, 1), (1, 2)], "points": list(self.points)}

    def step(self, actions):
        if self.seed == 99:
            time.sleep(120)
        if self.seed == 98 and self.turn == 3:
            raise RuntimeError("simulator bug at turn 3")
        for i, a in enumerate(actions):
            self.last_ok[i] = isinstance(a, list)
            if self.last_ok[i]:
                self.points[i] += 1.0
        self.turn += 1

    def is_over(self):
        return self.turn >= 10

    def scores(self):
        return list(self.points)

    def action_report(self, player_id):
        return {"well_formed": self.last_ok[player_id], "n_illegal": 0}


def selftest():
    g = Game(0)
    g.step([[], None])
    return [] if g.scores() == [1.0, 0.0] else [f"scores after one step: {g.scores()}"]
