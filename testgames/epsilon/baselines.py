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
    it, but a node the opponent is on or heading to is only attacked if its value pays the
    upkeep of that squad. Units that lead on a node they do not own yet stay there; on an
    owned node, as many units stay as the opponent has on it or heading to it (a tie keeps
    the node), if the node's value pays their upkeep. All other units are free. Free units
    are paired greedily (best first) with the nodes still in need, scored by
    value * min(turns left after capture, HORIZON) minus the upkeep of the whole squad there
    and back, divided by (travel time + 1); pairs that do not pay are skipped. Units with
    nothing to do walk home. Every unit moves one edge along a shortest path by travel time.

    What it ignores: sending defenders to a threatened node, how long a capture will last,
    and the opponent's reaction.
    """

    HORIZON = 20

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

        free = [0] * n
        squad = [0] * n  # units a capture of the node needs in total
        need = [0] * n  # of those, still missing
        for node in range(n):
            if node == base:
                free[node] = mine[node]
                continue
            enemy = theirs[node] + threat[node]
            if owned[node]:  # a tie keeps the node: hold it if that pays, free the rest
                keep = enemy if enemy * UPKEEP <= value[node] else 0
                free[node] = max(0, mine[node] - keep)
                continue
            leading = mine[node] > theirs[node]
            if not leading:
                free[node] = mine[node]
            if value[node] == 0 or (enemy > 0 and (enemy + 1) * UPKEEP > value[node]):
                free[node] = mine[node]  # nothing to win, or a fight that does not pay
                continue
            squad[node] = enemy + 1
            here = mine[node] if leading else 0
            need[node] = max(0, squad[node] - here - heading[node])

        def score(src: int, dst: int) -> float:
            d = self.dist[src][dst]
            turns_owned = min(N_TURNS - turn - d - 1, self.HORIZON)
            return (value[dst] * turns_owned - UPKEEP * squad[dst] * (2 * d + 2)) / (d + 1)

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
            if src != dst:
                orders[(src, self._next_hop(src, dst))] += k

        for node in range(n):
            if node != base and free[node] > 0:
                orders[(node, self._next_hop(node, base))] += free[node]
        return [(src, hop, k) for (src, hop), k in sorted(orders.items())]
