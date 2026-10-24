"""
Theta Game: two unit types, hidden demand, random surges and a "bigger side serves first" split.

Everything random (map, bases, base demand, surges) is drawn from the seed when the game is
created, so every pairing that plays a seed faces exactly the same environment.
"""

import math
import random
from dataclasses import dataclass
from typing import Any

from arena.core import Game

N_TURNS = 70
UNIT_TYPES = ("heavy", "light")
UNITS_PER_PLAYER = {"heavy": 10, "light": 20}
UNIT_CAPACITY = {"heavy": 8, "light": 3}
UNIT_UPKEEP = {"heavy": 3.0, "light": 1.0}  # per unit away from its own base, per turn

# map generator (see "Map")
N_NODES_RANGE = (12, 16)  # inclusive
EXTRA_EDGE_FRACTION = 0.4
MIN_BASE_DISTANCE = 3

# demand (see "Demand")
BASE_DEMAND_MEDIAN = 3.0
BASE_DEMAND_SIGMA = 0.7
BASE_DEMAND_CLIP = (0.5, 20.0)
SURGE_PROBABILITY = 0.1  # per node and turn
SURGE_MULTIPLIER = 3
SURGE_DURATION = 3  # turns, including the turn it starts


@dataclass(frozen=True)
class Instance:
    """Everything a match's environment consists of, fixed at creation."""

    n_nodes: int
    edges: list[tuple[int, int]]  # undirected, u < v, sorted
    bases: list[int]  # index = player_id
    base_demand: list[float]  # per node; 0.0 on both bases
    surge_left: list[list[int]]  # surge_left[turn][node]: turns the surge still lasts, 0 = none
    demand: list[list[float]]  # demand[turn][node]


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


def generate_map(rng: random.Random) -> tuple[int, list[tuple[int, int]], list[int]]:
    """Random connected graph plus two bases at least MIN_BASE_DISTANCE apart."""
    while True:
        n = rng.randint(*N_NODES_RANGE)
        perm = list(range(n))
        rng.shuffle(perm)
        edges: set[tuple[int, int]] = set()
        for k in range(1, n):
            u, v = perm[k], perm[rng.randrange(k)]
            edges.add((min(u, v), max(u, v)))
        n_extra = round(EXTRA_EDGE_FRACTION * n)
        while n_extra > 0:
            u, v = rng.sample(range(n), 2)
            edge = (min(u, v), max(u, v))
            if edge not in edges:
                edges.add(edge)
                n_extra -= 1
        edge_list = sorted(edges)
        dist = hop_distances(n, edge_list)
        base_0 = rng.randrange(n)
        candidates = [v for v in range(n) if dist[base_0][v] >= MIN_BASE_DISTANCE]
        if not candidates:
            continue
        base_1 = rng.choice(candidates)
        return n, edge_list, [base_0, base_1]


def generate_demand(
    rng: random.Random, n_nodes: int, bases: list[int]
) -> tuple[list[float], list[list[int]], list[list[float]]]:
    """Base demand per node, then independent surge starts per node and turn."""
    lo, hi = BASE_DEMAND_CLIP
    base_demand = [0.0] * n_nodes
    for v in range(n_nodes):
        if v in bases:
            continue
        raw = BASE_DEMAND_MEDIAN * math.exp(BASE_DEMAND_SIGMA * rng.gauss(0.0, 1.0))
        base_demand[v] = round(min(max(raw, lo), hi), 1)

    left = [0] * n_nodes
    surge_left: list[list[int]] = []
    demand: list[list[float]] = []
    for _turn in range(N_TURNS):
        for v in range(n_nodes):
            left[v] = max(left[v] - 1, 0)
            if v in bases:
                continue
            if rng.random() < SURGE_PROBABILITY:
                left[v] = SURGE_DURATION  # a new surge restarts the count; surges never stack
        surge_left.append(list(left))
        demand.append(
            [
                round(base_demand[v] * SURGE_MULTIPLIER, 1) if left[v] > 0 else base_demand[v]
                for v in range(n_nodes)
            ]
        )
    return base_demand, surge_left, demand


def generate_instance(seed: int) -> Instance:
    """The match environment for `seed`. Map and demand use independent RNG streams."""
    map_rng = random.Random(f"theta:{seed}:map")
    demand_rng = random.Random(f"theta:{seed}:demand")
    n_nodes, edges, bases = generate_map(map_rng)
    base_demand, surge_left, demand = generate_demand(demand_rng, n_nodes, bases)
    return Instance(n_nodes, edges, bases, base_demand, surge_left, demand)


def split_demand(demand: float, capacity_0: float, capacity_1: float) -> tuple[float, float]:
    """The side with more capacity serves first; equal capacity splits the demand evenly."""
    if demand <= 0:
        return 0.0, 0.0
    if capacity_0 == capacity_1:
        share = min(capacity_0, demand / 2)
        return share, share
    if capacity_0 > capacity_1:
        first = min(capacity_0, demand)
        return first, min(capacity_1, demand - first)
    first = min(capacity_1, demand)
    return min(capacity_0, demand - first), first


class ThetaGame(Game):
    N_TURNS = N_TURNS

    def __init__(self, seed: int | None = None, instance: Instance | None = None) -> None:
        """Create the match for `seed`; `instance` overrides the generator (for tests)."""
        super().__init__(seed)
        if instance is None:
            if seed is None:
                seed = random.SystemRandom().randrange(2**31)
            instance = generate_instance(seed)
        self.instance = instance
        self.n_nodes = instance.n_nodes
        self.edges = list(instance.edges)
        self.bases = list(instance.bases)
        self.demand = instance.demand
        self.surge_left = instance.surge_left
        self.graph: list[list[int]] = [[] for _ in range(self.n_nodes)]
        for u, v in self.edges:
            self.graph[u].append(v)
            self.graph[v].append(u)

        # units[player_id][unit_type][node]
        self.units: list[dict[str, list[int]]] = []
        for base in self.bases:
            fleet = {}
            for unit_type in UNIT_TYPES:
                row = [0] * self.n_nodes
                row[base] = UNITS_PER_PLAYER[unit_type]
                fleet[unit_type] = row
            self.units.append(fleet)

        self.profit_last_turn = [0.0, 0.0]
        self.cumulative_profit = [0.0, 0.0]
        self.turn = 0
        self.n_invalid_actions_last_turn = [0, 0]
        self.action_format_correct_last_turn = [True, True]

    def visible(self, player_id: int) -> list[bool]:
        """Nodes with at least one of the player's units, and their neighbours."""
        fleet = self.units[player_id]
        occupied = [any(fleet[t][v] > 0 for t in UNIT_TYPES) for v in range(self.n_nodes)]
        return [
            occupied[v] or any(occupied[u] for u in self.graph[v]) for v in range(self.n_nodes)
        ]

    def observe(self, player_id: int) -> dict:
        opponent_id = 1 - player_id
        turn = min(self.turn, self.N_TURNS - 1)  # after the end, show the last turn's demand
        seen = self.visible(player_id)
        mine, theirs = self.units[player_id], self.units[opponent_id]
        return {
            "turn": self.turn,
            "n_nodes": self.n_nodes,
            "edges": list(self.edges),
            "player_base": self.bases[player_id],
            "opponent_base": self.bases[opponent_id],
            "demand": [self.demand[turn][v] if seen[v] else None for v in range(self.n_nodes)],
            "surge_turns_left": [
                self.surge_left[turn][v] if seen[v] else None for v in range(self.n_nodes)
            ],
            "player_heavy": list(mine["heavy"]),
            "player_light": list(mine["light"]),
            "opponent_heavy": list(theirs["heavy"]),
            "opponent_light": list(theirs["light"]),
            "player_profit_last_turn": self.profit_last_turn[player_id],
            "opponent_profit_last_turn": self.profit_last_turn[opponent_id],
            "player_cumulative_profit": self.cumulative_profit[player_id],
            "opponent_cumulative_profit": self.cumulative_profit[opponent_id],
            "n_invalid_actions_last_turn": self.n_invalid_actions_last_turn[player_id],
            "action_format_correct_last_turn": self.action_format_correct_last_turn[player_id],
        }

    def step(self, actions: list[Any]) -> None:
        if self.is_over():
            return
        self.n_invalid_actions_last_turn = [0, 0]
        for player_id in range(2):
            action = actions[player_id] if player_id < len(actions) else None
            ok = self._check_action_format(action)
            self.action_format_correct_last_turn[player_id] = ok
            if not ok:
                continue
            fleet = self.units[player_id]
            movable = {t: list(fleet[t]) for t in UNIT_TYPES}
            for from_node, to_node, unit_type, count in action:
                valid = (
                    0 <= from_node < self.n_nodes
                    and 0 <= to_node < self.n_nodes
                    and to_node in self.graph[from_node]
                    and 0 <= count <= movable[unit_type][from_node]
                )
                if not valid:
                    self.n_invalid_actions_last_turn[player_id] += 1
                    continue
                movable[unit_type][from_node] -= count
                fleet[unit_type][from_node] -= count
                fleet[unit_type][to_node] += count

        revenue = self._revenue()
        upkeep = [self._upkeep(p) for p in range(2)]
        self.profit_last_turn = [revenue[p] - upkeep[p] for p in range(2)]
        for p in range(2):
            self.cumulative_profit[p] += self.profit_last_turn[p]
        self.turn += 1

    def is_over(self) -> bool:
        return self.turn >= self.N_TURNS

    def scores(self) -> list[float]:
        return list(self.cumulative_profit)

    def capacity(self, player_id: int, node: int) -> int:
        fleet = self.units[player_id]
        return sum(fleet[t][node] * UNIT_CAPACITY[t] for t in UNIT_TYPES)

    def _upkeep(self, player_id: int) -> float:
        base = self.bases[player_id]
        fleet = self.units[player_id]
        return sum(
            UNIT_UPKEEP[t] * (UNITS_PER_PLAYER[t] - fleet[t][base]) for t in UNIT_TYPES
        )

    def _revenue(self) -> list[float]:
        demand = self.demand[self.turn]
        revenue = [0.0, 0.0]
        for node in range(self.n_nodes):
            served = split_demand(demand[node], self.capacity(0, node), self.capacity(1, node))
            revenue[0] += served[0]
            revenue[1] += served[1]
        return revenue

    @staticmethod
    def _check_action_format(action: Any) -> bool:
        """Shape only: a list of (int, int, "heavy" | "light", int) tuples."""
        if not isinstance(action, list):
            return False
        for move in action:
            if not isinstance(move, tuple) or len(move) != 4:
                return False
            from_node, to_node, unit_type, count = move
            if not all(
                isinstance(v, int) and not isinstance(v, bool) for v in (from_node, to_node, count)
            ):
                return False
            if not isinstance(unit_type, str) or unit_type not in UNIT_TYPES:
                return False
        return True
