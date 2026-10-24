"""Fixture: imports a third-party package (not available to strategies)."""

import numpy as np


class Strategy:
    def __init__(self, player_id):
        self.player_id = player_id

    def act(self, obs):
        return [tuple(int(x) for x in np.zeros(3))]
