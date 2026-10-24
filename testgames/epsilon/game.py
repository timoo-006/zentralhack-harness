"""
Epsilon Game: territory on a map whose edges take time to travel.

Differences to the dev games that a harness must adapt to: every edge has a length of 1 to 3
turns and units are in transit (visible to both players) while they travel it, so shortest
paths are weighted (Dijkstra, not BFS); nodes are not served but owned: a node becomes yours
after you have strictly more units on it than the opponent at the end of 2 consecutive turns,
and then pays you its value every turn, even with no unit on it, until the opponent captures
it. Units cost upkeep whenever they are away from their own base, in transit included.
Everything random (map, edge lengths, bases, values) is drawn from the seed when the game is
created, so every pairing that plays a seed faces exactly the same environment.
"""

import heapq
import random
from dataclasses import dataclass
from typing import Any

from arena.core import Game

N_TURNS = 80
UNITS_PER_PLAYER = 20
UPKEEP = 2.0  # per unit away from its own base (in transit included), per turn
CAPTURE_TURNS = 2  # consecutive turn ends with a strict majority on a node to capture it

# map generator
N_NODES_RANGE = (10, 14)  # inclusive
EXTRA_EDGE_FRACTION = 0.4  # extra edges = round(EXTRA_EDGE_FRACTION * n_nodes)
EDGE_LENGTH_RANGE = (1, 3)  # inclusive, uniform integer, drawn per edge
MIN_BASE_DISTANCE = 3  # min shortest-path length (in edges, not turns) between the two bases

# node values
VALUE_RANGE = (1, 6)  # inclusive, uniform integer, drawn per non-base node


@dataclass(frozen=True)
class Instance:
    """Everything a match's environment consists of, fixed at creation."""

    n_nodes: int
    edges: list[tuple[int, int]]  # undirected, u < v, sorted
    lengths: list[int]  # lengths[k] is the length (travel turns) of edges[k]
    bases: list[int]  # index = player_id
    values: list[int]  # value per turn of each node; 0 on both bases


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


def travel_distances(n_nodes: int, edges: list[tuple[int, int, int]]) -> list[list[int]]:
    """All-pairs travel times (Dijkstra over (u, v, length) edges); -1 if unreachable."""
    neighbours: list[list[tuple[int, int]]] = [[] for _ in range(n_nodes)]
    for u, v, length in edges:
        neighbours[u].append((v, length))
        neighbours[v].append((u, length))
    dist = []
    for source in range(n_nodes):
        row = [-1] * n_nodes
        heap = [(0, source)]
        while heap:
            d, u = heapq.heappop(heap)
            if row[u] != -1:
                continue
            row[u] = d
            for v, length in neighbours[u]:
                if row[v] == -1:
                    heapq.heappush(heap, (d + length, v))
        dist.append(row)
    return dist


def generate_map(
    rng: random.Random,
) -> tuple[int, list[tuple[int, int]], list[int], list[int]]:
    """Random connected graph, edge lengths, and two bases at least MIN_BASE_DISTANCE apart."""
    while True:
        n = rng.randint(*N_NODES_RANGE)

        # Random spanning tree: each node in a random order hooks onto an earlier one.
        perm = list(range(n))
        rng.shuffle(perm)
        edges: set[tuple[int, int]] = set()
        for k in range(1, n):
            u, v = perm[k], perm[rng.randrange(k)]
            edges.add((min(u, v), max(u, v)))

        # Extra edges between uniformly drawn unconnected pairs: cycles and alternative routes.
        n_extra = round(EXTRA_EDGE_FRACTION * n)
        while n_extra > 0:
            u, v = rng.sample(range(n), 2)
            edge = (min(u, v), max(u, v))
            if edge not in edges:
                edges.add(edge)
                n_extra -= 1

        edge_list = sorted(edges)
        lengths = [rng.randint(*EDGE_LENGTH_RANGE) for _ in edge_list]
        dist = hop_distances(n, edge_list)
        base_0 = rng.randrange(n)
        candidates = [v for v in range(n) if dist[base_0][v] >= MIN_BASE_DISTANCE]
        if not candidates:
            continue  # bases cannot be far enough apart on this graph: draw a new one
        base_1 = rng.choice(candidates)
        return n, edge_list, lengths, [base_0, base_1]


def generate_instance(seed: int) -> Instance:
    """The match environment for `seed`. Map and values use independent RNG streams."""
    map_rng = random.Random(f"epsilon:{seed}:map")
    value_rng = random.Random(f"epsilon:{seed}:value")
    n_nodes, edges, lengths, bases = generate_map(map_rng)
    values = [0 if v in bases else value_rng.randint(*VALUE_RANGE) for v in range(n_nodes)]
    return Instance(n_nodes, edges, lengths, bases, values)


class EpsilonGame(Game):
    N_TURNS = N_TURNS
    UNITS_PER_PLAYER = UNITS_PER_PLAYER
    UPKEEP = UPKEEP
    CAPTURE_TURNS = CAPTURE_TURNS

    def __init__(self, seed: int | None = None, instance: Instance | None = None) -> None:
        """Create the match for `seed`; `instance` overrides the generator (for tests)."""
        super().__init__(seed)
        if instance is None:
            if seed is None:
                seed = random.SystemRandom().randrange(2**31)
            instance = generate_instance(seed)
        self.instance = instance

        self.n_nodes = instance.n_nodes
        self.edges = [
            (u, v, length) for (u, v), length in zip(instance.edges, instance.lengths, strict=True)
        ]
        self.length: dict[tuple[int, int], int] = {}
        for u, v, length in self.edges:
            self.length[(u, v)] = length
            self.length[(v, u)] = length
        self.bases = list(instance.bases)
        self.values = list(instance.values)

        self.units = [[0] * self.n_nodes, [0] * self.n_nodes]
        for player_id, base in enumerate(self.bases):
            self.units[player_id][base] = self.UNITS_PER_PLAYER
        # Per player: {(from_node, to_node, arrival_turn): count}
        self.transits: list[dict[tuple[int, int, int], int]] = [{}, {}]
        self.owner: list[int | None] = [None] * self.n_nodes
        self.lead_turns = [[0] * self.n_nodes, [0] * self.n_nodes]

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
            "node_value": list(self.values),
            "player_units": list(self.units[player_id]),
            "opponent_units": list(self.units[opponent_id]),
            "player_transits": self._transit_list(player_id),
            "opponent_transits": self._transit_list(opponent_id),
            "player_owned": [o == player_id for o in self.owner],
            "opponent_owned": [o == opponent_id for o in self.owner],
            "player_lead_turns": list(self.lead_turns[player_id]),
            "opponent_lead_turns": list(self.lead_turns[opponent_id]),
            "player_profit_last_turn": self.profit_last_turn[player_id],
            "opponent_profit_last_turn": self.profit_last_turn[opponent_id],
            "player_cumulative_profit": self.cumulative_profit[player_id],
            "opponent_cumulative_profit": self.cumulative_profit[opponent_id],
            "n_invalid_actions_last_turn": self.n_invalid_actions_last_turn[player_id],
            "action_format_correct_last_turn": self.action_format_correct_last_turn[player_id],
        }

    def _transit_list(self, player_id: int) -> list[tuple[int, int, int, int]]:
        """(from_node, to_node, count, arrival_turn), sorted by arrival, from, to."""
        return [
            (f, t, count, arrival)
            for (f, t, arrival), count in sorted(
                self.transits[player_id].items(), key=lambda kv: (kv[0][2], kv[0][0], kv[0][1])
            )
        ]

    def step(self, actions: list[Any]) -> None:
        if self.is_over():
            return
        self.n_invalid_actions_last_turn = [0, 0]

        # 1. Orders: units leave their node and go into transit.
        for player_id in range(2):
            action = actions[player_id] if player_id < len(actions) else None
            self.action_format_correct_last_turn[player_id] = self._check_action_format(action)
            if not self.action_format_correct_last_turn[player_id]:
                continue  # malformed action: treat the whole turn as a no-op

            # Only units present at the start of the turn can be ordered.
            movable = list(self.units[player_id])
            for from_node, to_node, count in action:
                length = self.length.get((from_node, to_node))
                valid = (
                    0 <= from_node < self.n_nodes
                    and 0 <= to_node < self.n_nodes
                    and length is not None
                    and 0 <= count <= movable[from_node]
                )
                if not valid:
                    self.n_invalid_actions_last_turn[player_id] += 1
                    continue
                if count == 0:
                    continue
                movable[from_node] -= count
                self.units[player_id][from_node] -= count
                key = (from_node, to_node, self.turn + length)
                self.transits[player_id][key] = self.transits[player_id].get(key, 0) + count

        # 2. Arrivals: transits due on the next turn land at the end of this turn.
        for player_id in range(2):
            transits = self.transits[player_id]
            for key in [k for k in transits if k[2] == self.turn + 1]:
                self.units[player_id][key[1]] += transits.pop(key)

        # 3. Ownership: strict majority at the end of CAPTURE_TURNS consecutive turns.
        for node in range(self.n_nodes):
            if node in self.bases:
                continue
            a, b = self.units[0][node], self.units[1][node]
            if a == b:
                self.lead_turns[0][node] = self.lead_turns[1][node] = 0
                continue
            leader = 0 if a > b else 1
            self.lead_turns[leader][node] += 1
            self.lead_turns[1 - leader][node] = 0
            if self.lead_turns[leader][node] >= self.CAPTURE_TURNS:
                self.owner[node] = leader

        # 4. Payout: owned values minus upkeep of every unit not on its own base.
        for player_id in range(2):
            revenue = float(sum(v for v, o in zip(self.values, self.owner) if o == player_id))
            away = self.UNITS_PER_PLAYER - self.units[player_id][self.bases[player_id]]
            self.profit_last_turn[player_id] = revenue - self.UPKEEP * away
            self.cumulative_profit[player_id] += self.profit_last_turn[player_id]

        self.turn += 1

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
            if not all(isinstance(v, int) and not isinstance(v, bool) for v in move):
                return False
        return True
