"""Fixture: an endless loop with a bare except (would swallow a BaseException timer)."""


class Strategy:
    def __init__(self, player_id):
        self.player_id = player_id

    def act(self, obs):
        x = 0
        while True:
            try:
                x = (x * 31 + 7) % 1000003
                [i * i for i in range(200)]
            except:  # noqa: E722
                pass
