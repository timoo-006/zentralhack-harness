"""
Iota Game: delivery orders with deadlines on a graph.

Differences to the dev games that a harness must adapt to: there is no steady demand. Orders
appear at random nodes, each with a reward and a deadline (turns left). At the end of a turn,
every open order is fulfilled by the player with more units on its node (a tie of at least one
unit each splits the reward); an order nobody fulfils before its deadline expires. Units cost
upkeep whenever they are away from their own base. Everything random (map, bases, every order
of every turn) is drawn from the seed when the game is created, so every pairing that plays a
seed faces exactly the same environment.
"""

import random
from dataclasses import dataclass
from typing import Any

from arena.core import Game

N_TURNS = 60
UNITS_PER_PLAYER = 12
UPKEEP = 1.0  # per unit away from its own base, per turn

# map generator
N_NODES_RANGE = (9, 13)  # inclusive
EXTRA_EDGE_FRACTION = 0.4  # extra edges = round(EXTRA_EDGE_FRACTION * n_nodes)
MIN_BASE_DISTANCE = 3  # min shortest-path length (in edges) between the two bases

# order generator
NEW_ORDERS_RANGE = (0, 2)  # inclusive, uniform integer: new orders per turn
REWARD_RANGE = (3, 12)  # inclusive, uniform integer
DEADLINE_RANGE = (2, 6)  # inclusive, uniform integer: turns_left when the order appears


@dataclass(frozen=True)
class Instance:
    """Everything a match's environment consists of, fixed at creation."""

    n_nodes: int
    edges: list[tuple[int, int]]  # undirected, u < v, sorted
    bases: list[int]  # index = player_id
    # orders[t] = the orders that appear on turn t, as (node, reward, deadline)
    orders: list[list[tuple[int, int, int]]]


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
    """Random connected graph and two bases at least MIN_BASE_DISTANCE apart."""
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


def generate_instance(seed: int) -> Instance:
    """The match environment for `seed`. Map and orders use independent RNG streams."""
    map_rng = random.Random(f"iota:{seed}:map")
    order_rng = random.Random(f"iota:{seed}:orders")
    n_nodes, edges, bases = generate_map(map_rng)
    sites = [v for v in range(n_nodes) if v not in bases]
    orders = []
    for _ in range(N_TURNS):
        k = order_rng.randint(*NEW_ORDERS_RANGE)
        orders.append(
            [
                (
                    order_rng.choice(sites),
                    order_rng.randint(*REWARD_RANGE),
                    order_rng.randint(*DEADLINE_RANGE),
                )
                for _ in range(k)
            ]
        )
    return Instance(n_nodes, edges, bases, orders)


class IotaGame(Game):
    N_TURNS = N_TURNS
    UNITS_PER_PLAYER = UNITS_PER_PLAYER
    UPKEEP = UPKEEP

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
        self.adjacent = set(self.edges) | {(v, u) for u, v in self.edges}
        self.bases = list(instance.bases)

        self.units = [[0] * self.n_nodes, [0] * self.n_nodes]
        for player_id, base in enumerate(self.bases):
            self.units[player_id][base] = self.UNITS_PER_PLAYER
        # Open orders as [node, reward, turns_left], in the order they appeared.
        self.open_orders: list[list[int]] = [list(o) for o in instance.orders[0]]

        self.profit_last_turn = [0.0, 0.0]
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
            "orders": sorted((n, r, t) for n, r, t in self.open_orders),
            "player_units": list(self.units[player_id]),
            "opponent_units": list(self.units[opponent_id]),
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
        if not isinstance(actions, (list, tuple)):
            actions = []

        # 1. Moves: each unit moves at most one edge; only units present at the start move.
        for player_id in range(2):
            action = actions[player_id] if player_id < len(actions) else None
            ok = self._check_action_format(action)
            self.action_format_correct_last_turn[player_id] = ok
            if not ok:
                continue
            movable = list(self.units[player_id])
            for from_node, to_node, count in action:
                valid = (
                    0 <= from_node < self.n_nodes
                    and 0 <= to_node < self.n_nodes
                    and (from_node, to_node) in self.adjacent
                    and 0 <= count <= movable[from_node]
                )
                if not valid:
                    self.n_invalid_actions_last_turn[player_id] += 1
                    continue
                movable[from_node] -= count
                self.units[player_id][from_node] -= count
                self.units[player_id][to_node] += count

        # 2. Orders: fulfilled by the strict majority on the node; a tie (>= 1 each) splits.
        revenue = [0.0, 0.0]
        still_open = []
        for order in self.open_orders:
            node, reward, _ = order
            a, b = self.units[0][node], self.units[1][node]
            if a > b:
                revenue[0] += reward
            elif b > a:
                revenue[1] += reward
            elif a > 0:
                revenue[0] += reward / 2
                revenue[1] += reward / 2
            else:
                still_open.append(order)

        # 3. Profit: revenue minus upkeep of every unit not on its own base.
        for player_id in range(2):
            away = self.UNITS_PER_PLAYER - self.units[player_id][self.bases[player_id]]
            self.profit_last_turn[player_id] = revenue[player_id] - self.UPKEEP * away
            self.cumulative_profit[player_id] += self.profit_last_turn[player_id]

        # 4. Deadlines: unfulfilled orders lose a turn; at 0 they expire. New orders appear.
        self.open_orders = [[n, r, t - 1] for n, r, t in still_open if t - 1 > 0]
        self.turn += 1
        if self.turn < self.N_TURNS:
            self.open_orders.extend(list(o) for o in self.instance.orders[self.turn])

    def is_over(self) -> bool:
        return self.turn >= self.N_TURNS

    def scores(self) -> list[float]:
        return list(self.cumulative_profit)

    @staticmethod
    def _check_action_format(action: Any) -> bool:
        """Shape only: a list of 3-int tuples. Legality of each move is checked in step()."""
        if not isinstance(action, list):
            return False
        for move in action:
            if not isinstance(move, tuple) or len(move) != 3:
                return False
            if not all(type(v) is int for v in move):
                return False
        return True
