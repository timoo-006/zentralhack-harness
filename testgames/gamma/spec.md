# Game Gamma

## Overview

Two players compete for deliveries on a graph over a fixed number of turns. Each player runs a
fleet of couriers of two types, **trucks** and **scooters**, and moves each courier on its own,
by its id. Every node except the two bases holds a **stock** of orders. Couriers at a node serve
orders from that node's stock, which is used up by service and grows back slowly, at a rate of
its own. Every courier that is away from its home base costs money each turn, and the two types
cost different amounts. A player's profit is the orders it serves minus the running costs it
pays, summed over the whole game. The player with the higher profit wins the match (see
"Tournament and ranking").

What changes from match to match:

- **The map.** Every match is played on a different random graph with randomly placed bases.
  You learn it from the observation, at the start of the match.
- **The nodes.** Each node's maximum stock and regeneration rate are drawn at random for each
  match. Both are in every observation.

What does not: there is no randomness during a match. Stock changes only through service and
regeneration, exactly as described in "Stock".

This document is the complete definition of the game. You will not get the game's source code or
a simulator, so everything needed to write a strategy, or to build your own simulator, is here.
Your deliverable is one Python class, described in "Your strategy".

## Constants

Every constant in this document, as Python you can copy:

```python
N_TURNS = 60
UNIT_TYPES = {
    "truck": {"capacity": 8, "cost": 3.0, "speed": 1},
    "scooter": {"capacity": 3, "cost": 1.5, "speed": 2},
}
FLEET = {"truck": 4, "scooter": 8}  # units per player, all start on the player's base
UNIT_ID_PREFIX = {"truck": "T", "scooter": "S"}  # unit ids: "T0".."T3", "S0".."S7"

# map generator (see "Map")
N_NODES_RANGE = (9, 13)  # inclusive
N_CHORDS = 3  # extra edges on top of the ring
MIN_BASE_DISTANCE = 3

# node stock (see "Stock")
STOCK_MAX_RANGE = (10, 35)  # integers, inclusive
REGEN_RANGE = (0.5, 3.5)  # uniform, rounded to 1 decimal
STOCK_DECIMALS = 2  # stock is rounded to this many decimals after every turn
```

## Seeds

Each match is created from an integer seed. The seed fixes the map, the bases and every node's
maximum stock and regeneration rate, before the first turn is played. In a tournament both
players of a match, and every pairing that plays the same seed, face exactly the same map.

You cannot reproduce a match's map from its seed: the generator below is specified exactly in
distribution, not in its random number calls. Use it to generate maps of your own, for example
to test a strategy.

## Map

The map is an undirected, connected graph with `n_nodes` nodes, numbered `0` to `n_nodes - 1`.
Two of the nodes are bases, one per player. It is generated as follows:

1. Draw `n_nodes` uniformly from 9 to 13 (inclusive).
2. **Ring.** Connect every node `i` to node `(i + 1) % n_nodes`: the nodes form a cycle
   `0 - 1 - 2 - ... - (n_nodes - 1) - 0`.
3. **Chords.** Add `N_CHORDS = 3` more edges. Each one connects a uniformly drawn pair of
   distinct nodes that are not connected yet.
4. **Bases.** Draw the base of player 0 uniformly from all nodes. The base of player 1 is node
   `(base_0 + n_nodes // 2) % n_nodes`, the opposite side of the ring. If the shortest-path
   distance (in edges) between the two bases is less than `MIN_BASE_DISTANCE` (3), discard the
   graph and start again at step 1.

There are no self-loops and no duplicate edges. The chords make shortcuts, so the map is not
symmetric. Over a tournament this evens out, because every pairing plays each seed in both seats
(see "Tournament and ranking").

The map does not change during a match. It is in every observation: `n_nodes`, `edges`,
`player_base` and `opponent_base`. **Distances** below are always shortest-path distances in
edges on this graph.

## Stock

Every node `i` except the two bases has a **maximum stock** and a **regeneration rate**, drawn
once per match, independently for every node:

```
stock_max[i] = float(randint(10, 35))           # STOCK_MAX_RANGE, integers, inclusive
regen[i]     = round(uniform(0.5, 3.5), 1)      # REGEN_RANGE, rounded to 1 decimal
```

Both bases have `stock_max = 0.0` and `regen = 0.0`: there is nothing to serve on a base, ever.

Every node starts the match full: `stock[i] = stock_max[i]` on turn 0. Each turn, after the
moves, the couriers at a node serve orders from its current stock (see "Scoring"), and then the
node regenerates:

```
stock_next[i] = round(min(stock_max[i], stock[i] - served_0[i] - served_1[i] + regen[i]), 2)
```

`served_p[i]` is what player `p` served at node `i` this turn (`0.0` if it has no couriers
there). A node that nobody serves grows by `regen[i]` per turn up to `stock_max[i]`. A node that
is served faster than it regenerates runs dry: keeping couriers on one node for many turns earns
less and less, while their running cost stays the same.

## Units

Each player has 12 couriers: 4 trucks and 8 scooters. All of them start on the player's own
base. Couriers are never created or destroyed.

| type      | ids               | capacity | cost per turn | speed |
|-----------|-------------------|---------:|--------------:|------:|
| `truck`   | `"T0"` to `"T3"`  |        8 |           3.0 |     1 |
| `scooter` | `"S0"` to `"S7"`  |        3 |           1.5 |     2 |

- **Capacity:** the most orders the courier can serve per turn.
- **Cost:** what the courier costs per turn while it is **deployed**, i.e. not on its own
  player's base: on any other node, including the opponent's base and nodes with no stock.
  Couriers on their own base cost nothing and serve nothing.
- **Speed:** each turn a courier can move to any node at a distance of at most its speed from
  where it is. A truck moves to a neighbouring node; a scooter moves to a neighbouring node or to
  a node two edges away. The path it takes does not matter: nodes it passes through are not
  affected.

Both players use the same ids (`"T0"` ... `"S7"`). An id always refers to your own courier when
you use it in an action. Any number of couriers, from either player, may be on the same node.

## Your strategy

A strategy is a Python class with two methods:

```python
class MyStrategy:
    def __init__(self, player_id: int) -> None:
        self.player_id = player_id  # 0 or 1

    def act(self, obs: dict) -> dict:
        return {"moves": {}}  # this turn's moves; see "Observation" and "Action"
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
2. each returns its action (a destination for some of its couriers),
3. both players' moves are applied, then that turn's orders are served and profit is paid out
   (see "Scoring"), then every node regenerates (see "Stock"), then the turn number goes up by
   one.

Moves are simultaneous: neither player sees the other's moves for this turn before choosing its
own. Each player only moves its own couriers, so the order in which the two players' moves are
applied makes no difference.

**Each courier moves at most once per turn,** from the node where it was at the start of the turn,
to a node at a distance of at most its speed. The service of turn `t` uses the stock shown in the
observation of turn `t` and the courier positions after that turn's moves.

## Observation

Each turn your strategy's `act(obs)` receives a `dict`:

```python
{
    "turn": int,  # current turn, starting at 0
    "n_nodes": int,  # number of nodes; node ids are 0 .. n_nodes - 1
    "edges": list[tuple[int, int]],  # every undirected edge once, as (u, v) with u < v, sorted
    "player_base": int,  # node id of your base
    "opponent_base": int,  # node id of the opponent's base
    "stock": list[float],  # length n_nodes: each node's stock now (served this turn)
    "stock_max": list[float],  # length n_nodes: each node's maximum stock (fixed)
    "regen": list[float],  # length n_nodes: each node's regeneration per turn (fixed)
    "player_units": dict[str, tuple[str, int]],  # your couriers: id -> (type, node)
    "opponent_units": dict[str, tuple[str, int]],  # the opponent's couriers: id -> (type, node)
    "player_profit_last_turn": float,  # your profit in the last turn (can be negative)
    "opponent_profit_last_turn": float,  # the opponent's profit in the last turn
    "player_cumulative_profit": float,  # your total profit so far
    "opponent_cumulative_profit": float,  # the opponent's total profit so far
    "n_invalid_actions_last_turn": int,  # number of your illegal moves in the last turn
    "action_format_correct_last_turn": bool,  # whether your last action was well-formed
}
```

- `player_units` and `opponent_units` always hold all 12 couriers of that player, in the order
  `"T0"`, ..., `"T3"`, `"S0"`, ..., `"S7"`, each as a tuple `(type, node)` with `type` `"truck"` or
  `"scooter"` and `node` the courier's current node.
- `stock` is exact: these are the values this turn's service uses. Both bases are included, with
  `0.0`.

You see everything: the whole map, all stocks and their parameters, the positions of all
couriers and both players' profits. The total number of turns is not in the observation (see
"End of game").

On turn 0 there is no last turn yet: all `*_profit_last_turn` and `*_cumulative_profit` fields
are `0.0`, `n_invalid_actions_last_turn` is `0`, and `action_format_correct_last_turn` is `True`.

## Action

Terms used below: an **action** is what `act` returns for one turn. It is a `dict` with exactly
one key, `"moves"`, whose value is a `dict` of **moves**:

```python
{"moves": {unit_id: to_node, ...}}
```

- Each move sends your courier `unit_id` (a string such as `"T2"` or `"S5"`) to node `to_node`
  (an `int`).
- Couriers that are not in `"moves"` stay where they are. `{"moves": {}}` means "do nothing".
- A move to the node the courier is already on is legal and does nothing.
- Each courier can appear at most once (it is a dict key), so the order of the moves does not
  matter.

Two things can go wrong, and next turn's observation reports them separately.

**Malformed action:** the whole action is ignored for that turn, as if you had returned
`{"moves": {}}`. Next turn, `action_format_correct_last_turn` is `False` and
`n_invalid_actions_last_turn` is `0`, because no move was looked at. The format is checked
strictly:

- the action must be a `dict` (not a list, not `None`, not any other type),
- its keys must be exactly `{"moves"}` (a missing `"moves"` key or any extra key is malformed),
- the value of `"moves"` must be a `dict` (a list of pairs such as `[("T0", 3)]` is malformed),
- every key of that dict must be a `str`, and every value a plain Python `int`. `bool` values and
  numpy integers such as `np.int64` are malformed. Convert them with `int(...)`.

`None` is malformed, so returning `None` is always a safe "do nothing".

**Illegal move:** the action is well-formed, but one move breaks a rule. Only that move is
skipped (that courier stays where it is), the other moves still apply, and it adds 1 to
`n_invalid_actions_last_turn`. A move is illegal if:

- `unit_id` is not one of `"T0"`, ..., `"T3"`, `"S0"`, ..., `"S7"`,
- `to_node` is not a node id (`0` to `n_nodes - 1`),
- the distance from the courier's current node to `to_node` is more than its speed (more than 1
  for a truck, more than 2 for a scooter).

## Scoring

After all moves are applied, each player's profit for the turn is its **revenue** minus its
**running cost**.

**Revenue.** At every node, the orders are served from the node's current stock and split between
the players at that node in proportion to the capacity they bring:

```
capacity_p = sum of the capacities of player p's couriers at the node   # truck 8, scooter 3
served_p   = capacity_p * stock / max(total_capacity, stock)
```

`total_capacity` is the sum of both players' capacity at that node. The formula has two cases:

- **Stock exceeds total capacity** (`total_capacity < stock`): every player serves its full
  capacity, `served_p = capacity_p`, and the rest of the stock stays on the node.
- **Total capacity meets or exceeds the stock** (`total_capacity >= stock`): the whole stock is
  served, shared in proportion to capacity, `served_p = stock * capacity_p / total_capacity`.

A node with no stock (both bases, or a node that has run dry) or no couriers contributes nothing.
Your revenue for a turn is the sum of `served_p` over all nodes. Afterwards the stock goes down by
what both players served, and regenerates (see "Stock").

**Running cost.** You pay the cost of each of your couriers that is not on your own base after
this turn's moves:

```
cost_p   = 3.0 * (trucks of p not on p's base) + 1.5 * (scooters of p not on p's base)
profit_p = revenue_p - cost_p
```

Profit can be negative. Your score in a match is the sum of your profits over all turns. A player
that never moves scores exactly `0`.

For example, at a node with stock 20.0 and regeneration 2.0:

- 1 truck alone serves `min(8, 20) = 8` for a profit of `8 - 3.0 = 5.0`, and the stock becomes
  `20 - 8 + 2 = 14.0`. If it stays, the stocks it finds on the next turns are 14.0, 8.0, 2.0,
  2.0, ...: it serves 8 three turns in a row, and from the fourth turn on only the 2.0 that grows
  back each turn, for a cost of 3.0: it loses money.
- 2 trucks of yours against 1 scooter of the opponent's bring capacity 16 against 3: total 19 <
  20, so you serve 16 and the opponent 3. One turn later the stock is `20 - 19 + 2 = 3.0`, and
  if the same couriers stay they split it: `3.0 * 16 / 19 ≈ 2.53` for you and `≈ 0.47` for the
  opponent.

### Worked example: turns 0 and 1

This is a real map (`n_nodes = 10`). Player 0's observation on turn 0 is:

```python
{
    "turn": 0,
    "n_nodes": 10,
    "edges": [
        (0, 1),
        (0, 9),
        (1, 2),
        (2, 3),
        (2, 6),
        (3, 4),
        (4, 5),
        (4, 6),
        (4, 8),
        (5, 6),
        (6, 7),
        (7, 8),
        (8, 9),
    ],
    "player_base": 0,
    "opponent_base": 5,
    "stock": [0.0, 24.0, 10.0, 29.0, 17.0, 0.0, 26.0, 33.0, 31.0, 27.0],
    "stock_max": [0.0, 24.0, 10.0, 29.0, 17.0, 0.0, 26.0, 33.0, 31.0, 27.0],
    "regen": [0.0, 0.5, 2.5, 1.5, 0.6, 0.0, 1.3, 2.6, 3.3, 1.8],
    "player_units": {
        "T0": ("truck", 0),
        "T1": ("truck", 0),
        "T2": ("truck", 0),
        "T3": ("truck", 0),
        "S0": ("scooter", 0),
        "S1": ("scooter", 0),
        "S2": ("scooter", 0),
        "S3": ("scooter", 0),
        "S4": ("scooter", 0),
        "S5": ("scooter", 0),
        "S6": ("scooter", 0),
        "S7": ("scooter", 0),
    },
    "opponent_units": {
        "T0": ("truck", 5),
        "T1": ("truck", 5),
        "T2": ("truck", 5),
        "T3": ("truck", 5),
        "S0": ("scooter", 5),
        "S1": ("scooter", 5),
        "S2": ("scooter", 5),
        "S3": ("scooter", 5),
        "S4": ("scooter", 5),
        "S5": ("scooter", 5),
        "S6": ("scooter", 5),
        "S7": ("scooter", 5),
    },
    "player_profit_last_turn": 0.0,
    "opponent_profit_last_turn": 0.0,
    "player_cumulative_profit": 0.0,
    "opponent_cumulative_profit": 0.0,
    "n_invalid_actions_last_turn": 0,
    "action_format_correct_last_turn": True,
}
```

Player 1's observation is the same, except that `player_base` is `5`, `opponent_base` is `0`,
and the two unit dicts and the profit fields are swapped. The distances from player 0's base
(node 0) are `[0, 1, 2, 3, 3, 4, 3, 3, 2, 1]` and from player 1's base (node 5)
`[4, 3, 2, 2, 1, 0, 1, 2, 2, 3]`, for nodes 0 to 9.

- Player 0 plays `{"moves": {"T0": 9, "T1": 1, "T2": 8, "S0": 8, "S1": 8, "S2": 2}}`.
- Player 1 plays `{"moves": {"T0": 6, "T1": 4, "S0": 8, "S1": 7, "S2": 7, "S3": 2, "S4": 2, "S5": 2}}`.

Player 0's move `"T2": 8` is illegal: node 8 is 2 edges from node 0 and a truck has speed 1. Only
that move is skipped; truck `T2` stays on the base. All other moves are legal (the scooters go
2 edges, the trucks 1 edge).

After the moves, nodes 2 and 8 are contested. The service uses the turn-0 stock:

| node | stock | P0 couriers   | P0 cap | P1 couriers       | P1 cap | P0 served | P1 served | next stock |
|-----:|------:|---------------|-------:|-------------------|-------:|----------:|----------:|-----------:|
| 1    | 24.0  | T1            | 8      |                   | 0      | 8.0       | 0.0       | 16.5       |
| 2    | 10.0  | S2            | 3      | S3, S4, S5        | 9      | 2.5       | 7.5       | 2.5        |
| 3    | 29.0  |               | 0      |                   | 0      | 0.0       | 0.0       | 29.0       |
| 4    | 17.0  |               | 0      | T1                | 8      | 0.0       | 8.0       | 9.6        |
| 6    | 26.0  |               | 0      | T0                | 8      | 0.0       | 8.0       | 19.3       |
| 7    | 33.0  |               | 0      | S1, S2            | 6      | 0.0       | 6.0       | 29.6       |
| 8    | 31.0  | S0, S1        | 6      | S0                | 3      | 6.0       | 3.0       | 25.3       |
| 9    | 27.0  | T0            | 8      |                   | 0      | 8.0       | 0.0       | 20.8       |

At node 2 the total capacity 12 meets the stock 10.0, so it is split `10 * 3 / 12 = 2.5` and
`10 * 9 / 12 = 7.5`, and the node is left with only its regeneration, `0 + 2.5 = 2.5`. At node 8
the total capacity 9 is less than the stock, so both serve their full capacity. Node 3 is full
already: `min(29.0, 29.0 + 1.5) = 29.0`.

| player | revenue | deployed couriers         | running cost              | profit |
|-------:|--------:|---------------------------|---------------------------|-------:|
| P0     | 24.5    | 2 trucks, 3 scooters      | 2 * 3.0 + 3 * 1.5 = 10.5  | 14.0   |
| P1     | 32.5    | 2 trucks, 6 scooters      | 2 * 3.0 + 6 * 1.5 = 15.0  | 17.5   |

Player 0's observation on turn 1 is then:

```python
{
    "turn": 1,
    "n_nodes": 10,
    "edges": [
        (0, 1),
        (0, 9),
        (1, 2),
        (2, 3),
        (2, 6),
        (3, 4),
        (4, 5),
        (4, 6),
        (4, 8),
        (5, 6),
        (6, 7),
        (7, 8),
        (8, 9),
    ],
    "player_base": 0,
    "opponent_base": 5,
    "stock": [0.0, 16.5, 2.5, 29.0, 9.6, 0.0, 19.3, 29.6, 25.3, 20.8],
    "stock_max": [0.0, 24.0, 10.0, 29.0, 17.0, 0.0, 26.0, 33.0, 31.0, 27.0],
    "regen": [0.0, 0.5, 2.5, 1.5, 0.6, 0.0, 1.3, 2.6, 3.3, 1.8],
    "player_units": {
        "T0": ("truck", 9),
        "T1": ("truck", 1),
        "T2": ("truck", 0),
        "T3": ("truck", 0),
        "S0": ("scooter", 8),
        "S1": ("scooter", 8),
        "S2": ("scooter", 2),
        "S3": ("scooter", 0),
        "S4": ("scooter", 0),
        "S5": ("scooter", 0),
        "S6": ("scooter", 0),
        "S7": ("scooter", 0),
    },
    "opponent_units": {
        "T0": ("truck", 6),
        "T1": ("truck", 4),
        "T2": ("truck", 5),
        "T3": ("truck", 5),
        "S0": ("scooter", 8),
        "S1": ("scooter", 7),
        "S2": ("scooter", 7),
        "S3": ("scooter", 2),
        "S4": ("scooter", 2),
        "S5": ("scooter", 2),
        "S6": ("scooter", 5),
        "S7": ("scooter", 5),
    },
    "player_profit_last_turn": 14.0,
    "opponent_profit_last_turn": 17.5,
    "player_cumulative_profit": 14.0,
    "opponent_cumulative_profit": 17.5,
    "n_invalid_actions_last_turn": 1,
    "action_format_correct_last_turn": True,
}
```

`n_invalid_actions_last_turn` is `1` because of the truck move. Player 1's observation on turn 1
has `n_invalid_actions_last_turn` `0`.

Profits are Python floats and can carry rounding error in their last digits (for example
`5.6000000000000005`); compare them with a tolerance.

## End of game

The game ends after 60 turns (turns 0 to 59). Each player's score is the profit it
collected; the higher score wins (see "Tournament and ranking").

Your strategy forfeits the rest of the match if it raises an exception, takes longer than
1 second for one call of `act` (10 seconds for `__init__`), or crashes. From that turn on your
moves are empty: your couriers stay where they are, still take their share of each node's stock
and still cost their running cost, and both still count for your score. The match always plays on
to the end, even if both players have forfeited.

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
