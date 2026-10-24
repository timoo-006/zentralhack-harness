"""Reference strategies for Kappa. All of them only ever play legal, well-formed actions."""

import random

from arena.core import Strategy


def _neighbours(obs: dict) -> list[list[tuple[int, int]]]:
    """neighbours[u] = [(v, edge index), ...]"""
    nb: list[list[tuple[int, int]]] = [[] for _ in range(obs["n_nodes"])]
    for k, (u, v) in enumerate(obs["edges"]):
        nb[u].append((v, k))
        nb[v].append((u, k))
    return nb


def _bfs(nb: list[list[tuple[int, int]]], source: int) -> list[int]:
    dist = [-1] * len(nb)
    dist[source] = 0
    frontier = [source]
    while frontier:
        nxt = []
        for u in frontier:
            for v, _ in nb[u]:
                if dist[v] == -1:
                    dist[v] = dist[u] + 1
                    nxt.append(v)
        frontier = nxt
    return dist


class DoNothing(Strategy):
    """Never moves: scores exactly 0."""

    def act(self, obs: dict) -> dict:
        return {"moves": []}


class RandomStrategy(Strategy):
    """Each turn, sends single units from random occupied nodes to random neighbours, within the
    edge capacities."""

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id)
        self.rng = random.Random(1000 + player_id)

    def act(self, obs: dict) -> dict:
        nb = _neighbours(obs)
        available = list(obs["player_units"])
        remaining = list(obs["edge_capacity"])
        moves = []
        for _ in range(self.rng.randint(0, 3)):
            occupied = [i for i, c in enumerate(available) if c > 0]
            if not occupied:
                break
            a = self.rng.choice(occupied)
            b, k = self.rng.choice(nb[a])
            if remaining[k] < 1:
                continue
            available[a] -= 1
            remaining[k] -= 1
            moves.append([a, b, 1])
        return {"moves": moves}


class GreedyMajority(Strategy):
    """Keeps a strict majority where it has one; sends every spare unit one step along a
    shortest path towards the node with the best demand / (distance + 1) that it does not hold
    yet and can take with few units, within the edge capacities."""

    MAX_NEED = 3  # ignore nodes that would need more than this many extra units

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id)
        self.nb: list[list[tuple[int, int]]] | None = None
        self.dist: list[list[int]] = []

    def act(self, obs: dict) -> dict:
        if self.nb is None:
            self.nb = _neighbours(obs)
            self.dist = [_bfs(self.nb, s) for s in range(obs["n_nodes"])]
        n = obs["n_nodes"]
        mine, theirs, demand = obs["player_units"], obs["opponent_units"], obs["demand"]
        # units each node must keep: just enough for the majority it holds now
        keep = [0] * n
        need = [0] * n
        for i in range(n):
            if demand[i] <= 0:
                continue
            if mine[i] > theirs[i]:
                keep[i] = theirs[i] + 1
            else:
                need[i] = theirs[i] + 1 - mine[i]
        spare = [mine[i] - keep[i] for i in range(n)]
        remaining = list(obs["edge_capacity"])
        planned: dict[tuple[int, int], int] = {}
        for a in range(n):
            for _ in range(spare[a]):
                targets = [j for j in range(n) if 0 < need[j] <= self.MAX_NEED and j != a]
                if not targets:
                    break
                j = max(targets, key=lambda t: (demand[t] / (self.dist[a][t] + 1), -t))
                step = None
                for v, k in sorted(self.nb[a]):
                    if self.dist[v][j] == self.dist[a][j] - 1 and remaining[k] > 0:
                        step = (v, k)
                        break
                if step is None:
                    continue
                v, k = step
                remaining[k] -= 1
                need[j] -= 1
                planned[(a, v)] = planned.get((a, v), 0) + 1
        return {"moves": [[a, b, c] for (a, b), c in sorted(planned.items())]}
