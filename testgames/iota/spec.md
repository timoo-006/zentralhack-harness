# Game Iota

## Overview

Two players compete for delivery orders on a graph over a fixed number of turns. Orders appear
at random nodes during the match. Each order has a reward and a deadline. At the end of a turn,
an order is fulfilled by the player with more units on its node, who collects the reward (a tie
splits it). An order that nobody fulfils before its deadline expires. Every unit that is away
from its home base costs upkeep each turn. A player's profit is the rewards it collects minus
the upkeep it pays, summed over the whole game. The player with the higher profit wins the
match (see "Tournament and ranking").

What changes from match to match:

- **The map.** Every match is played on a different random graph with randomly placed bases.
  You learn it from the observation, at the start of the match.
- **The orders.** Where, when and how large the orders are is random. You see an order only
  from the turn on which it appears; you never see future orders.

This document is the complete definition of the game. You will not get the game's source code or
a simulator, so everything needed to write a strategy, or to build your own simulator, is here.
Your deliverable is one Python class, described in "Your strategy".

## Constants

Every constant in this document, as Python you can copy:

```python
N_TURNS = 60
UNITS_PER_PLAYER = 12
UPKEEP = 1.0  # per unit away from its own base, per turn

# map generator (see "Map")
N_NODES_RANGE = (9, 13)  # inclusive
EXTRA_EDGE_FRACTION = 0.4
MIN_BASE_DISTANCE = 3

# order generator (see "Orders")
NEW_ORDERS_RANGE = (0, 2)  # inclusive: new orders per turn
REWARD_RANGE = (3, 12)  # inclusive
DEADLINE_RANGE = (2, 6)  # inclusive: turns_left of an order when it appears
```

## Seeds

Each match is created from an integer seed. The seed fixes the map, the bases and every order of
every turn, before the first turn is played. Nothing about the environment depends on what the
players do: an order appears on its turn whatever happened before. In a tournament both players
of a match, and every pairing that plays the same seed, face exactly the same map and orders.

You cannot reproduce a match's map or orders from its seed: the generator below is specified
exactly in distribution, not in its random number calls. Use it to generate maps and orders of
your own, for example to test a strategy.

## Map

The map is an undirected, connected graph with `n_nodes` nodes, numbered `0` to `n_nodes - 1`.
Two of the nodes are bases, one per player. It is generated as follows:

1. Draw `n_nodes` uniformly from 9 to 13 (inclusive).
2. **Spanning tree.** Put the node ids in a uniformly random order `p[0], ..., p[n_nodes - 1]`.
   For `k = 1, ..., n_nodes - 1`, add the edge between `p[k]` and `p[j]`, with `j` drawn
   uniformly from `0, ..., k - 1`. This makes the graph connected.
3. **Extra edges.** Add `round(EXTRA_EDGE_FRACTION * n_nodes)` more edges (4 to 5). Each one
   connects a uniformly drawn pair of distinct nodes that are not connected yet. (Python's
   `round` rounds `0.4 * 10 = 4.0` to 4 and `0.4 * 11 = 4.4` to 4, `0.4 * 12 = 4.8` and
   `0.4 * 13 = 5.2` to 5, `0.4 * 9 = 3.6` to 4.)
4. **Bases.** Draw the base of player 0 uniformly from all nodes. Draw the base of player 1
   uniformly from the nodes whose shortest-path distance (in edges) from player 0's base is at
   least `MIN_BASE_DISTANCE` (3). If there is no such node, discard the graph and start again at
   step 1.

There are no self-loops and no duplicate edges. The map is not symmetric: one base may be much
closer to the centre of the graph than the other. Over a tournament this evens out, because every
pairing plays each seed in both seats (see "Tournament and ranking").

The map does not change during a match. It is in every observation: `n_nodes`, `edges`,
`player_base` and `opponent_base`.

## Orders

Each turn `t` (from `0` to `N_TURNS - 1 = 59`), a number of new orders appears:

1. Draw the number of new orders `k` uniformly from 0 to 2 (inclusive).
2. For each of the `k` orders, independently:
   - its **node** uniformly from all nodes except the two bases,
   - its **reward** uniformly from the integers 3 to 12 (inclusive),
   - its **deadline** (`turns_left` when it appears) uniformly from the integers 2 to 6
     (inclusive).

All draws are independent of each other and of the map, apart from the node excluding the bases.
Several open orders can be on the same node, and two orders can be identical.

An order that appears on turn `t` is in the observation of turn `t` (so you can still move units
to it on that turn) with its full `turns_left`. Each turn it stays open, its `turns_left` goes
down by 1. An order with `turns_left = L` in the observation of turn `t` can be fulfilled at the
end of turn `t`, `t + 1`, ..., `t + L - 1`; if it is still open after the end of turn
`t + L - 1`, it expires and is gone. So an order with `turns_left = 1` can only be fulfilled at
the end of the current turn.

## Units

Each player has 12 units, and all of them start at their base. Units are never created or
destroyed. Any number of units, from either player, may be at the same node. Units may move onto
any node, including the opponent's base.

A unit is **deployed** when it is not on its own player's base. Every deployed unit costs
`UPKEEP = 1.0` per turn (see "Scoring"), including units on the opponent's base and units on
nodes without an order. Units on their own base cost nothing and fulfil nothing (orders never
appear on a base).

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
- `act` is called once per turn, 60 times per match. You can keep state on `self` between turns.
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
3. both players' moves are applied,
4. every open order is checked against the unit positions after the moves: it is fulfilled or
   stays open (see "Scoring"),
5. each player's profit for the turn (rewards collected minus upkeep) is paid out,
6. every order still open loses one of its `turns_left`; orders at `0` expire,
7. the turn number goes up by one, and the new orders of the new turn appear.

Moves are simultaneous: neither player sees the other's moves for this turn before choosing its
own. Each player only moves its own units, so the order in which the two players' moves are
applied makes no difference.

**Each unit moves at most one edge per turn.** A move can only use units that were at
`from_node` at the start of the turn. Units that arrive at a node during a turn cannot move on
until the next turn. For example, if your base is node 5 and it is connected to node 8, which is
connected to node 6, then `[(5, 8, 2), (8, 6, 2)]` from the start position moves 2 units to
node 8, and the second move is illegal.

A unit `d` edges away from an order's node can be on it at the end of turn `turn + d - 1` at the
earliest, so it can still fulfil an order with `turns_left >= d`.

## Observation

Each turn your strategy's `act(obs)` receives a `dict`:

```python
{
    "turn": int,  # current turn, starting at 0
    "n_nodes": int,  # number of nodes; node ids are 0 .. n_nodes - 1
    "edges": list[tuple[int, int]],  # every undirected edge once, as (u, v) with u < v, sorted
    "player_base": int,  # node id of your base
    "opponent_base": int,  # node id of the opponent's base
    "orders": list[tuple[int, int, int]],  # open orders as (node, reward, turns_left), sorted
    "player_units": list[int],  # length n_nodes, index = node id: your units at that node
    "opponent_units": list[int],  # length n_nodes, index = node id: opponent's units there
    "player_profit_last_turn": float,  # your profit in the last turn (can be negative)
    "opponent_profit_last_turn": float,  # the opponent's profit in the last turn
    "player_cumulative_profit": float,  # your total profit so far
    "opponent_cumulative_profit": float,  # the opponent's total profit so far
    "n_invalid_actions_last_turn": int,  # number of your illegal moves in the last turn
    "action_format_correct_last_turn": bool,  # whether your last action was well-formed
}
```

**`orders`** lists every order that is open on the current turn, including the ones that
appeared on this turn, as tuples `(node, reward, turns_left)` with three `int`s, sorted in
ascending order (by node, then reward, then `turns_left`). `turns_left >= 1` always. Two
identical orders appear as two identical tuples. The list can be empty.

You see the whole map, the positions of all units, all open orders and both players' profits.
You do not see future orders. The total number of turns is not in the observation (see "End of
game").

On turn 0 there is no last turn yet: all `*_profit_last_turn` and `*_cumulative_profit` fields
are `0.0`, `n_invalid_actions_last_turn` is `0`, and `action_format_correct_last_turn` is `True`.

## Action

Terms used below: an **action** is what `act` returns for one turn. It is a list of **moves**.

```python
[(from_node, to_node, count), ...]
```

- Each move sends `count` of your units from `from_node` to `to_node` along one edge.
- An empty list means "do nothing". Units that are not moved stay where they are.
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
- `from_node` and `to_node` are not connected by an edge (this includes `from_node == to_node`),
- `count` is negative,
- `count` is more than the units you still have at `from_node` from the start of the turn, after
  earlier moves in the same list (see "Turn order").

A move with `count` 0 is legal and does nothing.

## Scoring

After all moves are applied, every open order is checked. Let `a` and `b` be the numbers of units
of the two players on the order's node:

- `a > b`: the first player collects the full `reward`; the order is fulfilled and removed,
- `a == b >= 1`: each player collects `reward / 2`; the order is fulfilled and removed,
- `a == b == 0`: nothing happens; the order stays open (until it expires, see "Orders").

Only the number of units counts, not how many more: 1 unit against 0 takes the whole reward, as
do 5 against 4. All orders on a node are checked against the same units, so one unit alone on a
node fulfils every open order there. A unit waiting on a node fulfils an order that appears
there later at the end of the turn on which the order appears, if the unit is still there then
and not outnumbered.

Each player's profit for the turn:

```
revenue_i = sum of the rewards (or half rewards) player i collected this turn
upkeep_i  = UPKEEP * (UNITS_PER_PLAYER - units_i at own base)     # UPKEEP = 1.0
profit_i  = revenue_i - upkeep_i
```

Profit can be negative. Your score in a match is the sum of your profits over all turns. A player
that never moves scores exactly `0`.

For example, at a node with one open order of reward 8:

- 1 of your units alone there collects 8, for a profit of `8 - 1.0 = 7.0` from that unit this
  turn.
- 2 of your units against 1 of the opponent's: you collect 8, the opponent nothing; after
  upkeep that is `6.0` for you and `-1.0` for the opponent.
- 2 against 2: each collects `4.0`; after upkeep, `2.0` each.

### Worked example: turns 0 to 2

This is a real map (`n_nodes = 9`). Player 0's observation on turn 0 is:

```python
{
    "turn": 0,
    "n_nodes": 9,
    "edges": [(0, 1), (0, 7), (0, 8), (1, 3), (1, 4), (1, 6), (2, 8), (3, 6), (3, 7), (4, 5),
              (5, 8), (6, 8)],
    "player_base": 3,
    "opponent_base": 5,
    "orders": [(6, 12, 4)],
    "player_units": [0, 0, 0, 12, 0, 0, 0, 0, 0],
    "opponent_units": [0, 0, 0, 0, 0, 12, 0, 0, 0],
    "player_profit_last_turn": 0.0,
    "opponent_profit_last_turn": 0.0,
    "player_cumulative_profit": 0.0,
    "opponent_cumulative_profit": 0.0,
    "n_invalid_actions_last_turn": 0,
    "action_format_correct_last_turn": True,
}
```

Player 1's observation is the same, except that `player_base` is `5`, `opponent_base` is `3`,
and the two unit lists are swapped.

- Player 0 plays `[(3, 6, 1), (3, 7, 2)]`.
- Player 1 plays `[(5, 8, 2), (8, 6, 2)]`.

Player 1's second move is illegal: its units arrive on node 8 during this turn and cannot move
on (see "Turn order"). After the moves, player 0 has 1 unit on node 6 and player 1 has none, so
player 0 fulfils the order `(6, 12, 4)` and collects 12.

| player | revenue | deployed | upkeep | profit |
|-------:|--------:|---------:|-------:|-------:|
| P0     | 12.0    | 3        | 3.0    | 9.0    |
| P1     | 0.0     | 2        | 2.0    | -2.0   |

On turn 1 a new order `(7, 11, 2)` appears on node 7, where player 0's two units already are.
Player 0's observation on turn 1 is then:

```python
{
    "turn": 1,
    "n_nodes": 9,
    "edges": [(0, 1), (0, 7), (0, 8), (1, 3), (1, 4), (1, 6), (2, 8), (3, 6), (3, 7), (4, 5),
              (5, 8), (6, 8)],
    "player_base": 3,
    "opponent_base": 5,
    "orders": [(7, 11, 2)],
    "player_units": [0, 0, 0, 9, 0, 0, 1, 2, 0],
    "opponent_units": [0, 0, 0, 0, 0, 10, 0, 0, 2],
    "player_profit_last_turn": 9.0,
    "opponent_profit_last_turn": -2.0,
    "player_cumulative_profit": 9.0,
    "opponent_cumulative_profit": -2.0,
    "n_invalid_actions_last_turn": 0,
    "action_format_correct_last_turn": True,
}
```

Player 1's observation of turn 1 has `n_invalid_actions_last_turn` `1` (its illegal move).

On turn 1, player 0 plays `[(6, 3, 1), (7, 0, 1)]` and player 1 plays `[(8, 6, 1)]`. Player 0
still has 1 unit on node 7 and fulfils `(7, 11, 2)` for 11, minus upkeep for 2 deployed units:
profit `9.0` again. Player 1 has 2 units deployed and collects nothing: `-2.0`. On turn 2 the
orders `(6, 3, 2)` and `(8, 9, 6)` appear, and player 0's observation shows:

| field | value on turn 2 |
|---|---|
| `orders` | `[(6, 3, 2), (8, 9, 6)]` |
| `player_units` | `[1, 0, 0, 10, 0, 0, 0, 1, 0]` |
| `opponent_units` | `[0, 0, 0, 0, 0, 10, 1, 0, 1]` |
| `player_cumulative_profit` | `18.0` |
| `opponent_cumulative_profit` | `-4.0` |

Player 1 now has a unit on node 6 and a unit on node 8: if nobody else gets there, it fulfils both
new orders at the end of turn 2.

Profits are Python floats and can carry rounding error in their last digits; compare them with a
tolerance.

## End of game

The game ends after 60 turns (turns 0 to 59). Each player's score is the profit it collected;
the higher score wins (see "Tournament and ranking"). Orders still open at the end pay nothing,
and units that are away at the end cost nothing more.

Your strategy forfeits the rest of the match if it raises an exception, takes longer than
1 second for one call of `act` (10 seconds for `__init__`), or crashes. From that turn on your
moves are empty: your units stay where they are, still fulfil and share orders on their nodes and
still cost upkeep, and all of it still counts for your score. The match always plays on to the
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
