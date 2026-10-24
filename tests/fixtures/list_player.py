"""Fixture: always returns an empty list (scores 1 per turn in tiny_sim)."""


class Strategy:
    def __init__(self, player_id):
        self.player_id = player_id

    def act(self, obs):
        return []
