"""Fixture: raises on its 5th act call."""


class Strategy:
    def __init__(self, player_id):
        self.turns = 0

    def act(self, obs):
        self.turns += 1
        if self.turns == 5:
            raise RuntimeError("boom on turn 5")
        return []
