"""Fixture: prints 1 MB per turn (100 MB per match, over arena's 16 MB stderr limit)."""

import sys

CHUNK = "x" * (1 << 20)


class Strategy:
    def __init__(self, player_id):
        self.player_id = player_id

    def act(self, obs):
        print(CHUNK)
        sys.stdout.write(CHUNK)
        return []
