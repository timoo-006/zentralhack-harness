"""Fixture: beta's GreedyResponse baseline as a stand-alone, stdlib-only strategy."""

import heapq
from collections import Counter

DRONE_CAPACITY = 5
UPKEEP = 2.0


def hop_distances(n_nodes, edges):
    neighbours = [[] for _ in range(n_nodes)]
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


def _served(own, other, demand):
    cap, total = own * DRONE_CAPACITY, (own + other) * DRONE_CAPACITY
    if cap == 0 or demand == 0:
        return 0.0
    return cap * demand / max(total, demand)


class Strategy:
    def __init__(self, player_id):
        self.player_id = player_id
        self.neighbours = None
        self.dist = []

    def _setup(self, obs):
        if self.neighbours is not None:
            return
        n = obs["n_nodes"]
        self.neighbours = [[] for _ in range(n)]
        for u, v in obs["edges"]:
            self.neighbours[u].append(v)
            self.neighbours[v].append(u)
        self.dist = hop_distances(n, obs["edges"])

    def _move_towards(self, drones, target):
        surplus = {n: drones[n] - t for n, t in enumerate(target) if drones[n] > t}
        deficit = {n: t - drones[n] for n, t in enumerate(target) if drones[n] < t}
        moves = Counter()
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
        return [(src, hop, k) for (src, hop), k in moves.items()]

    def act(self, obs):
        self._setup(obs)
        base = obs["player_base"]
        opponent = obs["opponent_drones"]
        fleet = sum(obs["player_drones"])
        demand = [sum(f) / len(f) for f in obs["demand_forecast"]]
        target = [0] * obs["n_nodes"]

        def gain(node):
            k = target[node]
            return (
                _served(k + 1, opponent[node], demand[node])
                - _served(k, opponent[node], demand[node])
                - UPKEEP
            )

        heap = [(-gain(n), n) for n in range(obs["n_nodes"]) if n != base and demand[n] > 0]
        heapq.heapify(heap)
        for _ in range(fleet):
            if not heap:
                break
            neg_gain, node = heapq.heappop(heap)
            if -neg_gain <= 0:
                break
            target[node] += 1
            heapq.heappush(heap, (-gain(node), node))
        target[base] = fleet - sum(target)
        return self._move_towards(obs["player_drones"], target)
