"""Reference strategies for Epsilon.

The map differs per match, so every strategy builds its graph from the first observation.
"""

import random
from collections import Counter

from arena.core import Strategy

from testgames.epsilon.game import N_TURNS, UPKEEP, travel_distances


class _MapAware(Strategy):
    """Caches neighbours, edge lengths and travel times from the first observation."""

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id)
        self.neighbours: list[list[int]] | None = None
        self.length: dict[tuple[int, int], int] = {}
        self.dist: list[list[int]] = []

    def _setup(self, obs: dict) -> None:
        if self.neighbours is not None:
            return
        n = obs["n_nodes"]
        self.neighbours = [[] for _ in range(n)]
        for u, v, length in obs["edges"]:
            self.neighbours[u].append(v)
            self.neighbours[v].append(u)
            self.length[(u, v)] = self.length[(v, u)] = length
        self.dist = travel_distances(n, obs["edges"])

    def _next_hop(self, src: int, dst: int) -> int:
        """First node after `src` on a shortest (by travel time) path to `dst`; lowest id wins."""
        assert self.neighbours is not None
        return min(
            v
            for v in self.neighbours[src]
            if self.length[(src, v)] + self.dist[v][dst] == self.dist[src][dst]
        )


class DoNothing(Strategy):
    """Never moves. Pays no upkeep and owns nothing: always exactly 0."""

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
            weights = [self.rng.random() for _ in options]
            destinations = Counter(self.rng.choices(options, weights=weights, k=count))
            for to_node, k in sorted(destinations.items()):
                if to_node != node:
                    moves.append((node, to_node, k))
        return moves


class GreedyCapture(_MapAware):
    """Captures the nodes it does not own, cheapest and most valuable first; idle units go home.

    Every node it does not own needs one unit more than the opponent has on it or heading to
    it, minus its own units already there or heading there. Free units (on the base, or on
    a node it already owns that no opponent unit is heading to) are paired greedily with the
    needy nodes by value * turns left after capture / (travel time + 1). Units with nothing
    to do walk home. Every unit moves one edge along a shortest path by travel time.

    What it ignores: defending ahead of time, the upkeep of the walk, and the opponent's
    reaction.
    """

    def act(self, obs: dict) -> list[tuple[int, int, int]]:
        self._setup(obs)
        n = obs["n_nodes"]
        base = obs["player_base"]
        turn = obs["turn"]
        mine = obs["player_units"]
        theirs = obs["opponent_units"]
        value = obs["node_value"]
        owned = obs["player_owned"]

        heading = [0] * n
        for _, to_node, count, _ in obs["player_transits"]:
            heading[to_node] += count
        threat = [0] * n
        for _, to_node, count, _ in obs["opponent_transits"]:
            threat[to_node] += count

        need = [0] * n
        for node in range(n):
            if node in (base, obs["opponent_base"]) or owned[node] or value[node] == 0:
                continue
            need[node] = max(0, theirs[node] + threat[node] + 1 - mine[node] - heading[node])

        free = [0] * n
        free[base] = mine[base]
        for node in range(n):
            if node == base or mine[node] == 0:
                continue
            if owned[node] and threat[node] == 0 and theirs[node] == 0:
                free[node] = mine[node]  # captured and safe: done here
            elif owned[node]:
                free[node] = max(0, mine[node] - theirs[node] - threat[node])
            elif node == obs["opponent_base"] or value[node] == 0:
                free[node] = mine[node]

        def score(src: int, dst: int) -> float:
            d = self.dist[src][dst]
            turns_owned = N_TURNS - turn - d - 1
            return (value[dst] * turns_owned - UPKEEP * 2 * d) / (d + 1)

        pairs = sorted(
            (-score(src, dst), self.dist[src][dst], src, dst)
            for src in range(n)
            if free[src] > 0
            for dst in range(n)
            if need[dst] > 0
        )
        orders: Counter[tuple[int, int]] = Counter()
        for neg_score, _, src, dst in pairs:
            if neg_score >= 0:
                break
            k = min(free[src], need[dst])
            if k == 0:
                continue
            free[src] -= k
            need[dst] -= k
            orders[(src, self._next_hop(src, dst))] += k

        for node in range(n):
            if node != base and free[node] > 0:
                orders[(node, self._next_hop(node, base))] += free[node]
        return [(src, hop, k) for (src, hop), k in sorted(orders.items())]
