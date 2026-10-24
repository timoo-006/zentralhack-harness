"""Fixture: a simulator whose selftest reports a failure."""


class Game:
    n_players = 2

    def __init__(self, seed=None):
        self.turn = 0

    def observe(self, player_id):
        return {"turn": self.turn}

    def step(self, actions):
        self.turn += 1

    def is_over(self):
        return self.turn >= 3

    def scores(self):
        return [0.0, 0.0]


def selftest():
    return ["worked example 1: expected profit 12.5, got 10.0"]
