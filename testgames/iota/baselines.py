"""Reference strategies for Iota.

The map differs per match, so every strategy builds its graph from the first observation.
"""

import random
from collections import Counter

from arena.core import Strategy

from testgames.iota.game import UPKEEP, hop_distances


class _MapAware(Strategy):
    """Caches neighbours and hop distances from the first observation."""

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id)
        self.neighbours: list[list[int]] | None = None
        self.dist: list[list[int]] = []

    def _setup(self, obs: dict) -> None:
        if self.neighbours is not None:
            return
        n = obs["n_nodes"]
        self.neighbours = [[] for _ in range(n)]
        for u, v in obs["edges"]:
            self.neighbours[u].append(v)
            self.neighbours[v].append(u)
        self.dist = hop_distances(n, obs["edges"])

    def _next_hop(self, src: int, dst: int) -> int:
        """First node after `src` on a shortest path to `dst`; lowest id wins."""
        assert self.neighbours is not None
        return min(v for v in self.neighbours[src] if self.dist[v][dst] == self.dist[src][dst] - 1)


class DoNothing(Strategy):
    """Never moves. Pays no upkeep and fulfils nothing: always exactly 0."""

    def act(self, obs: dict) -> list[tuple[int, int, int]]:
        return []


class RandomStrategy(_MapAware):
    """Every unit on a node picks that node or one of its neighbours at random, each turn.

    The RNG is seeded from player_id so matches stay reproducible.
    """

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id)
        self.rng = random.Random(player_id)

    def act(self, obs: dict) -> list[tuple[int, int, int]]:
        self._setup(obs)
        assert self.neighbours is not None
        moves = []
        for node, count in enumerate(obs["player_units"]):
            if count <= 0:
                continue
            options = [node, *self.neighbours[node]]
            destinations = Counter(self.rng.choices(options, k=count))
            for to_node, k in sorted(destinations.items()):
                if to_node != node:
                    moves.append((node, to_node, k))
        return moves


class GreedyDispatch(_MapAware):
    """Sends the nearest free units to the most valuable reachable orders; idle units go home.

    Orders are taken by reward (highest first). An order on node `x` with `turns_left` L needs
    one unit more than the opponent has on `x` now, from units at most L edges away (a unit
    d edges away is on `x` at the end of turn `turn + d - 1`). It is taken only if its reward
    beats the upkeep of the squad going there and back home. Every unit moves one edge along a
    shortest path; units with nothing to do walk home.

    What it ignores: opponent units on their way, orders that may appear later, and parking
    units centrally to wait for them.
    """

    def act(self, obs: dict) -> list[tuple[int, int, int]]:
        self._setup(obs)
        n = obs["n_nodes"]
        base = obs["player_base"]
        theirs = obs["opponent_units"]
        free = list(obs["player_units"])
        orders: Counter[tuple[int, int]] = Counter()

        for node, reward, turns_left in sorted(obs["orders"], key=lambda o: (-o[1], o[2], o[0])):
            need = theirs[node] + 1
            pool = sorted(
                (self.dist[src][node], src)
                for src in range(n)
                if free[src] > 0 and self.dist[src][node] <= turns_left
            )
            squad: list[tuple[int, int, int]] = []  # (distance, src, count)
            missing = need
            for d, src in pool:
                k = min(free[src], missing)
                squad.append((d, src, k))
                missing -= k
                if missing == 0:
                    break
            if missing > 0:
                continue
            back = self.dist[node][base]
            cost = sum(UPKEEP * k * (d + back) for d, _, k in squad)
            if reward <= cost:
                continue
            for d, src, k in squad:
                free[src] -= k
                if d > 0:
                    orders[(src, self._next_hop(src, node))] += k

        for node in range(n):
            if node != base and free[node] > 0:
                orders[(node, self._next_hop(node, base))] += free[node]
        return [(src, hop, k) for (src, hop), k in sorted(orders.items())]
