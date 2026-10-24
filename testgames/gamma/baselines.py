"""Reference strategies for Gamma.

The map differs per match, so every strategy builds its distances from the first observation.
"""

import random

from arena.core import Strategy

from testgames.gamma.game import UNIT_TYPES, hop_distances


class _MapAware(Strategy):
    """Caches hop distances from the first observation."""

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id)
        self.dist: list[list[int]] | None = None

    def _setup(self, obs: dict) -> list[list[int]]:
        if self.dist is None:
            self.dist = hop_distances(obs["n_nodes"], obs["edges"])
        return self.dist

    def _step_towards(self, here: int, target: int, speed: int) -> int:
        """The node a unit of `speed` reaches this turn on a shortest path to `target`."""
        assert self.dist is not None
        hops = min(speed, self.dist[here][target])
        if hops == 0:
            return here
        return min(
            v
            for v in range(len(self.dist))
            if self.dist[here][v] == hops and self.dist[v][target] == self.dist[here][target] - hops
        )


class DoNothing(Strategy):
    """Never moves. Pays no running cost and earns nothing: always exactly 0."""

    def act(self, obs: dict) -> dict:
        return {"moves": {}}


class RandomStrategy(_MapAware):
    """Sends every unit to a uniformly random node within its reach (staying included).

    Every move is legal. The RNG is seeded from player_id so matches stay reproducible.
    """

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id)
        self.rng = random.Random(player_id)

    def act(self, obs: dict) -> dict:
        dist = self._setup(obs)
        moves = {}
        for unit_id, (kind, node) in obs["player_units"].items():
            speed = UNIT_TYPES[kind]["speed"]
            options = [v for v in range(obs["n_nodes"]) if 0 <= dist[node][v] <= speed]
            moves[unit_id] = self.rng.choice(options)
        return {"moves": moves}


class GreedyHarvest(_MapAware):
    """Assigns units one at a time to the (unit, node) pair with the best net gain.

    The gain of sending a unit to a node is what it would serve there over HORIZON turns
    after arriving (the node's stock on arrival, grown by its regeneration while the unit
    travels and serves, minus what the opponent's units there now could take and what
    already assigned own units take), capped by the unit's capacity per turn, minus its
    running cost for the travel turns and the serving turns. Units whose best gain is not
    positive go home. Every unit then moves as far as its speed allows along a shortest path
    to its target.

    What it ignores: the opponent's next moves, and that the opponent's units also deplete
    the stock.
    """

    HORIZON = 3

    def act(self, obs: dict) -> dict:
        dist = self._setup(obs)
        n = obs["n_nodes"]
        base = obs["player_base"]
        bases = {base, obs["opponent_base"]}

        opponent_capacity = [0] * n
        for kind, node in obs["opponent_units"].values():
            opponent_capacity[node] += UNIT_TYPES[kind]["capacity"]
        available = [
            max(0.0, obs["stock"][v] - opponent_capacity[v]) if v not in bases else 0.0
            for v in range(n)
        ]
        assigned = [0.0] * n

        units = dict(obs["player_units"])
        targets: dict[str, int] = {}
        while units:
            best = None
            for unit_id, (kind, node) in units.items():
                spec = UNIT_TYPES[kind]
                for v in range(n):
                    if v in bases:
                        continue
                    travel = -(-dist[node][v] // spec["speed"])  # ceil
                    stock = min(obs["stock_max"][v], available[v] + obs["regen"][v] * travel)
                    stock += obs["regen"][v] * (self.HORIZON - 1)
                    serve = min(spec["capacity"] * self.HORIZON, max(0.0, stock - assigned[v]))
                    gain = serve - spec["cost"] * (travel + self.HORIZON)
                    if best is None or gain > best[0]:
                        best = (gain, unit_id, v, serve)
            assert best is not None
            gain, unit_id, v, serve = best
            if gain <= 0:
                break
            targets[unit_id] = v
            assigned[v] += serve
            del units[unit_id]
        for unit_id in units:
            targets[unit_id] = base

        moves = {}
        for unit_id, (kind, node) in obs["player_units"].items():
            to = self._step_towards(node, targets[unit_id], UNIT_TYPES[kind]["speed"])
            if to != node:
                moves[unit_id] = to
        return {"moves": moves}
