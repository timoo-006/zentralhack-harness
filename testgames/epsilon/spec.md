# Game Epsilon

## Overview

Two players compete for territory on a graph over a fixed number of turns. Each turn, both
players send units along the edges of the map. Edges have lengths: a unit needs 1, 2 or 3 turns
to cross one, and is **in transit** in the meantime. A player who has strictly more units on a
node than the opponent at the end of two consecutive turns **captures** it. A node you own pays
you its value every turn, even when none of your units is on it, until the opponent captures it.
Every unit that is away from its home base, travelling or standing on a node, costs upkeep each
turn. A player's profit is the value of the nodes it owns minus the upkeep it pays, summed over
the whole game. The player with the higher profit wins the match (see "Tournament and ranking").

What changes from match to match:

- **The map.** Every match is played on a different random graph, with random edge lengths, random
  node values and randomly placed bases. You learn it from the observation, at the start of the
  match.

Nothing else is random: node values do not change during a match, and the opponent's units, its
transits and its nodes are always visible.

This document is the complete definition of the game. You will not get the game's source code or
a simulator, so everything needed to write a strategy, or to build your own simulator, is here.
Your deliverable is one Python class, described in "Your strategy".

## Constants

Every constant in this document, as Python you can copy:

```python
N_TURNS = 80
UNITS_PER_PLAYER = 20
UPKEEP = 2.0  # per unit away from its own base (in transit included), per turn
CAPTURE_TURNS = 2  # consecutive turn ends with a strict majority on a node to capture it

# map generator (see "Map")
N_NODES_RANGE = (10, 14)  # inclusive
EXTRA_EDGE_FRACTION = 0.4
EDGE_LENGTH_RANGE = (1, 3)  # inclusive, uniform integer, drawn per edge
MIN_BASE_DISTANCE = 3  # in edges, not in turns

# node values (see "Node values")
VALUE_RANGE = (1, 6)  # inclusive, uniform integer, drawn per non-base node
```

## Seeds

Each match is created from an integer seed. The seed fixes the map, the edge lengths, the bases
and the node values, before the first turn is played. Nothing about the environment depends on
what the players do. In a tournament both players of a match, and every pairing that plays the
same seed, face exactly the same map.

You cannot reproduce a match's map from its seed: the generator below is specified exactly in
distribution, not in its random number calls. Use it to generate maps of your own, for example to
test a strategy.

## Map

The map is an undirected, connected graph with `n_nodes` nodes, numbered `0` to `n_nodes - 1`.
Every edge has an integer **length** from 1 to 3: the number of turns a unit needs to cross it.
Two of the nodes are bases, one per player. The map is generated as follows:

1. Draw `n_nodes` uniformly from 10 to 14 (inclusive).
2. **Spanning tree.** Put the node ids in a uniformly random order `p[0], ..., p[n_nodes - 1]`.
   For `k = 1, ..., n_nodes - 1`, add the edge between `p[k]` and `p[j]`, with `j` drawn
   uniformly from `0, ..., k - 1`. This makes the graph connected.
3. **Extra edges.** Add `round(EXTRA_EDGE_FRACTION * n_nodes)` more edges (4 to 6; the value is
   never a tie). Each one connects a uniformly drawn pair of distinct nodes that are not
   connected yet.
4. **Lengths.** Give every edge a length drawn uniformly from 1, 2 and 3, independently.
5. **Bases.** Draw the base of player 0 uniformly from all nodes. Draw the base of player 1
   uniformly from the nodes whose shortest-path distance **in edges** (ignoring lengths) from
   player 0's base is at least `MIN_BASE_DISTANCE` (3). If there is no such node, discard the
   graph and start again at step 1.

There are no self-loops and no duplicate edges. Both directions of an edge have the same length.
Because of the lengths, the path with the fewest edges is often not the fastest one: the
**travel time** between two nodes is the smallest sum of edge lengths over all paths between
them (a weighted shortest path, e.g. Dijkstra's algorithm). The two bases are at least 3 edges and
so at least 3 turns apart, and usually more.

The map is not symmetric: one base may be central and close to the valuable nodes, the other at
the end of a long branch. Over a tournament this evens out, because every pairing plays each seed
in both seats (see "Tournament and ranking").

The map does not change during a match. It is in every observation: `n_nodes`, `edges` (with
their lengths), `player_base` and `opponent_base`.

## Node values

Every node except the two bases has a **value**, an integer drawn uniformly from 1 to 6
(inclusive), independently per node. Both bases have value `0`. The values are fixed for the
whole match and are in every observation (`node_value`). An owned node pays its owner its value
every turn (see "Ownership" and "Scoring").

## Units

Each player has `UNITS_PER_PLAYER = 20` units, and all of them start at their base. Units are
never created or destroyed, so each player has exactly 20 units for the whole game: on nodes
(including its base) or in transit. Any number of units, from either player, may be at the same
node or on the same edge. Units may move onto any node, including the opponent's base. Units never
fight: they only count for majorities (see "Ownership").

**Moving.** A move sends units from a node along one edge to a neighbouring node. A unit sent on
turn `t` along an edge of length `L` is in transit until it lands on the destination node at the
end of turn `t + L - 1`. It is on that node from turn `t + L` on, which is its **arrival turn**:
the first turn in whose observation it appears on the node, and the first turn on which it can
be moved again. For example, with an edge of length 3 sent on turn 10:

| end of turn | where the unit is                                  |
|------------:|----------------------------------------------------|
| 10          | in transit, `arrival_turn` 13                      |
| 11          | in transit, `arrival_turn` 13                      |
| 12          | lands on the destination node: counts there        |
| 13          | on the destination node; it can be moved on turn 13 |

A unit sent along an edge of length 1 lands at the end of the same turn and never shows up in a
transit list. Units in transit cannot be stopped or redirected, and they are not on any node: they
count for no majority. Going over several edges in a row costs no extra time: a unit that lands on
a node can be sent on along the next edge on its arrival turn, so a path takes exactly the sum of
its edge lengths.

**Upkeep.** A unit is **away** when it is not on its own player's base. Every away unit costs
`UPKEEP = 2.0` per turn (see "Scoring"): units in transit, units on nodes you own, on nodes
without value and on the opponent's base all count. Units on their own base cost nothing.

## Ownership

At the start of the match no node is owned. Bases can never be owned or captured.

At the end of every turn, after the units in transit for that turn have landed, each non-base node
is checked. Let `a` and `b` be the numbers of units of the two players on that node (units in
transit do not count):

- If one player has strictly more units there (`a > b` or `b > a`), that player **leads** on the
  node: its **lead count** on that node goes up by 1 and the other player's goes to 0.
- If `a == b` (including no units at all), nobody leads: both lead counts on that node go to 0.
- A player whose lead count on a node reaches `CAPTURE_TURNS = 2` (that is, it led there at the
  end of two consecutive turns) and does not own it yet **captures** it: from then on the node is
  owned by that player, and no longer by the opponent. The lead count keeps counting up while the
  player keeps leading; it has no further effect.

An owned node stays owned, with or without units on it, until the other player captures it. A tie
never changes the owner, so to defend a node it is enough to match the attacker's units there at
the end of every second turn. A capture takes effect at once: the node already pays its new owner
for the turn in which it was captured (see "Turn order"). The observation shows both players'
nodes (`player_owned`, `opponent_owned`) and lead counts (`player_lead_turns`,
`opponent_lead_turns`).

So capturing a node with one unit at travel time `d` from your base and walking it back home costs
`2 * d` turns of upkeep (`4.0 * d`): the unit is away at the end of `d` turns on the way out (it
lands at the end of the `d`-th), 1 more turn to lead a second time, and `d - 1` turns on the way
back (it is home at the end of the last one). The node pays from the turn of the capture on.

## Your strategy

A strategy is a Python class with two methods:

```python
class MyStrategy:
    def __init__(self, player_id: int) -> None:
        self.player_id = player_id  # 0 or 1

    def act(self, obs: dict) -> list[tuple[int, int, int]]:
        return []  # this turn's moves; see "Observation" and "Action"
```

- A new instance is created for every match, with `player_id` 0 or 1.
- `act` is called once per turn, 80 times per match. You can keep state on `self` between turns.
- The map is not known in `__init__`. It is in every observation; a strategy that needs
  precomputed data about the map (travel times, paths) typically builds it on the first call of
  `act` and keeps it on `self`.
- Everything in the observation is from your point of view (`player_*` fields are yours,
  `opponent_*` fields are the opponent's), so you do not need `player_id` to play.
- If `act` raises an exception, takes too long or crashes, you forfeit the rest of the match (see
  "End of game").

## Turn order

Each turn `t`:

1. both players' `act(obs)` are called with the observation for turn `t`,
2. each returns its action (a list of moves),
3. **moves:** both players' moves are applied: the units leave their nodes and go into transit,
   with `arrival_turn = t + length` of the edge,
4. **landing:** every unit in transit with `arrival_turn == t + 1` (both players', including the
   ones just sent along an edge of length 1) lands on its destination node,
5. **ownership:** every non-base node is checked as in "Ownership",
6. **payout:** each player is paid the turn's profit (see "Scoring"),
7. the turn number goes up by one.

Moves are simultaneous: neither player sees the other's moves for this turn before choosing its
own. Each player only moves its own units, so the order in which the two players' moves are applied
makes no difference.

**A move can only use units that were on `from_node` at the start of the turn** (the
`player_units` of the observation), and each unit crosses at most one edge per turn. Units that land
during a turn can be moved again only on the next turn. For example, if your base is node 3 and it
is connected to node 4 (length 1), which is connected to node 8 (length 1), then
`[(3, 4, 5), (4, 8, 5)]` from the start position sends 5 units to node 4, and the second move is
illegal.

The observation of turn `t + 1` shows exactly the state after steps 3 to 6 of turn `t`: units on
nodes, transits, owners and lead counts, and the profit of turn `t`.

## Observation

Each turn your strategy's `act(obs)` receives a `dict`:

```python
{
    "turn": int,  # current turn, starting at 0
    "n_nodes": int,  # number of nodes; node ids are 0 .. n_nodes - 1
    "edges": list[tuple[int, int, int]],  # every undirected edge once, as (u, v, length)
    #                                       with u < v, sorted; length is 1, 2 or 3 turns
    "player_base": int,  # node id of your base
    "opponent_base": int,  # node id of the opponent's base
    "node_value": list[int],  # length n_nodes, index = node id: value per turn; 0 on bases
    "player_units": list[int],  # length n_nodes, index = node id: your units on that node
    "opponent_units": list[int],  # length n_nodes: the opponent's units on that node
    "player_transits": list[tuple[int, int, int, int]],  # your units in transit, see below
    "opponent_transits": list[tuple[int, int, int, int]],  # the opponent's units in transit
    "player_owned": list[bool],  # length n_nodes: True where you own the node
    "opponent_owned": list[bool],  # length n_nodes: True where the opponent owns the node
    "player_lead_turns": list[int],  # length n_nodes: your lead count on each node
    "opponent_lead_turns": list[int],  # length n_nodes: the opponent's lead count
    "player_profit_last_turn": float,  # your profit in the last turn (can be negative)
    "opponent_profit_last_turn": float,  # the opponent's profit in the last turn
    "player_cumulative_profit": float,  # your total profit so far
    "opponent_cumulative_profit": float,  # the opponent's total profit so far
    "n_invalid_actions_last_turn": int,  # number of your illegal moves in the last turn
    "action_format_correct_last_turn": bool,  # whether your last action was well-formed
}
```

**Transits.** Each entry of `player_transits` and `opponent_transits` is a tuple
`(from_node, to_node, count, arrival_turn)`: `count` units that left `from_node` along the edge to
`to_node` and are on `to_node` from turn `arrival_turn` on (they land at the end of the current
turn if `arrival_turn == turn + 1`). Units sent from the same node to the same node with the same
arrival turn form one entry. Entries are sorted by `arrival_turn`, then `from_node`, then
`to_node`. Every entry has `arrival_turn > turn`; `to_node` is the end of that one edge, not the
final goal of the units.

For every player, `sum(player_units)` plus the counts of all its transits is always 20.

**Lead counts.** `player_lead_turns[i]` is the number of consecutive turns, up to and including the
last one, at whose end you had strictly more units on node `i` than the opponent (see "Ownership").
On every node at most one of the two lead counts is non-zero. Both are `0` on the bases.

You see the whole map, all units, all transits, both players' nodes and profits. The total number
of turns is not in the observation (see "End of game").

On turn 0 nothing has happened yet: all units are on their bases, both transit lists are empty, no
node is owned, all lead counts are `0`, all `*_profit_last_turn` and `*_cumulative_profit` fields
are `0.0`, `n_invalid_actions_last_turn` is `0`, and `action_format_correct_last_turn` is `True`.

## Action

Terms used below: an **action** is what `act` returns for one turn. It is a list of **moves**.

```python
[(from_node, to_node, count), ...]
```

- Each move sends `count` of your units from `from_node` along the edge to `to_node`. They are in
  transit for the edge's length (see "Units").
- An empty list means "do nothing". Units that are not moved stay where they are. `None` (or any
  other malformed action) also does nothing.
- Moves in the list are applied in order.

Two things can go wrong, and next turn's observation reports them separately.

**Malformed action:** the whole action is ignored for that turn, as if you had returned `[]`.
Next turn, `action_format_correct_last_turn` is `False` and `n_invalid_actions_last_turn` is `0`,
because no move was looked at. The format is checked strictly:

- the action must be a `list` (not a tuple, not `None`, not any other type),
- each move must be a `tuple` of exactly 3 items (a list like `[0, 1, 5]` is malformed),
- each item must be a plain Python `int`. `bool` values and numpy integers such as `np.int64`
  are malformed. Convert them with `int(...)`.

**Illegal move:** the action is well-formed, but one move breaks a rule. Only that move is
skipped, the rest of the list still applies, and it adds 1 to `n_invalid_actions_last_turn`. A
move is illegal if:

- `from_node` or `to_node` is not a node id (`0` to `n_nodes - 1`),
- `from_node` and `to_node` are not connected by an edge (this includes `from_node == to_node`;
  a move only ever crosses one edge),
- `count` is negative,
- `count` is more than the units you still have on `from_node` from the start of the turn, after
  earlier moves in the same list (see "Turn order"). Units in transit can never be moved.

A move with `count` 0 is legal and does nothing, as long as it breaks none of the other rules: for
example `(from_node, to_node, 0)` between two nodes without an edge is still illegal and counts.

## Scoring

After the moves, the landing and the ownership check, each player's profit for the turn is its
**revenue** minus its **upkeep**:

```
revenue_p = sum of node_value[i] over all nodes i that player p owns
upkeep_p  = UPKEEP * (UNITS_PER_PLAYER - units of p on its own base)    # UPKEEP = 2.0
profit_p  = revenue_p - upkeep_p
```

`UNITS_PER_PLAYER - units on own base` counts every away unit: on other nodes and in transit.
Revenue does not depend on units: an owned node pays its full value whether you have 0 or 10 units
on it. Profit can be negative. Your score in a match is the sum of your profits over all turns. A
player that never moves scores exactly `0`.

For example:

- You own nodes of value 5 and 1, have 2 units on the value-5 node and 1 unit in transit, and 17 on
  your base: profit `6 - 3 * 2.0 = 0.0`.
- You own nothing and have all 20 units out: profit `-40.0`. Sending everything out and leaving
  it there costs `40.0 * 80 = 3200` over a match, more than the values of all nodes of a map
  together usually pay (they add up to about 35 per turn).
- Capturing a value-4 node at travel time 3 with 1 unit and walking it home costs `4.0 * 3 = 12.0`
  of upkeep (see "Ownership"), so it has paid for itself after 3 turns of ownership. Holding the
  same node with a unit standing on it costs `2.0` per turn, half of its value.
- Two of your units on a node you own and two of the opponent's on it: nobody leads, the node
  stays yours and pays you its value, and each player pays `4.0` for those units.

### Worked example: turns 0 to 2

This is a real map (`n_nodes = 10`). Player 0's observation on turn 0 is:

```python
{
    "turn": 0,
    "n_nodes": 10,
    "edges": [
        (0, 4, 3),
        (0, 9, 2),
        (1, 4, 1),
        (2, 3, 3),
        (2, 4, 3),
        (2, 5, 1),
        (3, 4, 1),
        (3, 8, 3),
        (4, 5, 2),
        (4, 8, 1),
        (4, 9, 2),
        (6, 9, 1),
        (7, 8, 1),
    ],
    "player_base": 3,
    "opponent_base": 6,
    "node_value": [1, 1, 6, 0, 5, 1, 0, 4, 3, 1],
    "player_units": [0, 0, 0, 20, 0, 0, 0, 0, 0, 0],
    "opponent_units": [0, 0, 0, 0, 0, 0, 20, 0, 0, 0],
    "player_transits": [],
    "opponent_transits": [],
    "player_owned": [False, False, False, False, False, False, False, False, False, False],
    "opponent_owned": [False, False, False, False, False, False, False, False, False, False],
    "player_lead_turns": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "opponent_lead_turns": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "player_profit_last_turn": 0.0,
    "opponent_profit_last_turn": 0.0,
    "player_cumulative_profit": 0.0,
    "opponent_cumulative_profit": 0.0,
    "n_invalid_actions_last_turn": 0,
    "action_format_correct_last_turn": True,
}
```

Player 1's observation is the same, except that `player_base` is `6`, `opponent_base` is `3`, and
every `player_*` field is swapped with its `opponent_*` field.

On turn 0:

- Player 0 plays `[(3, 4, 2), (3, 8, 1)]`.
- Player 1 plays `[(6, 9, 3)]`.

Edge `(3, 4)` has length 1, so player 0's 2 units land on node 4 at the end of turn 0. Edge
`(3, 8)` has length 3: that unit is in transit with `arrival_turn` `0 + 3 = 3`. Player 1's 3 units
land on node 9 (edge `(6, 9)`, length 1). At the end of turn 0, player 0 leads on node 4 and
player 1 on node 9, both for the first time: no node is owned yet.

| player | revenue | away units | upkeep | profit |
|-------:|--------:|-----------:|-------:|-------:|
| P0     | 0       | 3          | 6.0    | -6.0   |
| P1     | 0       | 3          | 6.0    | -6.0   |

Player 0's observation on turn 1 is then:

```python
{
    "turn": 1,
    "n_nodes": 10,
    "edges": [
        (0, 4, 3),
        (0, 9, 2),
        (1, 4, 1),
        (2, 3, 3),
        (2, 4, 3),
        (2, 5, 1),
        (3, 4, 1),
        (3, 8, 3),
        (4, 5, 2),
        (4, 8, 1),
        (4, 9, 2),
        (6, 9, 1),
        (7, 8, 1),
    ],
    "player_base": 3,
    "opponent_base": 6,
    "node_value": [1, 1, 6, 0, 5, 1, 0, 4, 3, 1],
    "player_units": [0, 0, 0, 17, 2, 0, 0, 0, 0, 0],
    "opponent_units": [0, 0, 0, 0, 0, 0, 17, 0, 0, 3],
    "player_transits": [(3, 8, 1, 3)],
    "opponent_transits": [],
    "player_owned": [False, False, False, False, False, False, False, False, False, False],
    "opponent_owned": [False, False, False, False, False, False, False, False, False, False],
    "player_lead_turns": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0],
    "opponent_lead_turns": [0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    "player_profit_last_turn": -6.0,
    "opponent_profit_last_turn": -6.0,
    "player_cumulative_profit": -6.0,
    "opponent_cumulative_profit": -6.0,
    "n_invalid_actions_last_turn": 0,
    "action_format_correct_last_turn": True,
}
```

On turn 1, player 0 plays `[]` and player 1 plays `[(9, 4, 2)]`: 2 of its units on node 9 set
off towards node 4 (edge `(4, 9)`, length 2, `arrival_turn` 3). At the end of turn 1 player 0
leads on node 4 for the second time and captures it; player 1 still has 1 unit on node 9, leads
there for the second time and captures it. Both captures already pay for turn 1.

On turn 2, both players play `[]`. At the end of turn 2 both transits land (`arrival_turn` 3):
player 0's unit on node 8, where it leads for the first time, and player 1's 2 units on node 4.
Node 4 is now a tie, 2 against 2: nobody leads, both lead counts there go to 0, and node 4 stays
player 0's.

The fields of player 0's observations on turns 2 and 3 that changed (node lists by node id):

| field                        | turn 2 (after turn 1)              | turn 3 (after turn 2)              |
|------------------------------|------------------------------------|------------------------------------|
| `player_units`               | `[0, 0, 0, 17, 2, 0, 0, 0, 0, 0]`  | `[0, 0, 0, 17, 2, 0, 0, 0, 1, 0]`  |
| `opponent_units`             | `[0, 0, 0, 0, 0, 0, 17, 0, 0, 1]`  | `[0, 0, 0, 0, 2, 0, 17, 0, 0, 1]`  |
| `player_transits`            | `[(3, 8, 1, 3)]`                   | `[]`                               |
| `opponent_transits`          | `[(9, 4, 2, 3)]`                   | `[]`                               |
| `player_lead_turns`          | `[0, 0, 0, 0, 2, 0, 0, 0, 0, 0]`   | `[0, 0, 0, 0, 0, 0, 0, 0, 1, 0]`   |
| `opponent_lead_turns`        | `[0, 0, 0, 0, 0, 0, 0, 0, 0, 2]`   | `[0, 0, 0, 0, 0, 0, 0, 0, 0, 3]`   |
| `player_owned`               | `True` on node 4 only              | `True` on node 4 only              |
| `opponent_owned`             | `True` on node 9 only              | `True` on node 9 only              |
| `player_profit_last_turn`    | `-1.0` (5 - 3 * 2.0)               | `-1.0` (5 - 3 * 2.0)               |
| `opponent_profit_last_turn`  | `-5.0` (1 - 3 * 2.0)               | `-5.0` (1 - 3 * 2.0)               |
| `player_cumulative_profit`   | `-7.0`                             | `-8.0`                             |
| `opponent_cumulative_profit` | `-11.0`                            | `-16.0`                            |

If player 1 keeps its 2 units on node 4, it cannot capture node 4 while player 0 keeps 2 there
too, and both pay `4.0` per turn for that standoff. With 3 units on node 4 at the end of two
consecutive turns, it would capture the node.

Profits are Python floats; compare them with a tolerance.

## End of game

The game ends after 80 turns (turns 0 to 79). Each player's score is the profit it collected;
the higher score wins (see "Tournament and ranking"). Units that are away at the end cost nothing
more; nodes owned at the end pay nothing more.

Your strategy forfeits the rest of the match if it raises an exception, takes longer than
1 second for one call of `act` (10 seconds for `__init__`), or crashes. From that turn on your
moves are empty: your units stay where they are, units already in transit still land, your units
still count for majorities and still cost upkeep, the nodes you own still pay you until the
opponent captures them, and all of it still counts for your score. The match always plays on to
the end, even if both players have forfeited.

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
