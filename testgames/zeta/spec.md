# Game Zeta

## Overview

Two courier companies compete for orders on a graph over a fixed number of turns. Each turn,
both players move couriers ("units") between nodes; the units present at a node fill a share of
that node's orders proportional to the capacity they bring. Only the first 6 units of a player
at a node do any work: further units at the same node fill nothing. Every unit that is away from
its home base costs upkeep each turn, and the further it is from its base, the more it costs. A
player's profit is the orders it fills minus the upkeep it pays, summed over the whole game. The
player with the higher profit wins the match (see "Tournament and ranking").

What changes from match to match:

- **The map.** Every match is played on a different random graph with randomly placed bases.
  You learn it from the observation, at the start of the match.
- **The orders.** The orders of each node change from turn to turn and drift around the map.
  You see each node's orders for the current turn and the next two turns. The total number of
  orders over all nodes grows during the match, following a curve that is drawn at random for
  each match.

This document is the complete definition of the game. You will not get the game's source code or
a simulator, so everything needed to write a strategy, or to build your own simulator, is here.
Your deliverable is one Python class, described in "Your strategy".

**Read the formats carefully.** The observation and the action use their own key names and
shapes (for example, the forecast is indexed `orders[k][node]`, turn first, and the action is a
nested `dict`). Everything is specified exactly below.

## Constants

Every constant in this document, as Python you can copy:

```python
N_TURNS = 90
UNITS_PER_PLAYER = 24
UNIT_CAPACITY = 4
SERVE_LIMIT = 6  # at most this many units of one player serve at one node
UPKEEP_BASE = 1.0  # per unit away from its own base, per turn ...
UPKEEP_PER_HOP = 0.5  # ... plus this much per link of shortest-path distance from that base
FORECAST_HORIZON = 3  # the forecast covers the current turn and the next 2

# map generator (see "Map")
N_NODES_RANGE = (12, 18)  # inclusive
EXTRA_EDGE_FRACTION = 0.35
MIN_BASE_DISTANCE = 3

# total order curve (see "Orders")
D_START_RANGE = (35.0, 55.0)
D_END_RANGE = (90.0, 120.0)
CURVE_SHAPES = ("linear", "early", "late")

# per-node orders (see "Orders")
HUB_WEIGHT_SIGMA = 0.9
DRIFT_PHI = 0.92
DRIFT_SIGMA = 0.3
```

## Seeds

Each match is created from an integer seed. The seed fixes the map, the bases and the orders of
every turn, before the first turn is played. Nothing about the environment depends on what the
players do. In a tournament both players of a match, and every pairing that plays the same seed,
face exactly the same map and orders.

You cannot reproduce a match's map or orders from its seed: the generator below is specified
exactly in distribution, not in its random number calls. Use it to generate maps and orders of
your own, for example to test a strategy.

## Map

The map is an undirected, connected graph with `n` nodes, numbered `0` to `n - 1`, connected by
**links**. Two of the nodes are bases, one per player. It is generated as follows:

1. Draw `n` uniformly from 12 to 18 (inclusive).
2. **Spanning tree.** Put the node ids in a uniformly random order `p[0], ..., p[n - 1]`. For
   `k = 1, ..., n - 1`, add the link between `p[k]` and `p[j]`, with `j` drawn uniformly from
   `0, ..., k - 1`. This makes the graph connected.
3. **Extra links.** Add `round(EXTRA_EDGE_FRACTION * n)` more links (4 to 6; the value is never
   a tie). Each one connects a uniformly drawn pair of distinct nodes that are not linked yet.
4. **Bases.** Draw the base of player 0 uniformly from all nodes. Draw the base of player 1
   uniformly from the nodes whose shortest-path distance (in links) from player 0's base is at
   least `MIN_BASE_DISTANCE` (3). If there is no such node, discard the graph and start again at
   step 1.

There are no self-loops and no duplicate links. The map is not symmetric: one base may be
central and close to the busy nodes, the other at the end of a long branch. Over a tournament
this evens out, because every pairing plays each seed in both seats (see "Tournament and
ranking").

The map does not change during a match. It is in every observation: `graph["nodes"]` (the
number of nodes `n`), `graph["links"]`, `you["base"]` and `rival["base"]`.

**Hops.** `hops(a, b)` is the shortest-path distance between nodes `a` and `b`, counted in
links (`hops(a, a) = 0`). Upkeep depends on it (see "Scoring"); compute it yourself with a
breadth-first search over `graph["links"]`.

## Orders

Each turn `t` (from `0` to `N_TURNS - 1 = 89`), every node `i` has orders `orders[t][i] ≥ 0`.
Both bases have `0` orders on every turn. The orders of the other nodes are the **total**
`D(t)` of that turn, split between them by **weights** `w_i(t)`.

### Total orders

```
D(t) = D_start + (D_end - D_start) * s(t / 89)
```

At the start of the match, draw:

- `D_start` uniformly from `D_START_RANGE` (35 to 55),
- `D_end` uniformly from `D_END_RANGE` (90 to 120),
- the shape `s` uniformly from the three `CURVE_SHAPES`.

| shape    | `s(x)`             | growth                         |
|----------|--------------------|--------------------------------|
| `linear` | `x`                | steady                         |
| `early`  | `1 - (1 - x)**2`   | fast at first, then levels off |
| `late`   | `x**2`             | slow at first, then fast       |

So the total grows from somewhere between 35 and 55 on turn 0 to somewhere between 90 and 120
on turn 89. **The observation does not tell you `D_start`, `D_end` or the shape.** You only see
the orders of the forecast window (see "Observation").

### Weights

Each non-base node `i` has a fixed **hub weight** and a **drift** that changes every turn:

```
hub_i      = exp(HUB_WEIGHT_SIGMA * g_i)                       # g_i ~ N(0, 1), drawn once
drift_i(0) = 0
drift_i(t + 1) = DRIFT_PHI * drift_i(t) + DRIFT_SIGMA * e_i(t)  # e_i(t) ~ N(0, 1)
w_i(t)     = hub_i * exp(drift_i(t))
```

All `g_i` and `e_i(t)` are independent standard normal draws. Hub weights make some nodes busy
for the whole match; the drift moves orders between nodes over time (`DRIFT_PHI = 0.92`), with
noticeable changes from one turn to the next.

### Orders per node

```
orders[t][i] = round(D(t) * w_i(t) / W(t), 1)      # W(t) = sum of w_j(t) over all non-base nodes j
```

Orders are rounded to one decimal (Python's `round`). Because of rounding, the orders of a turn
add up to `D(t)` only approximately. A node's orders can round to `0.0`.

## Units

Each player has 24 units, and all of them start at their own base. Every unit has capacity
`UNIT_CAPACITY = 4`.

Units are never created or destroyed, so each player has exactly 24 units for the whole game.
Any number of units, from either player, may be at the same node, and units may move onto any
node, including the rival's base. But at each node, **only `SERVE_LIMIT = 6` of a player's
units serve**: a player with 9 units at a node has a capacity of `6 * 4 = 24` there, not 36. The
other 3 units fill nothing and still pay upkeep.

A unit is **deployed** when it is not on its own player's base. Every deployed unit costs upkeep
every turn, and the cost grows with its distance from its own base (see "Scoring"). This
includes units on the rival's base and on nodes without orders. Units on their own base cost
nothing and earn nothing.

## Your strategy

A strategy is a Python class with two methods:

```python
class MyStrategy:
    def __init__(self, player_id: int) -> None:
        self.player_id = player_id  # 0 or 1

    def act(self, obs: dict) -> dict[int, dict[int, int]]:
        return {}  # this turn's moves; see "Observation" and "Action"
```

- A new instance is created for every match, with `player_id` 0 or 1.
- `act` is called once per turn, 90 times per match. You can keep state on `self` between turns.
- The map is not known in `__init__`. It is in every observation; a strategy that needs
  precomputed data about the map (distances, paths) typically builds it on the first call of
  `act` and keeps it on `self`.
- Everything in the observation is from your point of view (`you` is yours, `rival` is the
  opponent's), so you do not need `player_id` to play.
- If `act` raises an exception, takes too long or crashes, you forfeit the rest of the match (see
  "End of game").

## Turn order

Each turn:

1. both players' `act(obs)` are called with the observation for the current turn,
2. each returns its action,
3. both players' moves are applied, then that turn's profit is paid out (see "Scoring"), then
   the turn number goes up by one.

Moves are simultaneous: neither player sees the other's moves for this turn before choosing its
own. Each player only moves its own units, so the order in which the two players' moves are
applied makes no difference.

**Each unit moves at most one link per turn.** A move can only use units that were at
`from_node` at the start of the turn. Units that arrive at a node during a turn cannot move on
until the next turn. For example, if your base is node 8 and it is linked to node 9, which is
linked to node 6, then `{8: {9: 5}, 9: {6: 5}}` from the start position moves 5 units to node 9,
and the second move is illegal (there are no units on node 9 at the start of the turn).

The payout of turn `t` uses `orders[t]`, the orders of that same turn, and the unit positions
after that turn's moves.

## Observation

Each turn your strategy's `act(obs)` receives a `dict`:

```python
{
    "turn": int,  # current turn, starting at 0
    "graph": {
        "nodes": int,  # number of nodes n; node ids are 0 .. n - 1
        "links": list[list[int]],  # every undirected link once, as [u, v] with u < v, sorted
    },
    "you": {
        "base": int,  # node id of your base
        # length n, index = node id: your units at that node (all, also those above SERVE_LIMIT)
        "units": list[int],
        "score": float,  # your total profit so far
        "last_profit": float,  # your profit in the last turn (can be negative)
    },
    "rival": {  # the same four keys, for the opponent
        "base": int,
        "units": list[int],
        "score": float,
        "last_profit": float,
    },
    "orders": list[list[float]],  # TURN FIRST: orders[k][i] = orders of node i on turn turn + k
    "feedback": {
        "format_ok": bool,  # whether your last action was well-formed
        "rejected": int,  # number of your illegal moves in the last turn
    },
}
```

**`orders`** has one row per forecast turn, and each row has one entry per node:
`obs["orders"][k][i]` is `orders[turn + k][i]`, the orders of node `i` on turn `turn + k`. Note
the index order: the turn offset `k` comes first, the node `i` second.

- `k = 0` is the **current turn**: the one your moves of this turn will be paid on.
- There are `min(FORECAST_HORIZON, N_TURNS - turn)` rows: 3, except on the last two turns (2 rows
  on turn 88, 1 row on turn 89). Every row has length `n`.
- The forecast is exact: these are the values the payouts will use.
- Both bases are included, with orders `0.0`.

You see the whole map, the positions of all units and both players' profits. You do not see
orders beyond the forecast window or the parameters of the order curve. The total number of
turns is not in the observation (see "End of game").

On turn 0 there is no last turn yet: `score` and `last_profit` are `0.0` for both players,
`feedback["format_ok"]` is `True` and `feedback["rejected"]` is `0`.

## Action

An **action** is what `act` returns for one turn: a `dict` that maps a source node to a `dict`
that maps destination nodes to unit counts.

```python
{from_node: {to_node: count, ...}, ...}
```

- Each **move** `from_node -> to_node: count` sends `count` of your units from `from_node` to
  `to_node` along one link.
- An empty dict `{}` means "do nothing". So does an inner empty dict (`{8: {}}`): it contains
  no moves, so it is never rejected, even under a key that is not a node id. Units that are not
  moved stay where they are.
- Moves are applied in the order of the dicts (Python dicts keep insertion order): all moves of
  the first source node, in the order of its inner dict, then those of the second, and so on.

Two things can go wrong, and next turn's `feedback` reports them separately.

**Malformed action:** the whole action is ignored for that turn, as if you had returned `{}`.
Next turn, `feedback["format_ok"]` is `False` and `feedback["rejected"]` is `0`, because no move
was looked at. The format is checked strictly:

- the action must be a plain `dict` (not a list, not a tuple, not `None`, not any other type);
  `None` is therefore always a no-op,
- every key of the action must be a plain Python `int` and every value must be a plain `dict`,
- every key of every inner dict must be a plain Python `int`, and every value a plain Python
  `int`.

"Plain `int`" excludes `bool` values, floats such as `3.0`, strings such as `"8"`, numpy
integers such as `np.int64` and every other subclass of `int`. Convert them with `int(...)`.
"Plain `dict`" excludes subclasses such as `collections.defaultdict` and
`collections.OrderedDict`, at either level; convert them with `dict(...)` (for a
`defaultdict(dict)` of moves, return `{u: dict(t) for u, t in moves.items()}`). Lists of moves
in the style `[(from_node, to_node, count)]` are malformed. A single malformed entry anywhere
makes the whole action malformed, even if every other entry is fine.

**Illegal move:** the action is well-formed, but one move breaks a rule. Only that move is
skipped, the rest of the action still applies, and it adds 1 to `feedback["rejected"]`. A move
is illegal if:

- `from_node` or `to_node` is not a node id (`0` to `n - 1`),
- `from_node` and `to_node` are not connected by a link (this includes `from_node == to_node`),
- `count` is negative,
- `count` is more than the units you still have at `from_node` from the start of the turn,
  after the earlier moves out of `from_node` in the same action (see "Turn order").

A move with `count` 0 between two linked nodes is legal and does nothing.

## Scoring

After all moves are applied, each player's profit for the turn is its **revenue** minus its
**upkeep**.

**Revenue.** Each node's orders of this turn are split between the players at that node:

```
serving_p  = min(units_p at the node, SERVE_LIMIT)            # SERVE_LIMIT = 6
capacity_p = serving_p * UNIT_CAPACITY                         # UNIT_CAPACITY = 4
filled_p   = capacity_p * orders / max(total_capacity, orders)
```

`total_capacity` is the sum of both players' `capacity_p` at that node (so it is at most
`2 * 6 * 4 = 48`). The formula has two cases:

- **Orders exceed total capacity** (`total_capacity < orders`): every player fills its full
  capacity, `filled_p = capacity_p`, and part of the orders go unfilled.
- **Total capacity meets or exceeds the orders** (`total_capacity >= orders`): the orders are
  shared in proportion to capacity, `filled_p = orders * capacity_p / total_capacity`.

A node with no orders or no units contributes nothing. Your revenue for a turn is the sum of
`filled_p` over all nodes.

**Upkeep.** Each of your units that is not on your own base after this turn's moves costs

```
upkeep(node) = UPKEEP_BASE + UPKEEP_PER_HOP * hops(your_base, node)   # 1.0 + 0.5 * hops
```

per turn: `1.5` one link from your base, `2.0` two links away, `2.5` three links away, and so
on. Units on your own base cost `0`. A unit on the rival's base, or on a node without orders,
pays the same distance-based upkeep. Units above the serving limit pay upkeep too.

```
upkeep_p = sum over all nodes v != your base of units_p at v * upkeep(v)
profit_p = revenue_p - upkeep_p
```

Profit can be negative. Your score in a match is the sum of your profits over all turns. A player
that never moves scores exactly `0`. A strategy that scatters its units without regard to
upkeep scores well below `0`.

For example, at a node with orders 10.0 that is 2 links from your base (upkeep `2.0` per unit):

- 2 units alone fill `min(8, 10) = 8`, for a profit of `8 - 2 * 2.0 = 4.0` from that node.
- 3 units alone fill `10` (capacity 12 meets the orders), for a profit of `10 - 6.0 = 4.0`: the
  third unit pays its upkeep and adds only 2 of revenue.
- 2 of your units against 3 of the rival's fill `10 * 8 / 20 = 4.0` for you and `6.0` for the
  rival. After upkeep that is `0.0` for you.

And at a node with orders 30.0 that is 1 link from your base (upkeep `1.5`):

- 6 units alone fill `min(24, 30) = 24`, profit `24 - 9.0 = 15.0`.
- 8 units alone also fill only `24` (only 6 serve), profit `24 - 12.0 = 12.0`.
- 6 of your units against 6 of the rival's: total capacity 48, you fill `30 * 24 / 48 = 15.0`.

### Worked example: turns 0, 1 and 2

This is a real map (`n = 12`) with a "linear" order curve. Player 0's base is node 8, player
1's base is node 5. The hop distances from node 8 are
`[1, 3, 1, 2, 1, 3, 2, 2, 0, 1, 3, 2]` (index = node id); from node 5 they are
`[2, 2, 4, 3, 4, 0, 1, 4, 3, 2, 4, 3]`. Player 0's observation on turn 0 is:

```python
{
    "turn": 0,
    "graph": {
        "nodes": 12,
        "links": [[0, 6], [0, 8], [0, 11], [1, 6], [2, 8], [3, 7], [3, 9], [3, 11], [4, 7],
                  [4, 8], [5, 6], [6, 9], [7, 10], [8, 9], [10, 11]],
    },
    "you": {
        "base": 8,
        "units": [0, 0, 0, 0, 0, 0, 0, 0, 24, 0, 0, 0],
        "score": 0.0,
        "last_profit": 0.0,
    },
    "rival": {
        "base": 5,
        "units": [0, 0, 0, 0, 0, 24, 0, 0, 0, 0, 0, 0],
        "score": 0.0,
        "last_profit": 0.0,
    },
    "orders": [
        [2.0, 1.4, 4.6, 3.3, 2.1, 0.0, 11.4, 1.1, 0.0, 9.1, 3.6, 8.2],  # turn 0 (current)
        [2.2, 1.6, 5.9, 3.6, 1.9, 0.0, 9.2, 1.3, 0.0, 6.6, 4.6, 10.3],  # turn 1
        [2.0, 1.9, 4.6, 7.3, 1.1, 0.0, 8.3, 1.2, 0.0, 5.7, 4.6, 11.2],  # turn 2
    ],
    "feedback": {"format_ok": True, "rejected": 0},
}
```

Player 1's observation is the same, except that `you` and `rival` are swapped.

**Turn 0.**

- Player 0 plays `{8: {9: 7, 2: 1}}`.
- Player 1 plays `{5: {6: 3, 9: 2}}`.

Player 1's move `5 -> 9` is illegal (nodes 5 and 9 are not linked), so it is skipped and
player 1's `feedback["rejected"]` on turn 1 is `1`; its move `5 -> 6` still applies. The payout
uses the turn-0 orders (`orders[0]`):

| node | orders | units  | serving | capacity | filled | upkeep per unit |
|-----:|-------:|-------:|--------:|---------:|-------:|----------------:|
| 9    | 9.1    | P0: 7  | 6       | 24       | 9.1    | 1.5 (1 hop)     |
| 2    | 4.6    | P0: 1  | 1       | 4        | 4.0    | 1.5 (1 hop)     |
| 6    | 11.4   | P1: 3  | 3       | 12       | 11.4   | 1.5 (1 hop)     |

| player | revenue | upkeep          | profit |
|-------:|--------:|----------------:|-------:|
| P0     | 13.1    | 8 * 1.5 = 12.0  | 1.1    |
| P1     | 11.4    | 3 * 1.5 = 4.5   | 6.9    |

Player 0 overstaffed node 9: 3 units (capacity 12) would have filled all 9.1 orders, and the
seventh unit is above the serving limit and fills nothing at all. Player 0's observation on
turn 1 is then:

```python
{
    "turn": 1,
    "graph": {
        "nodes": 12,
        "links": [[0, 6], [0, 8], [0, 11], [1, 6], [2, 8], [3, 7], [3, 9], [3, 11], [4, 7],
                  [4, 8], [5, 6], [6, 9], [7, 10], [8, 9], [10, 11]],
    },
    "you": {
        "base": 8,
        "units": [0, 0, 1, 0, 0, 0, 0, 0, 16, 7, 0, 0],
        "score": 1.0999999999999996,
        "last_profit": 1.0999999999999996,
    },
    "rival": {
        "base": 5,
        "units": [0, 0, 0, 0, 0, 21, 3, 0, 0, 0, 0, 0],
        "score": 6.9,
        "last_profit": 6.9,
    },
    "orders": [
        [2.2, 1.6, 5.9, 3.6, 1.9, 0.0, 9.2, 1.3, 0.0, 6.6, 4.6, 10.3],  # turn 1 (current)
        [2.0, 1.9, 4.6, 7.3, 1.1, 0.0, 8.3, 1.2, 0.0, 5.7, 4.6, 11.2],  # turn 2
        [1.5, 1.6, 5.6, 4.6, 1.1, 0.0, 7.1, 1.7, 0.0, 5.9, 6.1, 13.2],  # turn 3
    ],
    "feedback": {"format_ok": True, "rejected": 0},
}
```

The window moved on by one turn: the old row `k = 1` is now row `k = 0`. Player 1's
observation on turn 1 has `"feedback": {"format_ok": True, "rejected": 1}`.

**Turn 1.**

- Player 0 plays `{9: {6: 4}, 8: {2: 1}}`.
- Player 1 plays `{5: {6: 5}}`.

Both moves of each player are legal. Node 6 is now contested: player 0 has 4 units there (2
hops from its base, upkeep 2.0 each), player 1 has 8 (1 hop from its base, upkeep 1.5 each), of
which only 6 serve.

| node | orders | P0 units (serving, capacity) | P1 units (serving, capacity) | P0 filled | P1 filled |
|-----:|-------:|-----------------------------:|-----------------------------:|----------:|----------:|
| 6    | 9.2    | 4 (4, 16)                    | 8 (6, 24)                    | 3.68      | 5.52      |
| 9    | 6.6    | 3 (3, 12)                    | 0                            | 6.6       | 0.0       |
| 2    | 5.9    | 2 (2, 8)                     | 0                            | 5.9       | 0.0       |

At node 6 the total capacity is `16 + 24 = 40 >= 9.2`, so player 0 fills `9.2 * 16 / 40 = 3.68`
and player 1 fills `9.2 * 24 / 40 = 5.52`.

| player | revenue | upkeep                                      | profit |
|-------:|--------:|--------------------------------------------:|-------:|
| P0     | 16.18   | 4 * 2.0 + 3 * 1.5 + 2 * 1.5 = 15.5          | 0.68   |
| P1     | 5.52    | 8 * 1.5 = 12.0                              | -6.48  |

Player 0's observation on turn 2 is then:

```python
{
    "turn": 2,
    "graph": {
        "nodes": 12,
        "links": [[0, 6], [0, 8], [0, 11], [1, 6], [2, 8], [3, 7], [3, 9], [3, 11], [4, 7],
                  [4, 8], [5, 6], [6, 9], [7, 10], [8, 9], [10, 11]],
    },
    "you": {
        "base": 8,
        "units": [0, 0, 2, 0, 0, 0, 4, 0, 15, 3, 0, 0],
        "score": 1.7799999999999994,
        "last_profit": 0.6799999999999997,
    },
    "rival": {
        "base": 5,
        "units": [0, 0, 0, 0, 0, 16, 8, 0, 0, 0, 0, 0],
        "score": 0.41999999999999993,
        "last_profit": -6.48,
    },
    "orders": [
        [2.0, 1.9, 4.6, 7.3, 1.1, 0.0, 8.3, 1.2, 0.0, 5.7, 4.6, 11.2],  # turn 2 (current)
        [1.5, 1.6, 5.6, 4.6, 1.1, 0.0, 7.1, 1.7, 0.0, 5.9, 6.1, 13.2],  # turn 3
        [2.0, 2.2, 4.3, 5.1, 1.1, 0.0, 6.3, 2.1, 0.0, 8.0, 4.1, 13.8],  # turn 4
    ],
    "feedback": {"format_ok": True, "rejected": 0},
}
```

Profits are Python floats and carry rounding error in their last digits (for example
`1.0999999999999996` for 1.1); compare them with a tolerance.

## End of game

The game ends after 90 turns (turns 0 to 89). Each player's score is the profit it collected;
the higher score wins (see "Tournament and ranking").

Your strategy forfeits the rest of the match if it raises an exception, takes longer than
1 second for one call of `act` (10 seconds for `__init__`), or crashes. From that turn on your
moves are empty: your units stay where they are, still take their share of each node's orders
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
