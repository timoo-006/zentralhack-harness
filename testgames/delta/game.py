"""
Delta Game: drones under fog of war, a fleet bought with the money it earns, noisy demand and
contested nodes that go mostly to the bigger fleet.

Differences to the dev games that a harness must adapt to: the opponent's drones are only
visible near your own drones and your base, drones are bought during the match (the action
is a dict with moves and a build order), each node's demand is noisy around a hidden mean,
a contested node is won winner-take-most instead of being split proportionally, and a match
has 120 turns. Everything random (map, bases, demand) is drawn from the seed when the game
is created, so every pairing that plays a seed faces exactly the same environment.
"""

import random
from dataclasses import dataclass
from typing import Any

from arena.core import Game

N_TURNS = 120
START_DRONES = 10  # per player, on its base
MAX_DRONES = 40  # most drones a player may own
DRONE_CAPACITY = 4
UPKEEP = 2.0  # per drone away from its own base, per turn
BUILD_COST = 15.0  # per drone bought
START_MONEY = 20.0
VISION_RANGE = 1  # you see the opponent's drones within this many edges of yours / your base

# map generator
N_NODES_RANGE = (12, 18)  # inclusive
EXTRA_EDGE_FRACTION = 0.3  # extra edges = round(EXTRA_EDGE_FRACTION * n_nodes)
MIN_BASE_DISTANCE = 4  # min shortest-path length (in edges) between the two bases

# demand
MEAN_DEMAND_RANGE = (1.0, 10.0)  # hidden per-node mean, uniform
DEMAND_NOISE = 0.5  # demand = round(mean * uniform(1 - DEMAND_NOISE, 1 + DEMAND_NOISE), 1)


@dataclass(frozen=True)
class Instance:
    """Everything a match's environment consists of, fixed at creation."""

    n_nodes: int
    edges: list[tuple[int, int]]  # undirected, u < v, sorted
    bases: list[int]  # index = player_id
    mean_demand: list[float]  # hidden; 0.0 on both bases
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
        return n, edge_list, [base_0, rng.choice(candidates)]


def generate_instance(seed: int) -> Instance:
    """The match environment for `seed`. Map and demand use independent RNG streams."""
    map_rng = random.Random(f"delta:{seed}:map")
    demand_rng = random.Random(f"delta:{seed}:demand")
    n_nodes, edges, bases = generate_map(map_rng)
    mean = [0.0 if v in bases else demand_rng.uniform(*MEAN_DEMAND_RANGE) for v in range(n_nodes)]
    demand = []
    for _ in range(N_TURNS):
        row = [0.0] * n_nodes
        for v in range(n_nodes):
            if v not in bases:
                noise = demand_rng.uniform(1.0 - DEMAND_NOISE, 1.0 + DEMAND_NOISE)
                row[v] = round(mean[v] * noise, 1)
        demand.append(row)
    return Instance(n_nodes, edges, bases, mean, demand)


def served(own: int, other: int, demand: float) -> float:
    """Demand served by `own` drones against `other` drones at a node (see spec.md).

    The side with more drones serves first, up to its capacity; the other side serves what
    is left, up to its capacity. With equal drone counts the demand is split in proportion
    to capacity, as in the dev games.
    """
    if own == 0 or demand <= 0:
        return 0.0
    cap_own, cap_other = own * DRONE_CAPACITY, other * DRONE_CAPACITY
    if own > other:
        return min(cap_own, demand)
    if own < other:
        return min(cap_own, demand - min(cap_other, demand))
    return cap_own * demand / max(cap_own + cap_other, demand)


class DeltaGame(Game):
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
        self.graph: list[list[int]] = [[] for _ in range(self.n_nodes)]
        for u, v in self.edges:
            self.graph[u].append(v)
            self.graph[v].append(u)
        self.dist = hop_distances(self.n_nodes, self.edges)

        self.drones = [[0] * self.n_nodes, [0] * self.n_nodes]
        for player_id, base in enumerate(self.bases):
            self.drones[player_id][base] = START_DRONES

        self.profit_last_turn = [0.0, 0.0]
        self.revenue_last_turn = [0.0, 0.0]
        self.upkeep_last_turn = [0.0, 0.0]
        self.built_last_turn = [0, 0]
        self.cumulative_profit = [0.0, 0.0]
        self.turn = 0

        self.n_invalid_actions_last_turn = [0, 0]
        self.action_format_correct_last_turn = [True, True]

    def money(self, player_id: int) -> float:
        return START_MONEY + self.cumulative_profit[player_id]

    def visible(self, player_id: int) -> list[bool]:
        """Nodes within VISION_RANGE edges of the player's base or of any of its drones."""
        sources = [self.bases[player_id]]
        sources += [v for v in range(self.n_nodes) if self.drones[player_id][v] > 0]
        return [
            any(0 <= self.dist[s][v] <= VISION_RANGE for s in sources) for v in range(self.n_nodes)
        ]

    def observe(self, player_id: int) -> dict:
        opponent_id = 1 - player_id
        seen = self.visible(player_id)
        return {
            "turn": self.turn,
            "n_nodes": self.n_nodes,
            "edges": list(self.edges),
            "player_base": self.bases[player_id],
            "opponent_base": self.bases[opponent_id],
            "demand": (
                list(self.demand[self.turn]) if self.turn < N_TURNS else [0.0] * self.n_nodes
            ),
            "player_drones": list(self.drones[player_id]),
            "opponent_drones": [
                self.drones[opponent_id][v] if seen[v] else None for v in range(self.n_nodes)
            ],
            "player_money": self.money(player_id),
            "player_profit_last_turn": self.profit_last_turn[player_id],
            "player_cumulative_profit": self.cumulative_profit[player_id],
            "n_invalid_actions_last_turn": self.n_invalid_actions_last_turn[player_id],
            "action_format_correct_last_turn": self.action_format_correct_last_turn[player_id],
        }

    def step(self, actions: list[Any]) -> None:
        if self.is_over():
            return
        self.n_invalid_actions_last_turn = [0, 0]
        self.built_last_turn = [0, 0]

        for player_id in range(2):
            action = actions[player_id] if len(actions) > player_id else None
            ok = self._check_action_format(action)
            self.action_format_correct_last_turn[player_id] = ok
            if not ok:
                continue  # malformed action: the whole turn is a no-op for this player

            # Only drones present at the start of the turn can move: one edge per turn.
            drones = self.drones[player_id]
            movable = list(drones)
            for from_node, to_node, count in action["moves"]:
                valid = (
                    0 <= from_node < self.n_nodes
                    and 0 <= to_node < self.n_nodes
                    and to_node in self.graph[from_node]
                    and 0 <= count <= movable[from_node]
                )
                if not valid:
                    self.n_invalid_actions_last_turn[player_id] += 1
                    continue
                movable[from_node] -= count
                drones[from_node] -= count
                drones[to_node] += count

            # Bought drones appear on the base after the moves: they move from next turn on.
            build = action["build"]
            if build != 0 and (
                build < 0
                or sum(drones) + build > MAX_DRONES  # checked first: bounds build
                or build * BUILD_COST > self.money(player_id)
            ):
                self.n_invalid_actions_last_turn[player_id] += 1
            else:
                drones[self.bases[player_id]] += build
                self.built_last_turn[player_id] = build

        self.revenue_last_turn = [0.0, 0.0]
        demand = self.demand[self.turn]
        for node in range(self.n_nodes):
            n0, n1 = self.drones[0][node], self.drones[1][node]
            self.revenue_last_turn[0] += served(n0, n1, demand[node])
            self.revenue_last_turn[1] += served(n1, n0, demand[node])

        self.upkeep_last_turn = [
            UPKEEP * (sum(self.drones[p]) - self.drones[p][self.bases[p]]) for p in range(2)
        ]
        self.profit_last_turn = [
            self.revenue_last_turn[p]
            - self.upkeep_last_turn[p]
            - BUILD_COST * self.built_last_turn[p]
            for p in range(2)
        ]
        for player_id in range(2):
            self.cumulative_profit[player_id] += self.profit_last_turn[player_id]

        self.turn += 1

    def is_over(self) -> bool:
        return self.turn >= self.N_TURNS

    def scores(self) -> list[float]:
        return list(self.cumulative_profit)

    @staticmethod
    def _check_action_format(action: Any) -> bool:
        """Shape only: {"moves": [3-int tuples], "build": int}. Legality is checked in step()."""
        if not isinstance(action, dict) or set(action) != {"moves", "build"}:
            return False
        moves, build = action["moves"], action["build"]
        if not isinstance(build, int) or isinstance(build, bool) or not isinstance(moves, list):
            return False
        for move in moves:
            if not isinstance(move, tuple) or len(move) != 3:
                return False
            if not all(isinstance(v, int) and not isinstance(v, bool) for v in move):
                return False
        return True
