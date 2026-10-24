# Game Theta

## Overview

Two players compete for demand on a graph over a fixed number of turns. Each turn, both players
move units between nodes. Each player has two kinds of units: a few **heavy** units with a large
capacity and a high upkeep, and more **light** units with a small capacity and a low upkeep.
Units at a node serve that node's demand; when both players have units on the same node, the side
with **more capacity serves first** and the other side only gets what is left. Every unit that is
away from its home base costs upkeep each turn. A player's profit is the demand it serves minus
the upkeep it pays, summed over the whole game. The player with the higher profit wins the match
(see "Tournament and ranking").

Two things make the demand uncertain:

- **Hidden demand.** You only see the demand of nodes where you have units and of the nodes next
  to them. The opponent's units are always visible.
- **Surges.** Every node has a fixed base demand, but each turn every node can start a random
  surge that triples its demand for three turns.

What changes from match to match: the map (a different random graph with randomly placed bases
every match, given in the observation), the base demand of every node and the surges.

This document is the complete definition of the game. You will not get the game's source code or
a simulator, so everything needed to write a strategy, or to build your own simulator, is here.
Your deliverable is one Python class, described in "Your strategy".

## Constants

Every constant in this document, as Python you can copy:

```python
N_TURNS = 70
UNIT_TYPES = ("heavy", "light")
UNITS_PER_PLAYER = {"heavy": 10, "light": 20}
UNIT_CAPACITY = {"heavy": 8, "light": 3}
UNIT_UPKEEP = {"heavy": 3.0, "light": 1.0}  # per unit away from its own base, per turn

# map generator (see "Map")
N_NODES_RANGE = (12, 16)  # inclusive
EXTRA_EDGE_FRACTION = 0.4
MIN_BASE_DISTANCE = 3

# demand (see "Demand")
BASE_DEMAND_MEDIAN = 3.0
BASE_DEMAND_SIGMA = 0.7
BASE_DEMAND_CLIP = (0.5, 20.0)
SURGE_PROBABILITY = 0.1  # per node and turn
SURGE_MULTIPLIER = 3
SURGE_DURATION = 3  # turns, including the turn it starts
```

## Seeds

Each match is created from an integer seed. The seed fixes the map, the bases, the base demand
and every surge of every turn, before the first turn is played. Nothing about the environment
depends on what the players do. In a tournament both players of a match, and every pairing that
plays the same seed, face exactly the same map and demand.

You cannot reproduce a match's map or demand from its seed: the generator below is specified
exactly in distribution, not in its random number calls. Use it to generate maps and demand of
your own, for example to test a strategy.

## Map

The map is an undirected, connected graph with `n_nodes` nodes, numbered `0` to `n_nodes - 1`.
Two of the nodes are bases, one per player. It is generated as follows:

1. Draw `n_nodes` uniformly from 12 to 16 (inclusive).
2. **Spanning tree.** Put the node ids in a uniformly random order `p[0], ..., p[n_nodes - 1]`.
   For `k = 1, ..., n_nodes - 1`, add the edge between `p[k]` and `p[j]`, with `j` drawn
   uniformly from `0, ..., k - 1`. This makes the graph connected.
3. **Extra edges.** Add `round(EXTRA_EDGE_FRACTION * n_nodes)` more edges (5 or 6; the value is
   never a tie). Each one connects a uniformly drawn pair of distinct nodes that are not yet
   joined by an edge (draw a pair, and draw again if the edge already exists).
4. **Bases.** Draw the base of player 0 uniformly from all nodes. Draw the base of player 1
   uniformly from the nodes whose shortest-path distance (in edges) from player 0's base is at
   least `MIN_BASE_DISTANCE` (3). If there is no such node, discard the graph and start again at
   step 1.

So no node is next to both bases. The map is not symmetric: one base may be central and close to
the rich nodes, the other at the end of a long branch. Over a tournament this evens out, because
every pairing plays each seed in both seats (see "Tournament and ranking").

There are no self-loops and no duplicate edges. The map does not change during a match. It is in
every observation: `n_nodes`, `edges`, `player_base` and `opponent_base`.

## Demand

Each turn `t` (from `0` to `N_TURNS - 1 = 69`), every node `i` has a demand `demand[t][i] ≥ 0`.
Both bases have demand `0.0` on every turn and never surge.

### Base demand

Every other node `i` has a **base demand** `base[i]`, drawn once at the start of the match and
fixed for the whole match:

```
g_i     ~ N(0, 1)                                                  # independent per node
base[i] = round(clip(BASE_DEMAND_MEDIAN * exp(BASE_DEMAND_SIGMA * g_i), 0.5, 20.0), 1)
```

`clip(x, lo, hi)` is `min(max(x, lo), hi)`; rounding is to one decimal (Python's `round`). So
the base demand has median 3.0 and mean about 3.8; most nodes are between 1 and 8, a few are
richer. Base demands are independent of the map: a rich node can be anywhere.

### Surges

Each node `i` (not a base) has a surge counter `surge_turns_left[t][i]`, an integer from 0 to 3:
the number of turns, including turn `t`, that the node's current surge still lasts (`0` = no
surge). Before turn 0 every counter is 0. For every turn `t = 0, 1, ..., 69` and every non-base
node `i`, independently:

```
if random() < SURGE_PROBABILITY:                  # a new surge starts this turn (10 %)
    surge_turns_left[t][i] = SURGE_DURATION      # 3
else:
    surge_turns_left[t][i] = max(surge_turns_left[t - 1][i] - 1, 0)   # 0 before turn 0
```

A surge that starts on turn `s` covers turns `s`, `s + 1` and `s + 2` (counter 3, 2, 1). A new
surge on a node that is already surging restarts the counter at 3; surges never stack.

```
demand[t][i] = round(SURGE_MULTIPLIER * base[i], 1)   if surge_turns_left[t][i] > 0
demand[t][i] = base[i]                                otherwise
```

On any turn from turn 2 on, a node is surging with probability `1 - 0.9**3 = 0.271`, so its
average demand over a match is about `(1 + 2 * 0.271) * base[i] = 1.54 * base[i]`. Surges are
independent between nodes and turns: a surge cannot be predicted before it starts, but once it
has started you know (if you see the node) exactly how long it lasts.

## Units

Each player has two unit types, and all units start at their own base:

| type    | units per player | capacity | upkeep per turn when deployed |
|---------|-----------------:|---------:|------------------------------:|
| `heavy` |               10 |        8 |                           3.0 |
| `light` |               20 |        3 |                           1.0 |

Units are never created or destroyed. Any number of units, of both types and from both players,
may be at the same node. Units may move onto any node, including the opponent's base.

A unit is **deployed** when it is not on its own player's base. Every deployed unit costs its
upkeep every turn (see "Scoring"), including units on the opponent's base and on nodes without
demand. Units on their own base cost nothing and earn nothing.

## Your strategy

A strategy is a Python class with two methods:

```python
class MyStrategy:
    def __init__(self, player_id: int) -> None:
        self.player_id = player_id  # 0 or 1

    def act(self, obs: dict) -> list[tuple[int, int, str, int]]:
        return []  # this turn's moves; see "Observation" and "Action"
```

- A new instance is created for every match, with `player_id` 0 or 1.
- `act` is called once per turn, 70 times per match. You can keep state on `self` between turns,
  for example the demand of nodes you saw earlier.
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
2. each returns its action (a list of moves),
3. both players' moves are applied, then that turn's profit is paid out (see "Scoring"), then
   the turn number goes up by one.

Moves are simultaneous: neither player sees the other's moves for this turn before choosing its
own. Each player only moves its own units, so the order in which the two players' moves are
applied makes no difference.

**Each unit moves at most one edge per turn**, heavy and light alike. A move can only use units
that were at `from_node` at the start of the turn. Units that arrive at a node during a turn
cannot move on until the next turn. For example, if your base is node 8 and it is connected to
node 0, which is connected to node 3, then `[(8, 0, "light", 5), (0, 3, "light", 5)]` from the
start position moves 5 light units to node 0, and the second move is illegal.

The payout of turn `t` uses `demand[t]`, the demand of that same turn, and the unit positions
after that turn's moves.

## Visibility

A node is **visible** to you on turn `t` if, at the start of that turn (before the moves), you
have at least one unit (of either type) on it or on a node adjacent to it. Your base is visible
whenever you have a unit on it or next to it. On turn 0 you see your base and its neighbours.

For every visible node the observation gives its demand of the current turn and its surge
counter; for every other node both are `None`. Every node a unit of yours can reach with this
turn's move is visible, so you always know the exact demand you will be paid on at the nodes you
can move to (but not how many units the opponent will bring).

You always see the whole map and every unit of the opponent, wherever it is.

## Observation

Each turn your strategy's `act(obs)` receives a `dict`:

```python
{
    "turn": int,  # current turn, starting at 0
    "n_nodes": int,  # number of nodes; node ids are 0 .. n_nodes - 1
    "edges": list[tuple[int, int]],  # every undirected edge once, as (u, v) with u < v, sorted
    "player_base": int,  # node id of your base
    "opponent_base": int,  # node id of the opponent's base
    "demand": list[float | None],  # length n_nodes: demand[t][i] if node i is visible, else None
    "surge_turns_left": list[int | None],  # length n_nodes: surge counter if visible, else None
    "player_heavy": list[int],  # length n_nodes, index = node id: your heavy units there
    "player_light": list[int],  # length n_nodes: your light units there
    "opponent_heavy": list[int],  # length n_nodes: the opponent's heavy units there
    "opponent_light": list[int],  # length n_nodes: the opponent's light units there
    "player_profit_last_turn": float,  # your profit in the last turn (can be negative)
    "opponent_profit_last_turn": float,  # the opponent's profit in the last turn
    "player_cumulative_profit": float,  # your total profit so far
    "opponent_cumulative_profit": float,  # the opponent's total profit so far
    "n_invalid_actions_last_turn": int,  # number of your illegal moves in the last turn
    "action_format_correct_last_turn": bool,  # whether your last action was well-formed
}
```

- `demand[i]` is the demand of the **current turn** (the one your moves of this turn are paid on)
  for visible nodes. It is `base[i]` when `surge_turns_left[i] == 0` and
  `round(3 * base[i], 1)` when `surge_turns_left[i] > 0`, so a visible node always tells you its
  base demand. Both bases show `0.0` and `0` when visible.
- `surge_turns_left[i]` (visible nodes) counts the current turn: `3` means the surge started this
  turn and also covers the next two turns, `1` means this is its last turn, `0` means no surge.
  It does not tell you about surges that will start later.
- There is no forecast: you do not see the demand of future turns, nor the demand of nodes you do
  not see.

You see the whole map, the positions of all units and both players' profits. The total number of
turns is not in the observation (see "End of game").

On turn 0 there is no last turn yet: all `*_profit_last_turn` and `*_cumulative_profit` fields
are `0.0`, `n_invalid_actions_last_turn` is `0`, and `action_format_correct_last_turn` is `True`.

## Action

Terms used below: an **action** is what `act` returns for one turn. It is a list of **moves**.

```python
[(from_node, to_node, unit_type, count), ...]
```

- Each move sends `count` of your units of type `unit_type` (`"heavy"` or `"light"`) from
  `from_node` to `to_node` along one edge.
- An empty list means "do nothing". Units that are not moved stay where they are.
- Moves in the list are applied in order.

Two things can go wrong, and next turn's observation reports them separately.

**Malformed action:** the whole action is ignored for that turn, as if you had returned `[]`.
Next turn, `action_format_correct_last_turn` is `False` and `n_invalid_actions_last_turn` is `0`,
because no move was looked at. Returning `None` is a malformed action, so it is always a safe
no-op. The format is checked strictly:

- the action must be a `list` (not a tuple, not `None`, not any other type),
- each move must be a `tuple` of exactly 4 items (a list like `[0, 1, "light", 5]` is malformed),
- `from_node`, `to_node` and `count` must be plain Python `int`s. `bool` values, floats and numpy
  integers such as `np.int64` are malformed. Convert them with `int(...)`,
- `unit_type` must be exactly the string `"heavy"` or `"light"` (`"Heavy"`, `"h"` or `0` are
  malformed).

**Illegal move:** the action is well-formed, but one move breaks a rule. Only that move is
skipped, the rest of the list still applies, and it adds 1 to `n_invalid_actions_last_turn`. A
move is illegal if:

- `from_node` or `to_node` is not a node id (`0` to `n_nodes - 1`),
- `from_node` and `to_node` are not connected by an edge (this includes `from_node == to_node`),
- `count` is negative,
- `count` is more than the units of that type you still have at `from_node` from the start of the
  turn, after earlier moves in the same list (see "Turn order").

A move with `count` 0 between two connected nodes is legal and does nothing. Each illegal move
adds exactly 1, however many of these rules it breaks. There is no limit on the number of moves
in an action, and several moves may use the same `from_node` and `to_node`.

## Scoring

After all moves are applied, each player's profit for the turn is its **revenue** minus its
**upkeep**.

**Revenue.** At each node, each player's capacity is

```
capacity_p = 8 * heavy_p + 3 * light_p          # that player's units at the node
```

and the node's demand `d` of this turn is split between the players:

```
if capacity_0 == capacity_1:          # equal capacity (also: nobody there): split evenly
    served_0 = served_1 = min(capacity_0, d / 2)
else:                                 # the side with more capacity serves first
    big, small = the player with more capacity, the other player
    served_big   = min(capacity_big, d)
    served_small = min(capacity_small, d - served_big)
```

A player alone on a node is the "big" side and serves `min(capacity, d)`. A node with demand 0
or no units contributes nothing. Your revenue for a turn is the sum of your `served` over all
nodes. Units of the same player at different nodes never combine. Served amounts, revenue,
upkeep and profit are never rounded; only demand is (see "Demand").

**Upkeep.** You pay the upkeep of each of your units that is not on your own base after this
turn's moves:

```
upkeep_p = 3.0 * (10 - heavy_p at own base) + 1.0 * (20 - light_p at own base)
profit_p = revenue_p - upkeep_p
```

Profit can be negative. Your score in a match is the sum of your profits over all turns. A player
that never moves scores exactly `0`.

For example:

- Demand 3.9, alone: 1 light unit serves `min(3, 3.9) = 3`, profit `3 - 1.0 = 2.0`. 1 heavy unit
  serves `3.9`, profit `3.9 - 3.0 = 0.9`. Light units earn more per unit of capacity when the
  demand is small.
- Demand 10.0: your 1 heavy (capacity 8) against the opponent's 2 light (capacity 6). You have
  more capacity and serve `min(8, 10) = 8`; the opponent serves `min(6, 10 - 8) = 2`. From this
  node your profit is `8 - 3.0 = 5.0`, the opponent's `2 - 2.0 = 0.0`.
- Demand 10.0, and the opponent adds a third light unit (capacity 9 against your 8): now the
  opponent serves first, `min(9, 10) = 9`, and you get `min(8, 10 - 9) = 1`. Profits: opponent
  `9 - 3.0 = 6.0`, you `1 - 3.0 = -2.0`. One unit of capacity more decides who serves first.
- Demand 9.0, 3 light units each (capacity 9 each): equal capacity, each serves
  `min(9, 4.5) = 4.5`, profit `4.5 - 3.0 = 1.5` each.
- Demand 23.4 (a surge of base 7.8), 1 heavy each (capacity 8 each): each serves
  `min(8, 11.7) = 8`, profit `5.0` each.

### Worked example: turns 0 and 1

This is a real map (`n_nodes = 12`). Player 0's observation on turn 0 is:

```python
{
    "turn": 0,
    "n_nodes": 12,
    "edges": [
        (0, 3),
        (0, 4),
        (0, 8),
        (0, 9),
        (1, 2),
        (1, 10),
        (2, 3),
        (2, 9),
        (3, 7),
        (3, 10),
        (5, 8),
        (5, 11),
        (6, 9),
        (8, 11),
        (9, 11),
        (10, 11),
    ],
    "player_base": 8,
    "opponent_base": 6,
    "demand": [6.6, None, None, None, None, 2.0, None, None, 0.0, None, None, 11.7],
    "surge_turns_left": [3, None, None, None, None, 0, None, None, 0, None, None, 3],
    "player_heavy": [0, 0, 0, 0, 0, 0, 0, 0, 10, 0, 0, 0],
    "player_light": [0, 0, 0, 0, 0, 0, 0, 0, 20, 0, 0, 0],
    "opponent_heavy": [0, 0, 0, 0, 0, 0, 10, 0, 0, 0, 0, 0],
    "opponent_light": [0, 0, 0, 0, 0, 0, 20, 0, 0, 0, 0, 0],
    "player_profit_last_turn": 0.0,
    "opponent_profit_last_turn": 0.0,
    "player_cumulative_profit": 0.0,
    "opponent_cumulative_profit": 0.0,
    "n_invalid_actions_last_turn": 0,
    "action_format_correct_last_turn": True,
}
```

Player 0 sees its base 8 and its neighbours 0, 5 and 11. Nodes 0 and 11 both started a surge on
this turn (counter 3), so their base demands are `6.6 / 3 = 2.2` and `11.7 / 3 = 3.9`; node 5 is
not surging (base 2.0). Player 1's observation shows its own base 6 and its only neighbour 9
(demand `2.0`, counter `0`) and `None` everywhere else, with the `player_*` and `opponent_*`
fields swapped.

- Player 0 plays `[(8, 11, "heavy", 1), (8, 11, "light", 2), (8, 0, "light", 2), (8, 5, "light", 1), (8, 6, "light", 1)]`.
- Player 1 plays `[(6, 9, "light", 1)]`.

Player 0's last move is illegal (nodes 8 and 6 are not connected): it is skipped, that light unit
stays on base 8, and the other four moves apply. No node is contested. The payout uses the
turn-0 demand:

| node | demand | units          | capacity | served |
|-----:|-------:|----------------|---------:|-------:|
| 0    | 6.6    | P0: 2 light    | 6        | 6.0    |
| 5    | 2.0    | P0: 1 light    | 3        | 2.0    |
| 11   | 11.7   | P0: 1 heavy, 2 light | 14 | 11.7   |
| 9    | 2.0    | P1: 1 light    | 3        | 2.0    |

| player | revenue | deployed          | upkeep              | profit |
|-------:|--------:|-------------------|--------------------:|-------:|
| P0     | 19.7    | 1 heavy, 5 light  | 3.0 + 5.0 = 8.0     | 11.7   |
| P1     | 2.0     | 1 light           | 1.0                 | 1.0    |

On turn 1, player 0 has units on nodes 0, 5, 8 and 11, so it now also sees nodes 3, 4, 9 and 10.
Node 7 started a surge on turn 1 (its demand is 9.6), but no unit of player 0 is next to it, so
player 0 does not see it. Player 0's observation on turn 1 is:

```python
{
    "turn": 1,
    "n_nodes": 12,
    "edges": [
        (0, 3),
        (0, 4),
        (0, 8),
        (0, 9),
        (1, 2),
        (1, 10),
        (2, 3),
        (2, 9),
        (3, 7),
        (3, 10),
        (5, 8),
        (5, 11),
        (6, 9),
        (8, 11),
        (9, 11),
        (10, 11),
    ],
    "player_base": 8,
    "opponent_base": 6,
    "demand": [6.6, None, None, 1.6, 7.7, 2.0, None, None, 0.0, 2.0, 9.8, 11.7],
    "surge_turns_left": [2, None, None, 0, 0, 0, None, None, 0, 0, 0, 2],
    "player_heavy": [0, 0, 0, 0, 0, 0, 0, 0, 9, 0, 0, 1],
    "player_light": [2, 0, 0, 0, 0, 1, 0, 0, 15, 0, 0, 2],
    "opponent_heavy": [0, 0, 0, 0, 0, 0, 10, 0, 0, 0, 0, 0],
    "opponent_light": [0, 0, 0, 0, 0, 0, 19, 0, 0, 1, 0, 0],
    "player_profit_last_turn": 11.7,
    "opponent_profit_last_turn": 1.0,
    "player_cumulative_profit": 11.7,
    "opponent_cumulative_profit": 1.0,
    "n_invalid_actions_last_turn": 1,
    "action_format_correct_last_turn": True,
}
```

The surges on nodes 0 and 11 are in their second turn (counter 2) and will end after turn 2:
from turn 3 on these nodes are back at their base demand unless a new surge starts. Node 10
(base 9.8) and node 4 (base 7.7) are rich nodes player 0 has just discovered.

Profits are Python floats and can carry rounding error in their last digits (for example
`11.700000000000001`); compare them with a tolerance.

## End of game

The game ends after 70 turns (turns 0 to 69). Each player's score is the profit it collected;
the higher score wins (see "Tournament and ranking").

Your strategy forfeits the rest of the match if it raises an exception, takes longer than
1 second for one call of `act` (10 seconds for `__init__`), or crashes. From that turn on your
moves are empty: your units stay where they are, still take their share of each node's demand
and still cost upkeep, and both still count for your score. The match always plays on to the
end, even if both players have forfeited.

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
