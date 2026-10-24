# Playbook: what wins in this game family

Distilled from many matches of games in this family. The rules of each game differ, so treat
everything here as a checklist of ideas and verify each one against the specification: the
specification always wins.

## 1 The family

Two players, simultaneous moves, a fixed number of discrete turns. Each player controls units
on the nodes of a graph; nodes carry demand (or some other value); units at a node collect
value from it, and contested value is shared between the players by a rule in the spec
(typically in proportion to capacity, capped by capacity). Variations to expect: different
action formats (moves as tuples, dicts, per-unit ids), several unit types with different
capacity, cost or speed, running costs for units away from home, demand that changes over
time (often with a forecast in the observation), random maps given in the observation,
capture or ownership of nodes, hidden information, randomness, different numbers of turns
and units. The highest score at the end wins; a near-equal score is a draw.

## 2 Contract hygiene: the most common way to lose

- Copy the action format from the spec character by character: container types, element
  types, order of fields, field names. A tuple where a list is required (or the reverse),
  a `bool` or a float where an `int` is required, or a missing field usually makes the WHOLE
  action malformed and ignored. Build every action at the end with explicit `int(...)`.
- Legality is judged against the state at the START of the turn. A unit that moves this turn
  usually cannot move again in the same turn (one step per turn unless the spec says
  otherwise). Track a per-node `available` copy of your start-of-turn counts and decrement
  it as you emit moves.
- Moves must follow the spec's movement rule exactly (e.g. only along an existing edge, no
  move from a node to itself, only your own units, counts within what is available).
- Aggregate: emit one move per (from, to) pair with the summed count; skip zero counts.
- Use the observation's own map and positions, never an example map from the spec, when maps
  can differ between matches. Never index "mine" vs "theirs" fields by `player_id` when the
  observation is already relative to you; check which fields are absolute (bases, node ids).
- Do nothing (`None`, or the spec's empty action) is always safe; a wrong action is not.

## 3 Map preprocessing (first call of act)

- Build adjacency lists, then all-pairs hop distances with one BFS per node (O(n * e), fast
  for hundreds of nodes). With weighted edges or travel times use Dijkstra from each node.
- Precompute a next-hop table: `next_hop[a][b]` = the neighbour of `a` on a shortest path to
  `b`, ties broken by smallest node id. Moving a unit toward `b` is then one lookup.
- Cache distances from both home bases (or spawn points); they decide who reaches a node
  first and how much travel time a deployment wastes.

## 4 Economics of placing units

- Write the payout as a function `served(own, opp, value)` exactly as in the spec and test it
  on the spec's numeric examples in your head. For the usual capacity-share rule
  `served_i = cap_i * d / max(cap_total, d)`:
  - while total capacity is below the demand, every unit adds its full capacity;
  - once the node is saturated (total capacity >= demand), your extra unit only takes share
    from the opponent: your gain equals exactly the opponent's loss; with no opponent there,
    an extra unit at a saturated node gains nothing.
- The payout is concave in your own units (each extra unit at a node adds less). For a
  separable concave objective, greedy allocation is optimal: repeatedly give one unit to the
  node with the highest marginal gain (a heap), until units run out or the best marginal
  gain is not positive. Never send everyone to the single best node.
- Subtract every cost the spec defines: running costs per unit per turn, costs while
  travelling, build or move costs. A unit is only worth deploying where its marginal gain
  exceeds its marginal cost over the time it will actually spend there. Units that earn less
  than they cost should go (or stay) where they cost nothing.
- Payback: a deployment that needs `h` turns of travel pays about `h * cost` before it earns
  anything. Deploy only if `sum over the turns it will be in place of (marginal gain - cost)`
  is larger than the travel cost. Far-away small nodes are usually not worth it.
- Demand that changes: value a node over the turns your unit would be there, using the
  forecast if the observation has one (index 0 is usually the current turn, the one this
  turn's moves are paid on: check). Do not chase a spike that ends before you arrive. Beyond
  the forecast, extrapolate conservatively (last value or recent trend).
- Payout timing matters: if the payout is computed after this turn's moves, a unit that moves
  onto a node this turn is paid there this turn; a unit two hops away is paid only after the
  second move.

## 5 Movement and assignment

- Decide a target allocation (units per node), then move units toward it: pair surplus nodes
  with deficit nodes closest-first (sort all (distance, from, to) pairs), and move each paired
  unit one step along a shortest path. This is legal by construction and wastes little time.
- Hysteresis: re-targeting every turn makes units oscillate and never arrive. Keep a unit's
  current target unless the new plan is clearly better (e.g. by more than a fixed margin),
  and prefer plans that keep already-placed units in place.
- Units in transit still count: when computing marginal values, count units that will arrive
  soon as present (with their arrival time), or you will send too many to the same node.

## 6 Opponent modelling: only the margin counts

- Only `own score - opponent score` decides the match. Denying the opponent value is worth
  exactly as much as gaining it. The marginal value of one of your units at a node is
  `(your gain) + (the opponent's loss)` minus your costs. At a saturated contested node both
  terms are equal, so contesting the opponent's best nodes can be worth twice what it looks.
- The opponent's units are usually visible and move a limited distance per turn. Predict the
  next positions: at minimum "they stay where they are"; better, a mix of "stay" and "move
  toward the nodes a greedy player would want". Nodes the opponent cannot reach for `k` turns
  are safe for `k` turns.
- Best response: allocate against the predicted opponent positions, not the current ones,
  when units are moving. Abandon contests you cannot win profitably (both players losing
  money at one node helps the side with more elsewhere).
- Track the opponent across turns (did they move, where, how many units deployed) to classify
  them: idle, static, greedy, aggressive, random. Exploit what you see, but default to a
  solid allocation when unsure.

## 7 Winning, not scoring

- Maximise the probability of winning each match, not the expected score. A match is a draw
  when scores are within the spec's tolerance, so a tiny lead is worthless: win clearly.
- Must-win opponents: an idle opponent (scores exactly 0 when doing nothing is free: you must
  end clearly above it, so never run a plan that can end negative), a random opponent, a
  naive greedy opponent that ignores costs, a strong greedy best-responder, and a copy of
  yourself. Test mentally against all of them, in both seats.
- Seats can be asymmetric (bases in different positions, a map not mirrored): never assume
  you are player 0, and read every position from the observation.
- When far ahead late in the game, reduce risk (cut marginal units, avoid fights that can
  swing the result). When behind, take calculated risks (contest the opponent's richest
  nodes), because a loss by 1 equals a loss by 100.

## 8 Endgame and horizon

- The total number of turns usually comes from the spec, not from the observation. With turns
  numbered from 0, the turns left including the current one are `N_TURNS - turn`. Get this
  off-by-one right: it decides when deployments stop paying back.
- Do not start a trip that cannot arrive and pay back before the game ends. In the last turns,
  stop moving units that are earning; recall units whose remaining earnings are below their
  remaining costs only if returning actually saves cost.
- Last-turn actions usually still count (moves, then payout): do not idle on the final turn.

## 9 Hidden information, randomness, new mechanics

- Hidden opponent units: keep a belief from the last sightings, the opponent's reported
  score changes and their maximum speed; be conservative where you cannot see.
- Randomness: optimise the expected value with your own seeded estimates; prefer plans that
  are good across outcomes over plans that are best in one outcome.
- Capture or ownership: value a capture by the stream it yields over the remaining turns,
  minus what it costs to take and hold; defend what is about to be taken.
- Several unit types: allocate by value per unit of cost (or per unit of the scarce resource),
  fast units to distant or changing targets, cheap units to holding.

## 10 Performance in pure Python

- Precompute once, then keep each turn O(units * nodes * log) or so. Avoid deep copies of the
  observation and nested loops over all pairs every turn when the map is large.
- Use local variables in hot loops, `heapq` for greedy allocation, lists indexed by node id
  instead of dicts where ids are dense. No recursion that can go deep.
- Any search (rollouts, lookahead) runs against a `time.perf_counter()` deadline well inside
  TIME_BUDGET and always has a cheap valid answer ready before it starts.

## 11 Bug checklist: check every item before answering

1. Action types exactly as specified (containers, int not bool/float, field order/names).
2. No unit moved twice in one turn; counts never exceed start-of-turn availability.
3. Every move follows the movement rule (adjacent, not to itself, own units only).
4. Arrival delay respected: a far node is valued at the time the unit gets there.
5. No oscillation: targets are sticky; units already in place stay.
6. Not everyone to the same node: marginal values decrease as units are added.
7. The opponent's share is counted at contested nodes (gain and denial both).
8. All costs counted, including while travelling; the plan never loses to doing nothing.
9. Turn horizon and forecast indices are correct (current turn, last turn, window length).
10. Works for both player ids and for any map the spec allows (sizes, positions, degrees).
11. Handles empty or degenerate observations (no units left, zero demand, no legal moves).
12. State on `self` stays consistent if `act` is interrupted, and if a turn is skipped.
13. Each `act` is fast (target < 50 ms) and deterministic.

## 12 Reference helpers (for games that move counts of units between adjacent nodes)

Adapt names and the final action format to the spec; these are correct as written.

```python
import heapq
from collections import deque

def all_pairs_hops(n, adj):  # adj[u]: sorted neighbours of u; -1 = unreachable
    dist = []
    for s in range(n):
        d = [-1] * n
        d[s] = 0
        q = deque([s])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if d[v] < 0:
                    d[v] = d[u] + 1
                    q.append(v)
        dist.append(d)
    return dist

def greedy_allocate(n_units, nodes, gain):  # gain(v, k): net value of the (k+1)-th unit at v
    count = dict.fromkeys(nodes, 0)
    heap = [(-gain(v, 0), v) for v in nodes]
    heapq.heapify(heap)
    for _ in range(n_units):
        if not heap or -heap[0][0] <= 0:
            break
        _, v = heapq.heappop(heap)
        count[v] += 1
        heapq.heappush(heap, (-gain(v, count[v]), v))
    return count  # units not allocated should go (or stay) where they cost nothing

def step_toward(have, want, dist, adj):  # have: list of counts; want: {node: count}, all units
    surplus = {v: c - want.get(v, 0) for v, c in enumerate(have) if c > want.get(v, 0)}
    deficit = {v: w - have[v] for v, w in want.items() if w > have[v]}
    pairs = sorted((dist[s][t], s, t) for s in surplus for t in deficit if dist[s][t] > 0)
    moves = {}
    for _, s, t in pairs:
        k = min(surplus[s], deficit[t])
        if k <= 0:
            continue
        surplus[s] -= k
        deficit[t] -= k
        hop = min(v for v in adj[s] if dist[v][t] == dist[s][t] - 1)
        moves[(s, hop)] = moves.get((s, hop), 0) + k
    return moves  # {(from, to): count}: legal one-step moves; convert to the spec's format last
```
