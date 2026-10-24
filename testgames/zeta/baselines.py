"""Reference strategies for Zeta.

The map differs per match, so every strategy builds its graph from the first observation.
Actions are nested dicts {from_node: {to_node: count}} with plain int keys and values.
"""

import heapq
import random
from collections import Counter

from arena.core import Strategy

from testgames.zeta.game import (
    SERVE_LIMIT,
    UNIT_CAPACITY,
    UNITS_PER_PLAYER,
    UPKEEP_BASE,
    UPKEEP_PER_HOP,
    hop_distances,
)


def _to_action(moves: Counter) -> dict[int, dict[int, int]]:
    action: dict[int, dict[int, int]] = {}
    for (src, dst), k in moves.items():
        if k > 0:
            action.setdefault(int(src), {})[int(dst)] = int(k)
    return action


class _MapAware(Strategy):
    """Caches neighbours and hop distances from the first observation."""

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id)
        self.neighbours: list[list[int]] | None = None
        self.dist: list[list[int]] = []

    def _setup(self, obs: dict) -> None:
        if self.neighbours is not None:
            return
        n = obs["graph"]["nodes"]
        links = [(u, v) for u, v in obs["graph"]["links"]]
        self.neighbours = [[] for _ in range(n)]
        for u, v in links:
            self.neighbours[u].append(v)
            self.neighbours[v].append(u)
        self.dist = hop_distances(n, links)

    def _move_towards(self, units: list[int], target: list[int]) -> dict[int, dict[int, int]]:
        """Move surplus units one hop towards the nearest deficit nodes (closest pairs first)."""
        assert self.neighbours is not None
        surplus = {n: units[n] - t for n, t in enumerate(target) if units[n] > t}
        deficit = {n: t - units[n] for n, t in enumerate(target) if units[n] < t}
        moves: Counter[tuple[int, int]] = Counter()
        pairs = sorted((self.dist[s][d], s, d) for s in surplus for d in deficit)
        for _, src, dst in pairs:
            k = min(surplus[src], deficit[dst])
            if k == 0:
                continue
            surplus[src] -= k
            deficit[dst] -= k
            hop = min(
                v for v in self.neighbours[src] if self.dist[v][dst] == self.dist[src][dst] - 1
            )
            moves[(src, hop)] += k
        return _to_action(moves)


class DoNothing(Strategy):
    """Never moves. Pays no upkeep and earns nothing: always exactly 0."""

    def act(self, obs: dict) -> dict:
        return {}


class RandomStrategy(_MapAware):
    """Scatters units randomly over each occupied node and its neighbours.

    The RNG is seeded from player_id so matches stay reproducible.
    """

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id)
        self.rng = random.Random(player_id)

    def act(self, obs: dict) -> dict:
        self._setup(obs)
        assert self.neighbours is not None
        moves: Counter[tuple[int, int]] = Counter()
        for node, count in enumerate(obs["you"]["units"]):
            if count <= 0:
                continue
            options = [node, *self.neighbours[node]]
            weights = [self.rng.random() for _ in options]
            for to_node, k in Counter(self.rng.choices(options, weights=weights, k=count)).items():
                if to_node != node:
                    moves[(node, to_node)] += k
        return _to_action(moves)


def _served(own: int, other: int, orders: float) -> float:
    """Revenue of `own` units against `other` units at a node, with the serving limit."""
    cap = min(own, SERVE_LIMIT) * UNIT_CAPACITY
    total = cap + min(other, SERVE_LIMIT) * UNIT_CAPACITY
    if cap == 0 or orders == 0:
        return 0.0
    return cap * orders / max(total, orders)


class GreedyOrders(_MapAware):
    """Best response to where the rival is now, net of distance upkeep, ignoring travel.

    Each turn, every node's value is the mean of its orders over the forecast window. Units are
    assigned one at a time to the node where the next unit adds the most revenue minus that
    node's upkeep, against the rival's current units there, while that gain is positive.
    Unassigned units go home. Every unit then moves one hop towards its target.

    What it ignores: upkeep paid while travelling, how long orders last, and the rival's
    reaction.
    """

    def act(self, obs: dict) -> dict:
        self._setup(obs)
        base = obs["you"]["base"]
        rival = obs["rival"]["units"]
        n = obs["graph"]["nodes"]
        window = obs["orders"]
        value = [sum(row[i] for row in window) / len(window) for i in range(n)]
        upkeep = [UPKEEP_BASE + UPKEEP_PER_HOP * self.dist[base][i] for i in range(n)]
        target = [0] * n

        def gain(node: int) -> float:
            k = target[node]
            return (
                _served(k + 1, rival[node], value[node])
                - _served(k, rival[node], value[node])
                - upkeep[node]
            )

        heap = [(-gain(i), i) for i in range(n) if i != base and value[i] > 0]
        heapq.heapify(heap)
        for _ in range(UNITS_PER_PLAYER):
            if not heap:
                break
            neg_gain, node = heapq.heappop(heap)
            if -neg_gain <= 0:
                break
            target[node] += 1
            heapq.heappush(heap, (-gain(node), node))
        target[base] = UNITS_PER_PLAYER - sum(target)
        return self._move_towards(obs["you"]["units"], target)
