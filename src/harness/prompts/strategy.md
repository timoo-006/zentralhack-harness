TASK: write a complete strategy file for the game in the specification above, following the
strategy contract and the playbook.

## Your approach for this candidate

{persona}

## Strategic notes from an earlier analysis of this game

{analysis}

## Requirements

- `TIME_BUDGET = {time_budget}` at module level (seconds per `act`; the harness may change
  it). Typical `act` calls must take a small fraction of it; any search stops at about
  0.6 * TIME_BUDGET (measured with `time.perf_counter()` from the start of `act`).
- `class Strategy` with `__init__(self, player_id)` and `act(self, obs)`, standard library
  only, deterministic, no printing.
- Start the file with a comment that quotes the spec's exact action format and the payout
  and cost rules you rely on. Then write small, clearly named helpers: map preprocessing
  (built on the first `act`), the payout function exactly as the spec defines it, the
  planning logic of your approach, and one function that turns a plan into a legal action
  in exactly the spec's format, checked move by move against the start-of-turn state.
- Plan from the actual observation every turn (positions, values, forecast, turn number);
  read the number of turns and other constants from the spec and from the observation, never
  from an example in the spec when they can vary between matches.
- Count every cost, the opponent's share at contested nodes and the arrival delay of moving
  units. The strategy must beat an opponent that does nothing, by a clear margin, in both
  seats, and must not lose money by moving around without earning.
- Before answering, walk through the bug checklist of the playbook and make sure each item
  holds for your code. Then trace one turn mentally on the spec's example observation: what
  does `act` return, is every move legal, are the types exact?

Answer with exactly one fenced ```python block containing the complete file.
