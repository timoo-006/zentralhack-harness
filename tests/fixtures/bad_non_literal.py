"""Fixture: returns an object that is not a Python literal."""


class Move:
    def __init__(self, a, b, n):
        self.a, self.b, self.n = a, b, n


class Strategy:
    def __init__(self, player_id):
        self.player_id = player_id

    def act(self, obs):
        return [Move(0, 1, 1)]
