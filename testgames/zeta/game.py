"""
Zeta Game: beta's courier game with renamed observation keys, a turn-major order forecast,
a nested-dict action format, a per-node serving limit and upkeep that grows with the distance
from the own base.

Everything random (map, bases, orders) is drawn from the seed when the game is created, so
every pairing that plays a seed faces exactly the same environment.
"""

import math
import random
from dataclasses import dataclass
from typing import Any

from arena.core import Game

N_TURNS = 90
UNITS_PER_PLAYER = 24
UNIT_CAPACITY = 4
SERVE_LIMIT = 6  # at most this many units of one player serve at one node
UPKEEP_BASE = 1.0  # per unit away from its own base, per turn ...
UPKEEP_PER_HOP = 0.5  # ... plus this much per link of shortest-path distance from that base
FORECAST_HORIZON = 3  # the forecast covers turns t .. t + FORECAST_HORIZON - 1

# map generator
N_NODES_RANGE = (12, 18)  # inclusive
EXTRA_EDGE_FRACTION = 0.35  # extra links = round(EXTRA_EDGE_FRACTION * n_nodes)
MIN_BASE_DISTANCE = 3  # min shortest-path length (in links) between the two bases

# total order curve
D_START_RANGE = (35.0, 55.0)
D_END_RANGE = (90.0, 120.0)
CURVE_SHAPES = ("linear", "early", "late")

# per-node shares
HUB_WEIGHT_SIGMA = 0.9
DRIFT_PHI = 0.92
DRIFT_SIGMA = 0.3


@dataclass(frozen=True)
class Curve:
    """The total order curve D(t) of one match."""

    d_start: float
    d_end: float
    shape: str

    def s(self, x: float) -> float:
        if self.shape == "linear":
            return x
        if self.shape == "early":
            return 1.0 - (1.0 - x) ** 2
        if self.shape == "late":
            return x**2
        raise ValueError(f"unknown curve shape {self.shape!r}")

    def total(self, turn: int) -> float:
        return self.d_start + (self.d_end - self.d_start) * self.s(turn / (N_TURNS - 1))


@dataclass(frozen=True)
class Instance:
    """Everything a match's environment consists of, fixed at creation."""

    n_nodes: int
    edges: list[tuple[int, int]]  # undirected, u < v, sorted
    bases: list[int]  # index = player_id
    orders: list[list[float]]  # orders[turn][node]
    curve: Curve | None = None


def hop_distances(n_nodes: int, edges: list[tuple[int, int]]) -> list[list[int]]:
    """All-pairs shortest path lengths in links (BFS from every node); -1 if unreachable."""
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


def generate_curve(rng: random.Random) -> Curve:
    d_start = rng.uniform(*D_START_RANGE)
    d_end = rng.uniform(*D_END_RANGE)
    return Curve(d_start, d_end, rng.choice(CURVE_SHAPES))


def generate_orders(
    rng: random.Random, n_nodes: int, bases: list[int], curve: Curve
) -> list[list[float]]:
    """orders[t][node]: the total curve split by drifting per-node weights, 1 decimal."""
    nodes = [v for v in range(n_nodes) if v not in bases]
    hub = {v: math.exp(HUB_WEIGHT_SIGMA * rng.gauss(0.0, 1.0)) for v in nodes}
    drift = dict.fromkeys(nodes, 0.0)
    orders = []
    for turn in range(N_TURNS):
        weight = {v: hub[v] * math.exp(drift[v]) for v in nodes}
        total_weight = sum(weight.values())
        total = curve.total(turn)
        row = [0.0] * n_nodes
        for v in nodes:
            row[v] = round(total * weight[v] / total_weight, 1)
        orders.append(row)
        for v in nodes:
            drift[v] = DRIFT_PHI * drift[v] + DRIFT_SIGMA * rng.gauss(0.0, 1.0)
    return orders


def generate_instance(seed: int) -> Instance:
    """The match environment for `seed`. Map and orders use independent RNG streams."""
    map_rng = random.Random(f"zeta:{seed}:map")
    order_rng = random.Random(f"zeta:{seed}:orders")
    n_nodes, edges, bases = generate_map(map_rng)
    curve = generate_curve(order_rng)
    orders = generate_orders(order_rng, n_nodes, bases, curve)
    return Instance(n_nodes, edges, bases, orders, curve)


def _plain_int(value: Any) -> bool:
    """Exactly `int`: bool, numpy integers and other int subclasses are malformed."""
    return type(value) is int


class ZetaGame(Game):
    N_TURNS = N_TURNS
    UNITS_PER_PLAYER = UNITS_PER_PLAYER
    UNIT_CAPACITY = UNIT_CAPACITY
    SERVE_LIMIT = SERVE_LIMIT
    FORECAST_HORIZON = FORECAST_HORIZON

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
        self.orders = instance.orders
        self.graph: list[set[int]] = [set() for _ in range(self.n_nodes)]
        for u, v in self.edges:
            self.graph[u].add(v)
            self.graph[v].add(u)
        dist = hop_distances(self.n_nodes, self.edges)
        # upkeep_rate[p][node]: what one unit of player p costs per turn at that node.
        self.upkeep_rate = [
            [
                0.0 if node == base else UPKEEP_BASE + UPKEEP_PER_HOP * dist[base][node]
                for node in range(self.n_nodes)
            ]
            for base in self.bases
        ]
        self.units = [[0] * self.n_nodes, [0] * self.n_nodes]
        for player_id, base in enumerate(self.bases):
            self.units[player_id][base] = UNITS_PER_PLAYER
        self.last_profit = [0.0, 0.0]
        self.score = [0.0, 0.0]
        self.format_ok = [True, True]
        self.rejected = [0, 0]
        self.turn = 0

    def observe(self, player_id: int) -> dict:
        rival_id = 1 - player_id
        start = min(self.turn, N_TURNS)
        horizon = max(0, min(FORECAST_HORIZON, N_TURNS - start))
        return {
            "turn": self.turn,
            "graph": {"nodes": self.n_nodes, "links": [[u, v] for u, v in self.edges]},
            "you": self._side(player_id),
            "rival": self._side(rival_id),
            "orders": [list(row) for row in self.orders[start : start + horizon]],
            "feedback": {
                "format_ok": self.format_ok[player_id],
                "rejected": self.rejected[player_id],
            },
        }

    def _side(self, player_id: int) -> dict:
        return {
            "base": self.bases[player_id],
            "units": list(self.units[player_id]),
            "score": self.score[player_id],
            "last_profit": self.last_profit[player_id],
        }

    def step(self, actions: list[Any]) -> None:
        if self.is_over():
            return
        self.rejected = [0, 0]
        for player_id in range(2):
            action = actions[player_id] if player_id < len(actions) else None
            moves = self._parse_action(action)
            self.format_ok[player_id] = moves is not None
            if moves is None:
                continue  # malformed action: the whole turn is a no-op for this player
            # Only units present at the start of the turn can move: one link per turn.
            movable = list(self.units[player_id])
            for from_node, to_node, count in moves:
                valid = (
                    0 <= from_node < self.n_nodes
                    and 0 <= to_node < self.n_nodes
                    and to_node in self.graph[from_node]
                    and 0 <= count <= movable[from_node]
                )
                if not valid:
                    self.rejected[player_id] += 1
                    continue
                movable[from_node] -= count
                self.units[player_id][from_node] -= count
                self.units[player_id][to_node] += count

        revenue = self._revenue()
        for player_id in range(2):
            upkeep = sum(
                n * rate
                for n, rate in zip(self.units[player_id], self.upkeep_rate[player_id], strict=True)
            )
            self.last_profit[player_id] = revenue[player_id] - upkeep
            self.score[player_id] += self.last_profit[player_id]
        self.turn += 1

    def is_over(self) -> bool:
        return self.turn >= N_TURNS

    def scores(self) -> list[float]:
        return list(self.score)

    def _revenue(self) -> list[float]:
        """served_p = cap_p * orders / max(cap_0 + cap_1, orders), cap_p = min(n, 6) * 4."""
        orders = self.orders[self.turn]
        revenue = [0.0, 0.0]
        for node in range(self.n_nodes):
            capacity = [min(self.units[p][node], SERVE_LIMIT) * UNIT_CAPACITY for p in range(2)]
            total_capacity = sum(capacity)
            if orders[node] == 0 or total_capacity == 0:
                continue
            for p in range(2):
                revenue[p] += capacity[p] * orders[node] / max(total_capacity, orders[node])
        return revenue

    @staticmethod
    def _parse_action(action: Any) -> list[tuple[int, int, int]] | None:
        """Shape only: exactly {int: {int: int}} (no subclasses). Returns the moves in dict
        order, or None if the action is malformed. Legality is checked in step()."""
        if type(action) is not dict:
            return None
        moves = []
        for from_node, targets in action.items():
            if not _plain_int(from_node) or type(targets) is not dict:
                return None
            for to_node, count in targets.items():
                if not _plain_int(to_node) or not _plain_int(count):
                    return None
                moves.append((from_node, to_node, count))
        return moves
