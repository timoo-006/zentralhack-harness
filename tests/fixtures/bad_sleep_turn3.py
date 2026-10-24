"""Fixture: sleeps 5 s on its 3rd act call."""

import time


class Strategy:
    def __init__(self, player_id):
        self.turns = 0

    def act(self, obs):
        self.turns += 1
        if self.turns == 3:
            time.sleep(5)
        return []
