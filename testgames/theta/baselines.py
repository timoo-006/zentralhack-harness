"""Reference strategies for Theta.

The map differs per match, so every strategy builds its graph from the first observation.
"""

import random
from collections import Counter

from arena.core import Strategy

from testgames.theta.game import (
    BASE_DEMAND_MEDIAN,
    SURGE_MULTIPLIER,
    UNIT_CAPACITY,
    UNIT_TYPES,
    UNIT_UPKEEP,
    UNITS_PER_PLAYER,
    hop_distances,
    split_demand,
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

    def _move_towards(
        self, unit_type: str, units: list[int], target: list[int]
    ) -> list[tuple[int, int, str, int]]:
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
        return [(src, hop, unit_type, k) for (src, hop), k in moves.items()]


class DoNothing(Strategy):
    """Never moves. Pays no upkeep and earns nothing: always exactly 0."""

    def act(self, obs: dict) -> list[tuple[int, int, str, int]]:
        return []


class RandomStrategy(_MapAware):
    """Scatters every unit to a random choice of its node and the node's neighbours.

    The RNG is seeded from player_id so matches stay reproducible.
    """

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id)
        self.rng = random.Random(player_id)

    def act(self, obs: dict) -> list[tuple[int, int, str, int]]:
        self._setup(obs)
        assert self.neighbours is not None
        moves = []
        for unit_type in UNIT_TYPES:
            for node, count in enumerate(obs[f"player_{unit_type}"]):
                if count <= 0:
                    continue
                options = [node, *self.neighbours[node]]
                weights = [self.rng.random() for _ in options]
                destinations = Counter(self.rng.choices(options, weights=weights, k=count))
                for to_node, n in destinations.items():
                    if to_node != node:
                        moves.append((node, to_node, unit_type, n))
        return moves


class GreedyScout(_MapAware):
    """Best response to the opponent's current units, net of upkeep, ignoring travel.

    Remembers the base demand of every node it has seen (demand / 3 while a surge is on).
    A node's value is its current demand if visible, else its remembered base demand, else
    the median base demand (so unexplored nodes attract units). Units are then added one at a
    time, heavy or light, wherever the next unit adds the most revenue minus upkeep against
    the opponent's current capacity there, while that gain is positive. Unassigned units go
    home; every unit moves one hop towards its target.

    What it ignores: upkeep paid while travelling, that surges end, and the opponent's reaction.
    """

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id)
        self.known: list[float | None] = []

    def act(self, obs: dict) -> list[tuple[int, int, str, int]]:
        self._setup(obs)
        n = obs["n_nodes"]
        base = obs["player_base"]
        if not self.known:
            self.known = [None] * n
        for v in range(n):
            d = obs["demand"][v]
            if d is not None:
                left = obs["surge_turns_left"][v]
                self.known[v] = d / SURGE_MULTIPLIER if left else d
        value = []
        for v in range(n):
            d = obs["demand"][v]
            if d is not None:
                value.append(d)
            elif self.known[v] is not None:
                value.append(self.known[v])
            else:
                value.append(BASE_DEMAND_MEDIAN)
        value[base] = 0.0
        value[obs["opponent_base"]] = 0.0
        opp_cap = [
            obs["opponent_heavy"][v] * UNIT_CAPACITY["heavy"]
            + obs["opponent_light"][v] * UNIT_CAPACITY["light"]
            for v in range(n)
        ]

        target = {t: [0] * n for t in UNIT_TYPES}
        cap = [0] * n
        left_units = dict(UNITS_PER_PLAYER)

        def gain(v: int, unit_type: str) -> float:
            before = split_demand(value[v], cap[v], opp_cap[v])[0]
            after = split_demand(value[v], cap[v] + UNIT_CAPACITY[unit_type], opp_cap[v])[0]
            return after - before - UNIT_UPKEEP[unit_type]

        while True:
            best = (0.0, -1, "")
            for v in range(n):
                if value[v] <= 0:
                    continue
                for unit_type in UNIT_TYPES:
                    if left_units[unit_type] == 0:
                        continue
                    g = gain(v, unit_type)
                    if g > best[0] + 1e-9:
                        best = (g, v, unit_type)
            if best[1] < 0:
                break
            _, v, unit_type = best
            target[unit_type][v] += 1
            cap[v] += UNIT_CAPACITY[unit_type]
            left_units[unit_type] -= 1

        moves = []
        for unit_type in UNIT_TYPES:
            target[unit_type][base] += left_units[unit_type]
            moves += self._move_towards(unit_type, obs[f"player_{unit_type}"], target[unit_type])
        return moves
