# Game Delta

## Overview

Two players compete for demand on a graph over a fixed number of turns. Each turn, both players
move drones between nodes, and may buy new drones with the money they have earned. Drones at a
node serve that node's demand; when both players have drones on the same node, the side with
**more drones serves first** and the other side only gets what is left. Every drone that is away
from its home base costs upkeep each turn. A player's profit is the demand it serves minus the
upkeep it pays and the drones it buys, summed over the whole game. The player with the higher
profit wins the match (see "Tournament and ranking").

Two things are hidden:

- **Fog of war.** You only see the opponent's drones on nodes close to your own drones or your
  base.
- **The demand's mean.** Each node's demand is drawn at random every turn around a mean of its
  own. You see each turn's demand, but not the means.

What changes from match to match: the map (a different random graph with randomly placed bases
every match, given in the observation) and the mean demand of every node.

This document is the complete definition of the game. You will not get the game's source code or
a simulator, so everything needed to write a strategy, or to build your own simulator, is here.
Your deliverable is one Python class, described in "Your strategy".

## Constants

Every constant in this document, as Python you can copy:

```python
N_TURNS = 120
START_DRONES = 10  # per player, on its base
MAX_DRONES = 40  # most drones a player may own
DRONE_CAPACITY = 4
UPKEEP = 2.0  # per drone away from its own base, per turn
BUILD_COST = 15.0  # per drone bought
START_MONEY = 20.0
VISION_RANGE = 1  # you see the opponent's drones within this many edges of yours / your base

# map generator (see "Map")
N_NODES_RANGE = (12, 18)  # inclusive
EXTRA_EDGE_FRACTION = 0.3
MIN_BASE_DISTANCE = 4

# demand (see "Demand")
MEAN_DEMAND_RANGE = (1.0, 10.0)  # hidden per-node mean, uniform
DEMAND_NOISE = 0.5
```

## Seeds

Each match is created from an integer seed. The seed fixes the map, the bases and the demand of
every turn, before the first turn is played. Nothing about the environment depends on what the
players do. In a tournament both players of a match, and every pairing that plays the same seed,
face exactly the same map and demand.

You cannot reproduce a match's map or demand from its seed: the generator below is specified
exactly in distribution, not in its random number calls. Use it to generate maps and demand of
your own, for example to test a strategy.

## Map

The map is an undirected, connected graph with `n_nodes` nodes, numbered `0` to `n_nodes - 1`.
Two of the nodes are bases, one per player. It is generated as follows:

1. Draw `n_nodes` uniformly from 12 to 18 (inclusive).
2. **Spanning tree.** Put the node ids in a uniformly random order `p[0], ..., p[n_nodes - 1]`.
   For `k = 1, ..., n_nodes - 1`, add the edge between `p[k]` and `p[j]`, with `j` drawn
   uniformly from `0, ..., k - 1`. This makes the graph connected.
3. **Extra edges.** Add `round(EXTRA_EDGE_FRACTION * n_nodes)` more edges, with Python's
   `round` (so `4.5` rounds to `4`): 4 extra edges for 12 to 15 nodes, 5 for 16 to 18 nodes.
   Each one connects a uniformly drawn pair of distinct nodes that are not connected yet.
4. **Bases.** Draw the base of player 0 uniformly from all nodes. Draw the base of player 1
   uniformly from the nodes whose shortest-path distance (in edges) from player 0's base is at
   least `MIN_BASE_DISTANCE` (4). If there is no such node, discard the graph and start again at
   step 1.

There are no self-loops and no duplicate edges. The map is not symmetric. Over a tournament this
evens out, because every pairing plays each seed in both seats (see "Tournament and ranking").

The map does not change during a match. It is in every observation: `n_nodes`, `edges`,
`player_base` and `opponent_base`. **Distances** below are always shortest-path distances in
edges on this graph.

## Demand

Each turn `t` (from `0` to `N_TURNS - 1 = 119`), every node `i` has a demand `demand[t][i] ≥ 0`.
Both bases have demand `0.0` on every turn. Every other node has a hidden **mean** `mean_i`,
drawn once per match, and a fresh random demand around it every turn:

```
mean_i       = uniform(1.0, 10.0)                                   # MEAN_DEMAND_RANGE, hidden
demand[t][i] = round(mean_i * uniform(1 - 0.5, 1 + 0.5), 1)         # DEMAND_NOISE = 0.5
```

All draws are independent: the demand of a node on one turn says nothing about its demand on
the next turn beyond what it tells you about the node's mean. So each node's demand is between
half and one and a half times its mean, and its average over many turns is close to its mean.
Demand is rounded to one decimal (Python's `round`).

**The observation does not tell you the means.** You see the demand of the current turn only
(see "Observation"); to plan ahead, estimate each node's mean from the demand you have seen.

## Units

Each player starts with `START_DRONES = 10` drones on its base. Every drone has capacity 4 (it
serves at most 4 demand per turn).

A drone is **deployed** when it is not on its own player's base. Every deployed drone costs
`UPKEEP = 2.0` per turn (see "Scoring"), including drones on the opponent's base and drones on
nodes without demand. Drones on their own base cost nothing and earn nothing.

Any number of drones, from either player, may be at the same node. Drones may move onto any node,
including the opponent's base. Drones are never destroyed.

### Money and buying drones

Each player has **money**, which is always

```
money = START_MONEY + cumulative_profit          # START_MONEY = 20.0
```

so it starts at `20.0` and goes up and down with every turn's profit. Money can be negative.

Each turn you may **buy** drones (the `"build"` part of the action). Each drone costs
`BUILD_COST = 15.0`, which is taken out of that turn's profit (see "Scoring"). A purchase of `b`
drones is allowed if:

- `b * BUILD_COST <= money`, with `money` as it is at the start of the turn (`player_money` in
  the observation), and
- you then own at most `MAX_DRONES = 40` drones, counting all of your drones wherever they are.

Bought drones appear on your base after this turn's moves. They cannot move until the next turn,
and they cost no upkeep while they stay on the base.

## Your strategy

A strategy is a Python class with two methods:

```python
class MyStrategy:
    def __init__(self, player_id: int) -> None:
        self.player_id = player_id  # 0 or 1

    def act(self, obs: dict) -> dict:
        return {"moves": [], "build": 0}  # this turn's action; see "Observation" and "Action"
```

- A new instance is created for every match, with `player_id` 0 or 1.
- `act` is called once per turn, 120 times per match. You can keep state on `self` between turns
  (for example, the demand you have seen so far, or where you last saw opponent drones).
- The map is not known in `__init__`. It is in every observation; a strategy that needs
  precomputed data about the map (distances, paths) typically builds it on the first call of
  `act` and keeps it on `self`.
- Everything in the observation is from your point of view (`player_*` fields are yours,
  `opponent_*` fields are the opponent's), so you do not need `player_id` to play.
- If `act` raises an exception, takes too long or crashes, you forfeit the rest of the match (see
  "End of game").

## Turn order

Each turn:

1. both players' `act(obs)` are called with the observation for the current turn,
2. each returns its action (a list of moves and a number of drones to buy),
3. both players' moves are applied, then their purchases (the new drones appear on the bases),
   then that turn's profit is paid out (see "Scoring"), then the turn number goes up by one.

Moves are simultaneous: neither player sees the other's moves for this turn before choosing its
own. Each player only moves its own drones, so the order in which the two players' moves are
applied makes no difference.

**Each drone moves at most one edge per turn.** A move can only use drones that were at
`from_node` at the start of the turn. Drones that arrive at a node during a turn cannot move on
until the next turn. For example, if your base is node 0 and it is connected to node 11, which is
connected to node 8, then `[(0, 11, 3), (11, 8, 2)]` from the start position moves 3 drones to
node 11, and the second move is illegal.

The payout of turn `t` uses `demand[t]`, the demand shown in the observation of turn `t`, and the
drone positions after that turn's moves and purchases.

## Visibility

On every turn, a node is **visible** to you if its distance to your base, or to a node that holds
at least one of your drones, is at most `VISION_RANGE = 1`. In other words: your base, every node
with your drones, and all their neighbours. Visibility is computed from the positions at the
start of the turn, when the observation is made.

You see the opponent's drone count on visible nodes only. You never see the opponent's money,
profit, total number of drones or purchases. The opponent's base is visible only when one of your
drones is on it or next to it.

## Observation

Each turn your strategy's `act(obs)` receives a `dict`:

```python
{
    "turn": int,  # current turn, starting at 0
    "n_nodes": int,  # number of nodes; node ids are 0 .. n_nodes - 1
    "edges": list[tuple[int, int]],  # every undirected edge once, as (u, v) with u < v, sorted
    "player_base": int,  # node id of your base
    "opponent_base": int,  # node id of the opponent's base
    "demand": list[float],  # length n_nodes: each node's demand this turn (paid on this turn)
    "player_drones": list[int],  # length n_nodes, index = node id: your drones at that node
    "opponent_drones": list[int | None],  # length n_nodes: opponent's drones there, None if the
    # node is not visible to you (see "Visibility")
    "player_money": float,  # START_MONEY + player_cumulative_profit
    "player_profit_last_turn": float,  # your profit in the last turn (can be negative)
    "player_cumulative_profit": float,  # your total profit so far: your score
    "n_invalid_actions_last_turn": int,  # number of your illegal moves and purchases last turn
    "action_format_correct_last_turn": bool,  # whether your last action was well-formed
}
```

- `demand` is exact: these are the values this turn's payout uses. Both bases are included, with
  `0.0`.
- `opponent_drones[i]` is an `int` (possibly `0`) on a visible node and `None` on a node you
  cannot see. `None` does not mean "no drones".
- `player_money` is computed exactly as `START_MONEY + player_cumulative_profit` (a float).

The total number of turns is not in the observation (see "End of game").

On turn 0 there is no last turn yet: `player_profit_last_turn` and `player_cumulative_profit`
are `0.0`, `player_money` is `20.0`, `n_invalid_actions_last_turn` is `0`, and
`action_format_correct_last_turn` is `True`.

## Action

Terms used below: an **action** is what `act` returns for one turn. It is a `dict` with exactly
two keys:

```python
{"moves": [(from_node, to_node, count), ...], "build": n_buy}
```

- `"moves"` is a list of **moves**. Each move sends `count` of your drones from `from_node` to
  `to_node` along one edge. Moves in the list are applied in order. An empty list means "no
  moves"; drones that are not moved stay where they are.
- `"build"` is the number of drones to buy this turn (`0` for none).
- `{"moves": [], "build": 0}` means "do nothing".

Two things can go wrong, and next turn's observation reports them separately.

**Malformed action:** the whole action is ignored for that turn (no moves, no purchase), as if
you had returned `{"moves": [], "build": 0}`. Next turn, `action_format_correct_last_turn` is
`False` and `n_invalid_actions_last_turn` is `0`, because nothing was looked at. The format is
checked strictly:

- the action must be a `dict` (not a list, not `None`, not any other type),
- its keys must be exactly `{"moves", "build"}` (a missing key or any extra key is malformed),
- `"moves"` must be a `list` (not a tuple), and each move a `tuple` of exactly 3 items (a list
  like `[0, 1, 5]` is malformed),
- each item of a move, and `"build"`, must be a plain Python `int`. `bool` values and numpy
  integers such as `np.int64` are malformed. Convert them with `int(...)`.

`None` is malformed, so returning `None` is always a safe "do nothing".

**Illegal move or purchase:** the action is well-formed, but one part breaks a rule. Only that
part is skipped, the rest still applies, and each illegal part adds 1 to
`n_invalid_actions_last_turn`. A move is illegal if:

- `from_node` or `to_node` is not a node id (`0` to `n_nodes - 1`),
- `from_node` and `to_node` are not connected by an edge (this includes `from_node == to_node`),
- `count` is negative,
- `count` is more than the drones you still have at `from_node` from the start of the turn, after
  earlier moves in the same list (see "Turn order").

A move with `count` 0 is legal and does nothing. A `"build"` of `0` is always legal. A `"build"`
of `b != 0` is illegal, and no drone is bought, if `b` is negative, if you would own more than
`MAX_DRONES` drones, or if `b * BUILD_COST` is more than your money at the start of the turn
(see "Money and buying drones"). The moves of that turn still apply.

## Scoring

After all moves and purchases are applied, each player's profit for the turn is its **revenue**
minus its **upkeep** and its **purchases**.

**Revenue: the bigger fleet serves first.** At each node, with `n_p` the drones player `p` has
there and `demand` this turn's demand of the node:

```
capacity_p = n_p * DRONE_CAPACITY                          # DRONE_CAPACITY = 4

if n_0 == n_1:          # equal fleets (or none): split in proportion to capacity
    served_p = capacity_p * demand / max(capacity_0 + capacity_1, demand)
else:                   # the player with more drones there serves first
    served_more  = min(capacity_more, demand)
    served_fewer = min(capacity_fewer, demand - served_more)
```

Nodes with no demand or no drones contribute nothing. Your revenue for a turn is the sum of your
`served` over all nodes.

For example, at a node with demand 10.0:

- 1 drone alone serves `min(4, 10) = 4`.
- 2 drones against 1: the 2 drones (capacity 8) serve `min(8, 10) = 8`, the single drone serves
  what is left, `min(4, 10 - 8) = 2`.
- 3 drones against 2: the 3 drones (capacity 12) serve all `10`, the 2 drones serve `0` and only
  pay upkeep.
- 2 drones against 2: equal fleets share in proportion to capacity: total capacity 16 meets the
  demand, so each side serves `10 * 8 / 16 = 5`.

**Upkeep and purchases.**

```
upkeep_p = UPKEEP * (drones p owns - drones p has on its own base)     # UPKEEP = 2.0
profit_p = revenue_p - upkeep_p - BUILD_COST * (drones p bought this turn)   # BUILD_COST = 15.0
```

Profit can be negative. Your score in a match is the sum of your profits over all turns, which
is also `player_cumulative_profit`, and your final money minus `START_MONEY`. A player that never
moves and never buys scores exactly `0`.

### Worked example: turns 0 and 1

This is a real map (`n_nodes = 12`). Player 0's observation on turn 0 is:

```python
{
    "turn": 0,
    "n_nodes": 12,
    "edges": [
        (0, 7),
        (0, 11),
        (1, 2),
        (1, 3),
        (1, 9),
        (2, 8),
        (3, 4),
        (3, 10),
        (3, 11),
        (4, 5),
        (4, 8),
        (6, 7),
        (6, 10),
        (8, 11),
        (9, 10),
    ],
    "player_base": 0,
    "opponent_base": 9,
    "demand": [0.0, 2.1, 7.4, 1.5, 3.4, 7.3, 10.4, 1.0, 8.6, 0.0, 10.5, 2.3],
    "player_drones": [10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "opponent_drones": [0, None, None, None, None, None, None, 0, None, None, None, 0],
    "player_money": 20.0,
    "player_profit_last_turn": 0.0,
    "player_cumulative_profit": 0.0,
    "n_invalid_actions_last_turn": 0,
    "action_format_correct_last_turn": True,
}
```

On turn 0 player 0 sees its base (node 0) and its neighbours, nodes 7 and 11; every other node is
`None`. Player 1's base is node 9.

- Player 0 plays `{"moves": [(0, 11, 3), (0, 7, 3), (11, 8, 2)], "build": 1}`.
- Player 1 plays `{"moves": [(9, 10, 6), (9, 1, 2)], "build": 0}`.

Player 0's third move `(11, 8, 2)` is illegal: the drones that arrive at node 11 this turn cannot
move on. Its purchase of 1 drone is legal (`15.0 <= 20.0`); the new drone appears on node 0.
After the moves, player 0 has 3 drones on node 7, 3 on node 11 and 4 + 1 = 5 on its base;
player 1 has 6 drones on node 10, 2 on node 1 and 2 on its base. No node is contested. The payout
uses the turn-0 demand:

| node | demand | drones | capacity | served |
|-----:|-------:|-------:|---------:|-------:|
| 7    | 1.0    | P0: 3  | 12       | 1.0    |
| 11   | 2.3    | P0: 3  | 12       | 2.3    |
| 10   | 10.5   | P1: 6  | 24       | 10.5   |
| 1    | 2.1    | P1: 2  | 8        | 2.1    |

| player | revenue | deployed | upkeep | bought | purchases | profit | money after |
|-------:|--------:|---------:|-------:|-------:|----------:|-------:|------------:|
| P0     | 3.3     | 6        | 12.0   | 1      | 15.0      | -23.7  | -3.7        |
| P1     | 12.6    | 8        | 16.0   | 0      | 0.0       | -3.4   | 16.6        |

Both players lost money on turn 0: player 0 sent 3 drones where 1 would have served the same
demand, and bought a drone it cannot use before turn 1. With negative money it cannot buy again
until it has earned at least 15.0 more. Player 0's observation on turn 1 is then:

```python
{
    "turn": 1,
    "n_nodes": 12,
    "edges": [
        (0, 7),
        (0, 11),
        (1, 2),
        (1, 3),
        (1, 9),
        (2, 8),
        (3, 4),
        (3, 10),
        (3, 11),
        (4, 5),
        (4, 8),
        (6, 7),
        (6, 10),
        (8, 11),
        (9, 10),
    ],
    "player_base": 0,
    "opponent_base": 9,
    "demand": [0.0, 2.4, 13.6, 2.3, 7.7, 9.5, 12.9, 2.1, 13.0, 0.0, 5.8, 2.2],
    "player_drones": [5, 0, 0, 0, 0, 0, 0, 3, 0, 0, 0, 3],
    "opponent_drones": [0, None, None, 0, None, None, 0, 0, 0, None, None, 0],
    "player_money": -3.6999999999999993,
    "player_profit_last_turn": -23.7,
    "player_cumulative_profit": -23.7,
    "n_invalid_actions_last_turn": 1,
    "action_format_correct_last_turn": True,
}
```

Player 0's drones are now on nodes 0, 7 and 11, so it sees nodes 0, 7 and 11, their neighbours 6
(next to 7) and 3 and 8 (next to 11): six visible nodes, all without opponent drones. It does not
see nodes 1 and 10, where player 1's drones are. Player 1's observation on turn 1 shows
`opponent_drones` `None` on nodes 0, 4, 5, 7, 8 and 11, and `0` on its visible nodes 1, 2, 3, 6,
9 and 10.

Profits and money are Python floats and can carry rounding error in their last digits (here
`-3.6999999999999993` for `20.0 + (-23.7)`); compare them with a tolerance.

## End of game

The game ends after 120 turns (turns 0 to 119). Each player's score is the profit it
collected; the higher score wins (see "Tournament and ranking").

Your strategy forfeits the rest of the match if it raises an exception, takes longer than
1 second for one call of `act` (10 seconds for `__init__`), or crashes. From that turn on your
actions are empty (no moves, no purchases): your drones stay where they are, still take their
share of each node's demand and still cost upkeep, and both still count for your score. The match
always plays on to the end, even if both players have forfeited.

## Tournament and ranking

Every match has a result. The player with the higher score **wins** and the other one loses. If
the two scores differ by at most 1% of `|score_0| + |score_1|`, the match is a **draw** (two
scores of `0` are a draw). A strategy that forfeits stays in the match: its score still counts as
described in "End of game", and the result is decided by the scores alone.

Your strategy plays a tournament against the strategies of the other teams: every pairing plays in
both seats, on the same set of seeds. Each team takes part with several strategies (one per run
of its harness); strategies of the same team never play each other. No reference strategies take
part. A separate tournament is played for each game.

Only results count, not by how much a match was won or lost:

- A win counts 1, a draw ½, a loss 0. Your goal is to beat your opponent: lowering the
  opponent's score helps you exactly as much as raising your own.
- Every strategy gets a **rating** from all its results in the tournament, with the
  Bradley–Terry model: strategy A beats strategy B with probability
  `1 / (1 + 10 ** ((R_B - R_A) / 400))`, and the ratings `R` are those that make the observed
  results most likely (a draw counts as half a win for each). Unlike a plain win rate, a rating
  accounts for how strong the opponents were that a strategy happened to meet.
- A team's rating in a game is the mean of the ratings of its strategies. Across games, a team's
  ratings are averaged.
