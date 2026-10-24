# Game Kappa

## Overview

Two players compete for nodes on a graph over a fixed number of turns. Each turn, both players
move units between nodes. Every node has a fixed demand, and the side with **strictly more units**
on a node earns that node's whole demand that turn; on a tie nobody earns there. Edges are
**congested**: each edge lets only a few of your units through per turn. There is no upkeep, but
every unit you move costs money. A player's profit is the demand it earns minus what it pays for
moving, summed over the whole game. The player with the higher profit wins the match (see
"Tournament and ranking").

What changes from match to match: **the map**. Every match is played on a different random graph
with randomly placed bases, random edge capacities and random node demands. You learn all of it
from the observation, at the start of the match. Nothing changes during a match except the
positions of the units.

This document is the complete definition of the game. You will not get the game's source code or
a simulator, so everything needed to write a strategy, or to build your own simulator, is here.
Your deliverable is one Python class, described in "Your strategy".

## Constants

Every constant in this document, as Python you can copy:

```python
N_TURNS = 80
UNITS_PER_PLAYER = 15
MOVE_COST = 0.5  # per unit moved, per move order

# map generator (see "Map")
N_NODES_RANGE = (10, 14)  # inclusive
EXTRA_EDGE_FRACTION = 0.4  # extra edges = round(EXTRA_EDGE_FRACTION * n_nodes)
CAPACITY_RANGE = (1, 4)  # inclusive, uniform integer, drawn per edge
MIN_BASE_DISTANCE = 3  # min shortest-path length (in edges) between the two bases

# node demand (see "Demand")
DEMAND_RANGE = (1, 6)  # inclusive, uniform integer, drawn per non-base node
```

## Seeds

Each match is created from an integer seed. The seed fixes the map, the edge capacities, the
bases and the demand, before the first turn is played. Nothing about the environment depends on
what the players do. In a tournament both players of a match, and every pairing that plays the
same seed, face exactly the same map.

You cannot reproduce a match's map from its seed: the generator below is specified exactly in
distribution, not in its random number calls. Use it to generate maps of your own, for example to
test a strategy.

## Map

The map is an undirected, connected graph with `n_nodes` nodes, numbered `0` to `n_nodes - 1`.
Two of the nodes are bases, one per player. It is generated as follows:

1. Draw `n_nodes` uniformly from 10 to 14 (inclusive).
2. **Spanning tree.** Put the node ids in a uniformly random order `p[0], ..., p[n_nodes - 1]`.
   For `k = 1, ..., n_nodes - 1`, add the edge between `p[k]` and `p[j]`, with `j` drawn
   uniformly from `0, ..., k - 1`. This makes the graph connected.
3. **Extra edges.** Add `round(EXTRA_EDGE_FRACTION * n_nodes)` more edges (4 to 6). Each one
   connects a uniformly drawn pair of distinct nodes that are not connected yet.
4. **Bases.** Draw the base of player 0 uniformly from all nodes. Draw the base of player 1
   uniformly from the nodes whose shortest-path distance (in edges) from player 0's base is at
   least `MIN_BASE_DISTANCE` (3). If there is no such node, discard the graph and start again at
   step 1.
5. **Capacities.** Every edge gets a capacity drawn uniformly from the integers 1 to 4
   (`CAPACITY_RANGE`), independently per edge. The capacity of an edge is the same for both
   players and both directions (see "Action").

There are no self-loops and no duplicate edges. The map is not symmetric: one base may be central,
the other at the end of a long branch, and one base may have only edges of capacity 1. Over a
tournament this evens out, because every pairing plays each seed in both seats (see "Tournament
and ranking").

The map does not change during a match. It is in every observation: `n_nodes`, `edges`,
`edge_capacity`, `player_base`, `opponent_base` and `demand`.

## Demand

Every node `i` has a demand `demand[i]`, an integer that is the same on every turn of the match.
Both bases have demand `0`. Every other node has a demand drawn uniformly from the integers 1 to 6
(`DEMAND_RANGE`), independently per node. Demand is not used up: a node pays its demand again on
every turn that someone holds it (see "Scoring").

## Units

Each player has 15 units, and all of them start at their base. Units are never created or
destroyed. Any number of units, from either player, may be at the same node. Units may move onto
any node, including the opponent's base. A unit that is not moved costs nothing.

## Your strategy

A strategy is a Python class with two methods:

```python
class MyStrategy:
    def __init__(self, player_id: int) -> None:
        self.player_id = player_id  # 0 or 1

    def act(self, obs: dict) -> dict:
        return {"moves": []}  # this turn's moves; see "Observation" and "Action"
```

- A new instance is created for every match, with `player_id` 0 or 1.
- `act` is called once per turn, 80 times per match. You can keep state on `self` between turns.
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
2. each returns its action (a dict with a list of moves),
3. both players' moves are applied, then that turn's profit is paid out (see "Scoring"), then the
   turn number goes up by one.

Moves are simultaneous: neither player sees the other's moves for this turn before choosing its
own. Each player only moves its own units and uses only its own share of the edge capacities, so
the order in which the two players' moves are applied makes no difference.

**Each unit moves at most one edge per turn.** A move can only use units that were at `from_node`
at the start of the turn. Units that arrive at a node during a turn cannot move on until the next
turn.

The payout of a turn uses the unit positions after that turn's moves.

## Observation

Each turn your strategy's `act(obs)` receives a `dict`:

```python
{
    "turn": int,  # current turn, starting at 0
    "n_nodes": int,  # number of nodes; node ids are 0 .. n_nodes - 1
    "edges": list[tuple[int, int]],  # every undirected edge once, as (u, v) with u < v, sorted
    "edge_capacity": list[int],  # same length as edges: edge_capacity[k] is the capacity of edges[k]
    "player_base": int,  # node id of your base
    "opponent_base": int,  # node id of the opponent's base
    "demand": list[int],  # length n_nodes, index = node id: constant demand (0 on both bases)
    "player_units": list[int],  # length n_nodes, index = node id: your units at that node
    "opponent_units": list[int],  # length n_nodes, index = node id: opponent's units at that node
    "player_profit_last_turn": float,  # your profit in the last turn (can be negative)
    "opponent_profit_last_turn": float,  # the opponent's profit in the last turn
    "player_cumulative_profit": float,  # your total profit so far
    "opponent_cumulative_profit": float,  # the opponent's total profit so far
    "n_invalid_actions_last_turn": int,  # number of your illegal moves in the last turn
    "action_format_correct_last_turn": bool,  # whether your last action was well-formed
}
```

You see the whole map, all capacities, all demands, the positions of all units and both players'
profits. The total number of turns is not in the observation (see "End of game").

On turn 0 there is no last turn yet: all `*_profit_last_turn` and `*_cumulative_profit` fields are
`0.0`, `n_invalid_actions_last_turn` is `0`, and `action_format_correct_last_turn` is `True`.

## Action

Terms used below: an **action** is what `act` returns for one turn. It is a dict with exactly one
key, `"moves"`, whose value is a list of **moves**:

```python
{"moves": [[from_node, to_node, count], ...]}
```

- Each move sends `count` of your units from `from_node` to `to_node` along one edge.
- `{"moves": []}` means "do nothing". Units that are not moved stay where they are.
- Moves in the list are applied in order.

**Edge capacity.** Each turn, you may move at most `edge_capacity[k]` of your units across edge
`edges[k]`, counting both directions together. The opponent has its own, separate allowance on
every edge. The allowance is reset at the start of every turn. For example, on an edge of
capacity 3 between nodes 4 and 7, `[[4, 7, 2], [7, 4, 1]]` uses the whole capacity, and a further
`[4, 7, 1]` in the same turn would be illegal.

Two things can go wrong, and next turn's observation reports them separately.

**Malformed action:** the whole action is ignored for that turn, as if you had returned
`{"moves": []}`. Next turn, `action_format_correct_last_turn` is `False` and
`n_invalid_actions_last_turn` is `0`, because no move was looked at. You pay nothing for a
malformed action. The format is checked strictly:

- the action must be a `dict` (not `None`, not a list, not any other type),
- it must have exactly one key, the string `"moves"`; any other or additional key is malformed,
- the value of `"moves"` must be a `list` (not a tuple),
- each move must be a `list` of exactly 3 items (a tuple like `(0, 1, 5)` is malformed),
- each item must be a plain Python `int`. `bool` values, floats and numpy integers such as
  `np.int64` are malformed. Convert them with `int(...)`.

`None` is always malformed, so returning `None` is a safe no-op.

**Illegal move:** the action is well-formed, but one move breaks a rule. Only that move is
skipped (it moves nothing, costs nothing and uses no capacity), the rest of the list still
applies, and it adds 1 to `n_invalid_actions_last_turn`. A move is illegal if:

- `from_node` or `to_node` is not a node id (`0` to `n_nodes - 1`),
- `from_node` and `to_node` are not connected by an edge (this includes `from_node == to_node`),
- `count` is negative,
- `count` is more than the units you still have at `from_node` from the start of the turn, after
  earlier moves in the same list (see "Turn order"),
- `count` is more than the capacity of that edge you still have this turn, after earlier moves in
  the same list. A move is never split: `[0, 5, 3]` on an edge with 2 capacity left is illegal as
  a whole and moves nothing.

A move with `count` 0 is legal, does nothing and costs nothing.

## Scoring

After both players' moves are applied, each player's profit for the turn is its **revenue** minus
its **move cost**.

**Revenue.** For every node `i`, compare the two players' units on it after this turn's moves:

```
if player_units[i] > opponent_units[i]:   you earn demand[i]
if player_units[i] < opponent_units[i]:   the opponent earns demand[i]
if player_units[i] == opponent_units[i]:  nobody earns at node i this turn
```

The winner of a node takes its whole demand; the size of the majority does not matter (1 unit
against 0 earns as much as 9 against 2). Bases have demand 0, so they never pay.

**Move cost.** You pay `MOVE_COST = 0.5` for every unit moved by a legal move this turn:

```
move_cost  = MOVE_COST * (sum of count over your legal moves this turn)
profit     = revenue - move_cost
```

Profit can be negative. Your score in a match is the sum of your profits over all turns. A player
that never moves scores exactly `0`. All profits are multiples of 0.5 and are exact floats.

### Worked example: turns 0 to 2

This is a real map (`n_nodes = 10`). Player 0's observation on turn 0 is:

```python
{
    "turn": 0,
    "n_nodes": 10,
    "edges": [(0, 4), (0, 5), (0, 7), (1, 9), (2, 4), (2, 5), (2, 8), (3, 6), (3, 7), (3, 9),
              (5, 7), (5, 9), (6, 9)],
    "edge_capacity": [3, 2, 2, 3, 2, 4, 4, 3, 3, 3, 4, 2, 4],
    "player_base": 0,
    "opponent_base": 1,
    "demand": [0, 0, 6, 6, 4, 6, 5, 2, 4, 3],
    "player_units": [15, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "opponent_units": [0, 15, 0, 0, 0, 0, 0, 0, 0, 0],
    "player_profit_last_turn": 0.0,
    "opponent_profit_last_turn": 0.0,
    "player_cumulative_profit": 0.0,
    "opponent_cumulative_profit": 0.0,
    "n_invalid_actions_last_turn": 0,
    "action_format_correct_last_turn": True,
}
```

Player 1's observation is the same, except that `player_base` is `1`, `opponent_base` is `0`,
and the two unit lists are swapped.

- Player 0 plays `{'moves': [[0, 4, 3], [0, 5, 2], [0, 5, 1]]}`.
- Player 1 plays `{'moves': [[1, 9, 3], [9, 3, 1]]}`.

Player 0's third move is illegal: edge (0, 5) has capacity 2, and the second move used it up.
Player 1's second move is illegal: node 9 had no units of player 1 at the start of the turn (the
3 units that just arrived cannot move on). Each player has 1 illegal move. After the moves, player
0 holds node 4 (3 units against 0, demand 4) and node 5 (2 against 0, demand 6), and player 1
holds node 9 (3 against 0, demand 3):

| player | revenue    | units moved | move cost | profit |
|-------:|-----------:|------------:|----------:|-------:|
| P0     | 4 + 6 = 10 | 5           | 2.5       | 7.5    |
| P1     | 3          | 3           | 1.5       | 1.5    |

Player 0's observation on turn 1 is then:

```python
{
    "turn": 1,
    "n_nodes": 10,
    "edges": [(0, 4), (0, 5), (0, 7), (1, 9), (2, 4), (2, 5), (2, 8), (3, 6), (3, 7), (3, 9),
              (5, 7), (5, 9), (6, 9)],
    "edge_capacity": [3, 2, 2, 3, 2, 4, 4, 3, 3, 3, 4, 2, 4],
    "player_base": 0,
    "opponent_base": 1,
    "demand": [0, 0, 6, 6, 4, 6, 5, 2, 4, 3],
    "player_units": [10, 0, 0, 0, 3, 2, 0, 0, 0, 0],
    "opponent_units": [0, 12, 0, 0, 0, 0, 0, 0, 0, 3],
    "player_profit_last_turn": 7.5,
    "opponent_profit_last_turn": 1.5,
    "player_cumulative_profit": 7.5,
    "opponent_cumulative_profit": 1.5,
    "n_invalid_actions_last_turn": 1,
    "action_format_correct_last_turn": True,
}
```

On turn 1, player 0 plays `{'moves': [[5, 9, 2]]}` and player 1 plays `{'moves': [[9, 5, 1]]}`.
The two players cross on edge (5, 9); each uses only its own capacity of that edge, so both moves
are legal. After the moves, node 9 has 2 units of each player (a tie: nobody earns its 3), node 5
has 1 unit of player 1 and none of player 0 (player 1 earns 6), and player 0 still holds node 4:

| field (player 0's turn-2 observation) | value |
|---|---|
| `player_units` | `[10, 0, 0, 0, 3, 0, 0, 0, 0, 2]` |
| `opponent_units` | `[0, 12, 0, 0, 0, 1, 0, 0, 0, 2]` |
| `player_profit_last_turn` | `3.0` (revenue 4, cost 2 * 0.5) |
| `opponent_profit_last_turn` | `5.5` (revenue 6, cost 1 * 0.5) |
| `player_cumulative_profit` | `10.5` |
| `opponent_cumulative_profit` | `7.0` |
| `n_invalid_actions_last_turn` | `0` |

## End of game

The game ends after 80 turns (turns 0 to 79). Each player's score is the profit it collected; the
higher score wins (see "Tournament and ranking").

Your strategy forfeits the rest of the match if it raises an exception, takes longer than
1 second for one call of `act` (10 seconds for `__init__`), or crashes. From that turn on your
moves are empty: your units stay where they are, still count for the majority on their nodes and
still earn the nodes you hold, and all of it still counts for your score. The match always plays
on to the end, even if both players have forfeited.

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
