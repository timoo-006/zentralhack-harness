"""Fixture: an endless search loop that swallows every Exception."""


class Strategy:
    def __init__(self, player_id):
        self.player_id = player_id

    def act(self, obs):
        best = 0
        while True:
            try:
                best = (best * 31 + 7) % 1000003
                if best < 0:
                    raise ValueError("never")
            except Exception:
                pass
