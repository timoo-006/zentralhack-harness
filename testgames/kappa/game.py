"""
Kappa Game: winner-take-all nodes on a map whose edges are congested.

Differences to the dev games that a harness must adapt to: every edge has a capacity (1 to 4
units per player per turn, both directions together), and a move that exceeds the edge's
remaining capacity is illegal; node demand is constant and a node pays its whole demand to the
side with strictly more units there (a tie pays nobody); there is no upkeep, but every unit moved
costs MOVE_COST; the action is a dict {"moves": [[from, to, count], ...]} with list moves.
Everything random (map, capacities, bases, demand) is drawn from the seed when the game is
created, so every pairing that plays a seed faces exactly the same environment.
"""

import random
from dataclasses import dataclass
from typing import Any

from arena.core import Game

N_TURNS = 80
UNITS_PER_PLAYER = 15
MOVE_COST = 0.5  # per unit moved, per move order

# map generator
N_NODES_RANGE = (10, 14)  # inclusive
EXTRA_EDGE_FRACTION = 0.4  # extra edges = round(EXTRA_EDGE_FRACTION * n_nodes)
CAPACITY_RANGE = (1, 4)  # inclusive, uniform integer, drawn per edge
MIN_BASE_DISTANCE = 3  # min shortest-path length (in edges) between the two bases

# node demand
DEMAND_RANGE = (1, 6)  # inclusive, uniform integer, drawn per non-base node


@dataclass(frozen=True)
class Instance:
    """Everything a match's environment consists of, fixed at creation."""

    n_nodes: int
    edges: list[tuple[int, int]]  # undirected, u < v, sorted
    capacities: list[int]  # capacities[k] is the capacity of edges[k]
    bases: list[int]  # index = player_id
    demand: list[int]  # constant demand per node; 0 on both bases


def hop_distances(n_nodes: int, edges: list[tuple[int, int]]) -> list[list[int]]:
    """All-pairs shortest path lengths in edges (BFS from every node); -1 if unreachable."""
    neighbours: list[list[int]] = [[] for _ in range(n_nodes)]
    for u, v in edges:
        neighbours[u].append(v)
        neighbours[v].append(u)
    dist = []
    for source in range(n_nodes):
        row = [-1] * n_nodes
        row[source] = 0
        frontier = [source]
        while frontier:
            nxt = []
            for u in frontier:
                for v in neighbours[u]:
                    if row[v] == -1:
                        row[v] = row[u] + 1
                        nxt.append(v)
            frontier = nxt
        dist.append(row)
    return dist


def generate_instance(seed: int | None) -> Instance:
    rng = random.Random(seed)
    while True:
        n = rng.randint(*N_NODES_RANGE)
        order = list(range(n))
        rng.shuffle(order)
        edge_set: set[tuple[int, int]] = set()
        for k in range(1, n):
            a, b = order[k], order[rng.randrange(k)]
            edge_set.add((min(a, b), max(a, b)))
        n_extra = round(EXTRA_EDGE_FRACTION * n)
        while n_extra > 0:
            a, b = rng.sample(range(n), 2)
            e = (min(a, b), max(a, b))
            if e not in edge_set:
                edge_set.add(e)
                n_extra -= 1
        edges = sorted(edge_set)
        hops = hop_distances(n, edges)
        b0 = rng.randrange(n)
        far = [v for v in range(n) if hops[b0][v] >= MIN_BASE_DISTANCE]
        if not far:
            continue
        b1 = rng.choice(far)
        capacities = [rng.randint(*CAPACITY_RANGE) for _ in edges]
        demand = [0 if i in (b0, b1) else rng.randint(*DEMAND_RANGE) for i in range(n)]
        return Instance(n, edges, capacities, [b0, b1], demand)


def _parse(action: Any) -> list[list[int]] | None:
    """The list of moves of a well-formed action, or None if it is malformed."""
    if type(action) is not dict or len(action) != 1 or "moves" not in action:
        return None
    moves = action["moves"]
    if type(moves) is not list:
        return None
    for m in moves:
        if type(m) is not list or len(m) != 3 or any(type(x) is not int for x in m):
            return None
    return moves


class KappaGame(Game):
    n_players = 2
    N_TURNS = N_TURNS
    UNITS_PER_PLAYER = UNITS_PER_PLAYER

    def __init__(self, seed: int | None = None) -> None:
        super().__init__(seed)
        self.instance = generate_instance(seed)
        inst = self.instance
        self.edge_index = {e: k for k, e in enumerate(inst.edges)}
        self.turn = 0
        self.units = [[0] * inst.n_nodes for _ in range(2)]
        for p in range(2):
            self.units[p][inst.bases[p]] = UNITS_PER_PLAYER
        self.profit_last = [0.0, 0.0]
        self.cumulative = [0.0, 0.0]
        self.invalid_last = [0, 0]
        self.format_ok_last = [True, True]

    def is_over(self) -> bool:
        return self.turn >= N_TURNS

    def scores(self) -> list[float]:
        return list(self.cumulative)

    def observe(self, player_id: int) -> dict:
        inst = self.instance
        me, op = player_id, 1 - player_id
        return {
            "turn": self.turn,
            "n_nodes": inst.n_nodes,
            "edges": list(inst.edges),
            "edge_capacity": list(inst.capacities),
            "player_base": inst.bases[me],
            "opponent_base": inst.bases[op],
            "demand": list(inst.demand),
            "player_units": list(self.units[me]),
            "opponent_units": list(self.units[op]),
            "player_profit_last_turn": self.profit_last[me],
            "opponent_profit_last_turn": self.profit_last[op],
            "player_cumulative_profit": self.cumulative[me],
            "opponent_cumulative_profit": self.cumulative[op],
            "n_invalid_actions_last_turn": self.invalid_last[me],
            "action_format_correct_last_turn": self.format_ok_last[me],
        }

    def _apply(self, p: int, action: Any) -> tuple[float, int, bool]:
        """Apply player p's action; returns (move cost, illegal moves, well-formed)."""
        moves = _parse(action)
        if moves is None:
            return 0.0, 0, False
        n = self.instance.n_nodes
        start = self.units[p]
        available = list(start)  # units that were at each node at the start of the turn
        new = list(start)
        remaining = list(self.instance.capacities)
        cost = 0.0
        invalid = 0
        for a, b, count in moves:
            if not (0 <= a < n and 0 <= b < n):
                invalid += 1
                continue
            k = self.edge_index.get((min(a, b), max(a, b)))
            if k is None or count < 0 or count > available[a] or count > remaining[k]:
                invalid += 1
                continue
            available[a] -= count
            remaining[k] -= count
            new[a] -= count
            new[b] += count
            cost += MOVE_COST * count
        self.units[p] = new
        return cost, invalid, True

    def step(self, actions: list[Any]) -> None:
        if self.is_over():
            return
        if type(actions) is not list:
            actions = []
        actions = list(actions) + [None] * (2 - len(actions))
        costs = [0.0, 0.0]
        for p in range(2):
            costs[p], self.invalid_last[p], self.format_ok_last[p] = self._apply(p, actions[p])
        revenue = [0.0, 0.0]
        for i, d in enumerate(self.instance.demand):
            u0, u1 = self.units[0][i], self.units[1][i]
            if d > 0 and u0 > u1:
                revenue[0] += d
            elif d > 0 and u1 > u0:
                revenue[1] += d
        for p in range(2):
            self.profit_last[p] = revenue[p] - costs[p]
            self.cumulative[p] += self.profit_last[p]
        self.turn += 1
