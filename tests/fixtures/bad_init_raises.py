"""Fixture: __init__ raises."""


class Strategy:
    def __init__(self, player_id):
        raise ValueError("cannot initialise")

    def act(self, obs):
        return []
