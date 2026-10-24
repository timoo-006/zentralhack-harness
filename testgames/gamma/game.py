"""
Gamma Game: couriers of two types, moved one by one, harvesting node stock that grows back.

Differences to the dev games that a harness must adapt to: units are addressed by id and the
action is a dict, there are two unit types (capacity, running cost and speed differ), node
demand is a stock that service uses up and that regenerates at a node-specific rate, and a
match has 60 turns. Everything random (map, bases, node parameters) is drawn from the seed
when the game is created, so every pairing that plays a seed faces exactly the same map.
"""

import random
from dataclasses import dataclass
from typing import Any

from arena.core import Game

N_TURNS = 60
UNIT_TYPES = {
    "truck": {"capacity": 8, "cost": 3.0, "speed": 1},
    "scooter": {"capacity": 3, "cost": 1.5, "speed": 2},
}
FLEET = {"truck": 4, "scooter": 8}  # units per player, all start on the player's base
UNIT_ID_PREFIX = {"truck": "T", "scooter": "S"}  # unit ids: "T0".."T3", "S0".."S7"

# map generator
N_NODES_RANGE = (9, 13)  # inclusive
N_CHORDS = 3  # extra edges on top of the ring
MIN_BASE_DISTANCE = 3  # min shortest-path length (in edges) between the two bases

# node stock
STOCK_MAX_RANGE = (10, 35)  # integers, inclusive
REGEN_RANGE = (0.5, 3.5)  # uniform, rounded to 1 decimal
STOCK_DECIMALS = 2  # stock is rounded to this many decimals after every turn


@dataclass(frozen=True)
class Instance:
    """Everything a match's environment consists of, fixed at creation."""

    n_nodes: int
    edges: list[tuple[int, int]]  # undirected, u < v, sorted
    bases: list[int]  # index = player_id
    stock_max: list[float]  # index = node id; 0.0 on both bases
    regen: list[float]  # index = node id; 0.0 on both bases


def unit_ids() -> list[tuple[str, str]]:
    """(unit id, unit type) of one player's fleet, in observation order."""
    return [
        (f"{UNIT_ID_PREFIX[kind]}{k}", kind) for kind, count in FLEET.items() for k in range(count)
    ]


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
    """A ring with random chords, and two bases on opposite sides of the ring."""
    while True:
        n = rng.randint(*N_NODES_RANGE)
        edges = {(min(i, (i + 1) % n), max(i, (i + 1) % n)) for i in range(n)}
        n_chords = N_CHORDS
        while n_chords > 0:
            u, v = rng.sample(range(n), 2)
            edge = (min(u, v), max(u, v))
            if edge not in edges:
                edges.add(edge)
                n_chords -= 1
        edge_list = sorted(edges)
        base_0 = rng.randrange(n)
        base_1 = (base_0 + n // 2) % n
        if hop_distances(n, edge_list)[base_0][base_1] >= MIN_BASE_DISTANCE:
            return n, edge_list, [base_0, base_1]


def generate_instance(seed: int) -> Instance:
    """The match environment for `seed`. Map and node parameters use independent streams."""
    map_rng = random.Random(f"gamma:{seed}:map")
    node_rng = random.Random(f"gamma:{seed}:nodes")
    n_nodes, edges, bases = generate_map(map_rng)
    stock_max = [0.0] * n_nodes
    regen = [0.0] * n_nodes
    for node in range(n_nodes):
        if node in bases:
            continue
        stock_max[node] = float(node_rng.randint(*STOCK_MAX_RANGE))
        regen[node] = round(node_rng.uniform(*REGEN_RANGE), 1)
    return Instance(n_nodes, edges, bases, stock_max, regen)


class GammaGame(Game):
    N_TURNS = N_TURNS
    UNIT_TYPES = UNIT_TYPES
    FLEET = FLEET

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
        self.stock_max = list(instance.stock_max)
        self.regen = list(instance.regen)
        self.dist = hop_distances(self.n_nodes, self.edges)

        self.stock = list(self.stock_max)  # every node starts full
        self.unit_type = dict(unit_ids())
        # positions[player_id][unit_id] = node
        self.positions = [dict.fromkeys(self.unit_type, base) for base in self.bases]

        self.profit_last_turn = [0.0, 0.0]
        self.revenue_last_turn = [0.0, 0.0]
        self.cost_last_turn = [0.0, 0.0]
        self.cumulative_profit = [0.0, 0.0]
        self.turn = 0

        self.n_invalid_actions_last_turn = [0, 0]
        self.action_format_correct_last_turn = [True, True]

    def observe(self, player_id: int) -> dict:
        opponent_id = 1 - player_id
        return {
            "turn": self.turn,
            "n_nodes": self.n_nodes,
            "edges": list(self.edges),
            "player_base": self.bases[player_id],
            "opponent_base": self.bases[opponent_id],
            "stock": list(self.stock),
            "stock_max": list(self.stock_max),
            "regen": list(self.regen),
            "player_units": self._units(player_id),
            "opponent_units": self._units(opponent_id),
            "player_profit_last_turn": self.profit_last_turn[player_id],
            "opponent_profit_last_turn": self.profit_last_turn[opponent_id],
            "player_cumulative_profit": self.cumulative_profit[player_id],
            "opponent_cumulative_profit": self.cumulative_profit[opponent_id],
            "n_invalid_actions_last_turn": self.n_invalid_actions_last_turn[player_id],
            "action_format_correct_last_turn": self.action_format_correct_last_turn[player_id],
        }

    def _units(self, player_id: int) -> dict[str, tuple[str, int]]:
        return {uid: (self.unit_type[uid], node) for uid, node in self.positions[player_id].items()}

    def step(self, actions: list[Any]) -> None:
        if self.is_over():
            return
        self.n_invalid_actions_last_turn = [0, 0]

        for player_id in range(2):
            action = actions[player_id] if len(actions) > player_id else None
            ok = self._check_action_format(action)
            self.action_format_correct_last_turn[player_id] = ok
            if not ok:
                continue  # malformed action: the whole turn is a no-op for this player

            # Every unit appears at most once (dict keys), so moves cannot interact.
            positions = self.positions[player_id]
            for unit_id, to_node in action["moves"].items():
                valid = (
                    unit_id in positions
                    and 0 <= to_node < self.n_nodes
                    and self.dist[positions[unit_id]][to_node]
                    <= UNIT_TYPES[self.unit_type[unit_id]]["speed"]
                )
                if not valid:
                    self.n_invalid_actions_last_turn[player_id] += 1
                    continue
                positions[unit_id] = to_node

        capacity = self.capacity()
        served = [[0.0] * self.n_nodes, [0.0] * self.n_nodes]
        for node in range(self.n_nodes):
            stock = self.stock[node]
            total_capacity = capacity[0][node] + capacity[1][node]
            if stock <= 0 or total_capacity == 0:
                continue
            for player_id in range(2):
                served[player_id][node] = (
                    capacity[player_id][node] * stock / max(total_capacity, stock)
                )

        self.revenue_last_turn = [sum(served[0]), sum(served[1])]
        self.cost_last_turn = self.running_cost()
        self.profit_last_turn = [
            r - c for r, c in zip(self.revenue_last_turn, self.cost_last_turn, strict=True)
        ]
        for player_id in range(2):
            self.cumulative_profit[player_id] += self.profit_last_turn[player_id]

        for node in range(self.n_nodes):
            if node in self.bases:
                continue
            self.stock[node] = round(
                min(
                    self.stock_max[node],
                    self.stock[node] - served[0][node] - served[1][node] + self.regen[node],
                ),
                STOCK_DECIMALS,
            )

        self.turn += 1

    def is_over(self) -> bool:
        return self.turn >= self.N_TURNS

    def scores(self) -> list[float]:
        return list(self.cumulative_profit)

    def capacity(self) -> list[list[int]]:
        """capacity[player_id][node]: total capacity of that player's units on that node."""
        capacity = [[0] * self.n_nodes, [0] * self.n_nodes]
        for player_id in range(2):
            for unit_id, node in self.positions[player_id].items():
                capacity[player_id][node] += UNIT_TYPES[self.unit_type[unit_id]]["capacity"]
        return capacity

    def running_cost(self) -> list[float]:
        """Per player: the cost of its units that are not on its own base."""
        return [
            sum(
                UNIT_TYPES[self.unit_type[unit_id]]["cost"]
                for unit_id, node in self.positions[player_id].items()
                if node != base
            )
            for player_id, base in enumerate(self.bases)
        ]

    @staticmethod
    def _check_action_format(action: Any) -> bool:
        """Shape only: {"moves": {str: int}}. Legality of each move is checked in step()."""
        if not isinstance(action, dict) or set(action) != {"moves"}:
            return False
        moves = action["moves"]
        if not isinstance(moves, dict):
            return False
        return all(
            isinstance(unit_id, str) and isinstance(node, int) and not isinstance(node, bool)
            for unit_id, node in moves.items()
        )
