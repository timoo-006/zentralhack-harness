"""Reference strategies for Delta.

The map differs per match, so every strategy builds its graph from the first observation.
"""

import random
from collections import Counter

from arena.core import Strategy

from testgames.delta.game import (
    BUILD_COST,
    MAX_DRONES,
    N_TURNS,
    UPKEEP,
    hop_distances,
    served,
)


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

    def _move_towards(self, drones: list[int], target: list[int]) -> list[tuple[int, int, int]]:
        """Move surplus drones one hop towards the nearest deficit nodes (closest pairs first)."""
        assert self.neighbours is not None
        surplus = {n: drones[n] - t for n, t in enumerate(target) if drones[n] > t}
        deficit = {n: t - drones[n] for n, t in enumerate(target) if drones[n] < t}
        moves: Counter[tuple[int, int]] = Counter()
        for _, src, dst in sorted((self.dist[s][d], s, d) for s in surplus for d in deficit):
            k = min(surplus[src], deficit[dst])
            if k == 0:
                continue
            surplus[src] -= k
            deficit[dst] -= k
            hop = min(
                v for v in self.neighbours[src] if self.dist[v][dst] == self.dist[src][dst] - 1
            )
            moves[(src, hop)] += k
        return [(src, hop, k) for (src, hop), k in moves.items()]


class DoNothing(Strategy):
    """Never moves and never builds. Pays no upkeep and earns nothing: always exactly 0."""

    def act(self, obs: dict) -> dict:
        return {"moves": [], "build": 0}


class RandomStrategy(_MapAware):
    """Scatters drones randomly over each occupied node and its neighbours, and now and then
    buys a drone when it can afford one.

    Every move is legal. The RNG is seeded from player_id so matches stay reproducible.
    """

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id)
        self.rng = random.Random(player_id)

    def act(self, obs: dict) -> dict:
        self._setup(obs)
        assert self.neighbours is not None
        moves = []
        for node, count in enumerate(obs["player_drones"]):
            if count <= 0:
                continue
            options = [node, *self.neighbours[node]]
            weights = [self.rng.random() for _ in options]
            destinations = Counter(self.rng.choices(options, weights=weights, k=count))
            for to_node, n in destinations.items():
                if to_node != node:
                    moves.append((node, to_node, n))
        affordable = obs["player_money"] >= BUILD_COST
        room = sum(obs["player_drones"]) < MAX_DRONES
        build = 1 if affordable and room and self.rng.random() < 0.2 else 0
        return {"moves": moves, "build": build}


class GreedyBuilder(_MapAware):
    """Best response to the opponent drones last seen, on the mean demand seen so far.

    Each node's demand is estimated by the mean of every demand observed there. The opponent
    is assumed to keep the drone counts last seen on each node (0 where never seen). An exact
    dynamic program over nodes then picks how many drones to put on each node to maximise
    served demand minus upkeep (winner-take-most makes the gain per node non-concave, so a
    one-drone-at-a-time greedy would miss "outnumber the opponent" allocations). Unassigned
    drones go home; every drone moves one hop towards its target. It buys drones while one
    more drone adds enough per turn to pay back its price before the match ends.

    What it ignores: travel time and upkeep on the way, and the opponent's reaction.
    """

    PAYBACK_MARGIN = 10  # turns of travel and slack before a bought drone starts paying off

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id)
        self.demand_sum: list[float] = []
        self.seen_opponent: list[int] = []
        self.turns_seen = 0

    def act(self, obs: dict) -> dict:
        self._setup(obs)
        n = obs["n_nodes"]
        if not self.demand_sum:
            self.demand_sum = [0.0] * n
            self.seen_opponent = [0] * n
        self.turns_seen += 1
        for v in range(n):
            self.demand_sum[v] += obs["demand"][v]
            if obs["opponent_drones"][v] is not None:
                self.seen_opponent[v] = obs["opponent_drones"][v]
        mean = [s / self.turns_seen for s in self.demand_sum]

        drones = obs["player_drones"]
        owned = sum(drones)
        base = obs["player_base"]
        room = MAX_DRONES - owned
        affordable = max(0, int(obs["player_money"] // BUILD_COST))
        most = owned + min(room, affordable, 3)
        best, choice = self._allocate(base, mean, most)

        turns_left = N_TURNS - obs["turn"]
        build = 0
        while owned + build < most:
            extra = best[owned + build + 1] - best[owned + build]
            if extra * (turns_left - self.PAYBACK_MARGIN) <= BUILD_COST:
                break
            build += 1

        target = [0] * n
        k = max(range(owned + 1), key=lambda j: best[j])  # best use of the drones we have
        for v in range(n - 1, -1, -1):
            target[v] = choice[v][k]
            k -= target[v]
        target[base] += owned - sum(target)
        return {"moves": self._move_towards(drones, target), "build": build}

    def _allocate(
        self, base: int, mean: list[float], most: int
    ) -> tuple[list[float], list[list[int]]]:
        """best[j]: the most net gain from at most j drones off base; choice[v][j]: the drones
        on node v in that allocation, with nodes 0..v using at most j drones."""
        n = len(mean)
        best = [0.0] * (most + 1)
        choice = [[0] * (most + 1) for _ in range(n)]
        for v in range(n):
            if v == base or mean[v] <= 0:
                continue
            gains = [
                served(k, self.seen_opponent[v], mean[v]) - UPKEEP * k for k in range(most + 1)
            ]
            new = list(best)
            for j in range(most + 1):
                for k in range(1, j + 1):
                    value = best[j - k] + gains[k]
                    if value > new[j]:
                        new[j] = value
                        choice[v][j] = k
            best = new
        return best, choice
