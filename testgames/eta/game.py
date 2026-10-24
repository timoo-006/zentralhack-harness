"""
Eta Game: battery-powered rovers on a fixed symmetric map.

Every rover has a battery. It drains while the rover is away from its own base (moving drains
extra) and recharges only at the base; a rover whose battery hits 0 is stranded for good and
costs a towing fee every turn for the rest of the match. Demand is constant per node, except
for four "hot" nodes whose demand doubles every 10th turn. The seed only fixes which turns are
hot (the phase of the 10-turn cycle).
"""

import random
from typing import Any

from arena.core import Game

N_TURNS = 100
UNITS_PER_PLAYER = 16
UNIT_CAPACITY = 4
BATTERY_MAX = 10  # every rover starts at its base with a full battery
DRAIN = 1  # energy lost at the end of every turn a rover ends away from its own base
MOVE_COST = 1  # extra energy for moving one edge
RECHARGE = 3  # energy gained at the end of every turn a rover ends on its own base
TOW_FEE = 1.0  # per stranded rover, per turn

N_NODES = 12
BASES = [0, 11]  # index = player_id
NODE_DEMANDS = [0, 5, 3, 5, 8, 12, 12, 8, 5, 3, 5, 0]  # index = node id
EDGES = [
    (0, 1), (0, 2), (0, 3), (1, 2), (1, 4), (2, 3), (2, 5), (2, 6), (3, 4), (4, 5),
    (5, 9), (6, 7), (6, 9), (7, 8), (7, 10), (8, 9), (8, 11), (9, 10), (9, 11), (10, 11),
]  # fmt: skip
HOT_NODES = [4, 5, 6, 7]
HOT_PERIOD = 10  # a turn is hot when turn % HOT_PERIOD == hot_phase
HOT_MULTIPLIER = 2


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


def hot_phase_for(seed: int) -> int:
    """The match's hot phase, 0 .. HOT_PERIOD - 1, fixed by the seed."""
    return random.Random(f"eta:{seed}:hot").randrange(HOT_PERIOD)


def demand_at(turn: int, hot_phase: int) -> list[int]:
    """Demand of every node on `turn`: NODE_DEMANDS, hot nodes doubled on hot turns."""
    hot = turn % HOT_PERIOD == hot_phase
    return [
        d * HOT_MULTIPLIER if hot and node in HOT_NODES else d
        for node, d in enumerate(NODE_DEMANDS)
    ]


class EtaGame(Game):
    N_TURNS = N_TURNS
    UNITS_PER_PLAYER = UNITS_PER_PLAYER

    def __init__(self, seed: int | None = None, hot_phase: int | None = None) -> None:
        """Create the match for `seed`; `hot_phase` overrides the seed (for tests)."""
        super().__init__(seed)
        if hot_phase is None:
            if seed is None:
                seed = random.SystemRandom().randrange(2**31)
            hot_phase = hot_phase_for(seed)
        self.hot_phase = hot_phase
        self.edges = list(EDGES)
        self.graph: list[set[int]] = [set() for _ in range(N_NODES)]
        for u, v in self.edges:
            self.graph[u].add(v)
            self.graph[v].add(u)

        # units[player_id][unit_id] = [node, energy]
        self.units = [
            [[BASES[p], BATTERY_MAX] for _ in range(UNITS_PER_PLAYER)] for p in range(2)
        ]
        self.profit_last_turn = [0.0, 0.0]
        self.cumulative_profit = [0.0, 0.0]
        self.n_invalid_actions_last_turn = [0, 0]
        self.action_format_correct_last_turn = [True, True]
        self.turn = 0

    def observe(self, player_id: int) -> dict:
        opponent_id = 1 - player_id
        return {
            "turn": self.turn,
            "n_nodes": N_NODES,
            "edges": list(self.edges),
            "player_base": BASES[player_id],
            "opponent_base": BASES[opponent_id],
            "hot_phase": self.hot_phase,
            "demand": demand_at(self.turn, self.hot_phase),
            "player_units": [
                {"id": uid, "node": node, "energy": energy}
                for uid, (node, energy) in enumerate(self.units[player_id])
            ],
            "opponent_units": sorted((node, energy) for node, energy in self.units[opponent_id]),
            "player_profit_last_turn": self.profit_last_turn[player_id],
            "opponent_profit_last_turn": self.profit_last_turn[opponent_id],
            "player_cumulative_profit": self.cumulative_profit[player_id],
            "opponent_cumulative_profit": self.cumulative_profit[opponent_id],
            "n_invalid_actions_last_turn": self.n_invalid_actions_last_turn[player_id],
            "action_format_correct_last_turn": self.action_format_correct_last_turn[player_id],
        }

    def step(self, actions: list[Any]) -> None:
        self.n_invalid_actions_last_turn = [0, 0]

        # 1. Moves. Each player moves only its own rovers, so the order of players is irrelevant.
        for player_id in range(2):
            action = actions[player_id] if player_id < len(actions) else None
            ok = self._check_action_format(action)
            self.action_format_correct_last_turn[player_id] = ok
            if not ok:
                continue  # malformed action: the whole turn is a no-op
            units = self.units[player_id]
            moved: set[int] = set()
            for unit_id, to_node in action:
                valid = (
                    0 <= unit_id < UNITS_PER_PLAYER
                    and unit_id not in moved
                    and units[unit_id][1] > 0
                    and 0 <= to_node < N_NODES
                    and to_node in self.graph[units[unit_id][0]]
                )
                if not valid:
                    self.n_invalid_actions_last_turn[player_id] += 1
                    continue
                moved.add(unit_id)
                units[unit_id][0] = to_node
                units[unit_id][1] -= MOVE_COST

        # 2. Batteries: recharge at the own base, drain everywhere else.
        for player_id in range(2):
            for unit in self.units[player_id]:
                if unit[0] == BASES[player_id]:
                    unit[1] = min(BATTERY_MAX, unit[1] + RECHARGE)
                else:
                    unit[1] = max(0, unit[1] - DRAIN)

        # 3. Payout: only rovers with energy left serve demand; stranded ones pay the tow fee.
        demand = demand_at(self.turn, self.hot_phase)
        active = [[0] * N_NODES for _ in range(2)]
        stranded = [0, 0]
        for player_id in range(2):
            for node, energy in self.units[player_id]:
                if energy > 0:
                    active[player_id][node] += 1
                else:
                    stranded[player_id] += 1
        revenue = [0.0, 0.0]
        for node in range(N_NODES):
            capacity = [active[p][node] * UNIT_CAPACITY for p in range(2)]
            total = capacity[0] + capacity[1]
            if demand[node] == 0 or total == 0:
                continue
            for p in range(2):
                revenue[p] += capacity[p] * demand[node] / max(total, demand[node])
        self.profit_last_turn = [revenue[p] - TOW_FEE * stranded[p] for p in range(2)]
        for p in range(2):
            self.cumulative_profit[p] += self.profit_last_turn[p]
        self.turn += 1

    def is_over(self) -> bool:
        return self.turn >= N_TURNS

    def scores(self) -> list[float]:
        return list(self.cumulative_profit)

    @staticmethod
    def _check_action_format(action: Any) -> bool:
        """Shape only: a list of 2-int lists. Legality of each move is checked in step()."""
        if not isinstance(action, list):
            return False
        for move in action:
            if not isinstance(move, list) or len(move) != 2:
                return False
            if not all(isinstance(v, int) and not isinstance(v, bool) for v in move):
                return False
        return True
