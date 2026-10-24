"""Reference strategies for Eta.

The map is fixed, but every strategy still reads it from the first observation.
"""

import heapq
import random

from arena.core import Strategy

from testgames.eta.game import (
    BATTERY_MAX,
    DRAIN,
    MOVE_COST,
    UNIT_CAPACITY,
    UNITS_PER_PLAYER,
    hop_distances,
)


class DoNothing(Strategy):
    """Never moves. Its rovers stay home, earn nothing and never strand: always exactly 0."""

    def act(self, obs: dict) -> list[list[int]]:
        return []


class RandomStrategy(Strategy):
    """Each rover that can move goes to a random neighbour with probability 1/2.

    Every move is legal, but the rovers ignore their batteries and strand. The RNG is seeded
    from player_id so matches stay reproducible.
    """

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id)
        self.rng = random.Random(player_id)
        self.neighbours: list[list[int]] | None = None

    def act(self, obs: dict) -> list[list[int]]:
        if self.neighbours is None:
            self.neighbours = [[] for _ in range(obs["n_nodes"])]
            for u, v in obs["edges"]:
                self.neighbours[u].append(v)
                self.neighbours[v].append(u)
        moves = []
        for unit in obs["player_units"]:
            if unit["energy"] > 0 and self.rng.random() < 0.5:
                moves.append([unit["id"], self.rng.choice(self.neighbours[unit["node"]])])
        return moves


def _served(own: int, other: int, demand: float) -> float:
    """Revenue of `own` active rovers against `other` active rovers at a node."""
    cap, total = own * UNIT_CAPACITY, (own + other) * UNIT_CAPACITY
    if cap == 0 or demand == 0:
        return 0.0
    return cap * demand / max(total, demand)


class SafeGreedy(Strategy):
    """Greedy on this turn's demand, but never strands a rover.

    Targets: rovers are assigned one at a time to the node where one more rover adds the most
    revenue this turn, against the opponent's active rovers there, among the nodes a full
    battery can reach and still come back from. Rovers then move one hop towards their
    target, closest first, but only if they could still get home from where they arrive.
    A rover too low to stay another turn heads home; at home it recharges until it has at
    least 9 energy.

    What it ignores: hot turns ahead, the travel time, the opponent's reaction and the end of
    the match (where stranding no longer costs much).
    """

    MIN_GAIN = 0.5
    LEAVE_ENERGY = BATTERY_MAX - 1

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id)
        self.dist: list[list[int]] | None = None
        self.neighbours: list[list[int]] = []

    def _hop(self, here: int, target: int) -> int:
        assert self.dist is not None
        return min(
            v for v in self.neighbours[here] if self.dist[v][target] == self.dist[here][target] - 1
        )

    def act(self, obs: dict) -> list[list[int]]:
        n = obs["n_nodes"]
        if self.dist is None:
            self.dist = hop_distances(n, obs["edges"])
            self.neighbours = [[] for _ in range(n)]
            for u, v in obs["edges"]:
                self.neighbours[u].append(v)
                self.neighbours[v].append(u)
        dist = self.dist
        base = obs["player_base"]
        home = dist[base]
        demand = obs["demand"]

        opponent = [0] * n
        for node, energy in obs["opponent_units"]:
            if energy > 0:
                opponent[node] += 1

        # Nodes a rover leaving with LEAVE_ENERGY can reach, stay one turn on, and leave again.
        reachable = [
            v for v in range(n) if v != base and demand[v] > 0 and 4 * home[v] <= self.LEAVE_ENERGY
        ]
        target = [0] * n

        def gain(node: int) -> float:
            k = target[node]
            return _served(k + 1, opponent[node], demand[node]) - _served(
                k, opponent[node], demand[node]
            )

        heap = [(-gain(v), v) for v in reachable]
        heapq.heapify(heap)
        for _ in range(UNITS_PER_PLAYER):
            if not heap:
                break
            neg, node = heapq.heappop(heap)
            if -neg < self.MIN_GAIN:
                break
            target[node] += 1
            heapq.heappush(heap, (-gain(node), node))

        moves: list[list[int]] = []
        free = []
        for unit in obs["player_units"]:
            uid, node, energy = unit["id"], unit["node"], unit["energy"]
            if energy == 0:
                continue  # stranded
            if node == base:
                if energy >= self.LEAVE_ENERGY:
                    free.append(unit)
                continue
            if energy - DRAIN < 2 * home[node] - 1:
                moves.append([uid, self._hop(node, base)])  # must head home now
            elif target[node] > 0:
                target[node] -= 1  # stays and serves
            else:
                free.append(unit)

        # Fill the remaining targets with the closest free rovers.
        pairs = sorted(
            (dist[u["node"]][v], u["id"], v) for u in free for v in range(n) if target[v] > 0
        )
        by_id = {u["id"]: u for u in free}
        used: set[int] = set()
        for _, uid, v in pairs:
            if uid in used or target[v] == 0:
                continue
            unit = by_id[uid]
            nxt = self._hop(unit["node"], v)
            if unit["energy"] - MOVE_COST - DRAIN < 2 * home[nxt] - 1:
                continue  # could not get back from there
            used.add(uid)
            target[v] -= 1
            moves.append([uid, nxt])
        for uid, unit in by_id.items():
            if uid not in used and unit["node"] != base:
                moves.append([uid, self._hop(unit["node"], base)])  # nothing to do: recharge
        return moves
