TASK: analyse the game in the specification above for the engineers who will write its
strategies. Do not write code. Answer in plain text (short headed sections with bullet
points, no code blocks), at most 600 words, precise and specific to this game. The notes
will be given to every later strategy-writing call, so state exact rules, formats and
numbers from the spec, not general advice.

Cover, in this order:

1. Scoring levers: exactly how score is gained and lost each turn (the payout formula, its
   saturation and sharing behaviour, every cost and when it is charged), what doing nothing
   scores, and which decisions move the margin between the players most.
2. What the observation reveals and what it hides: map, positions, values, forecasts, turn
   counter, scores, feedback about the previous action; which fields are relative to the
   player and which are absolute; what must be read from the spec instead (number of turns,
   constants).
3. Action format pitfalls: the exact format with types, what is malformed (whole action
   ignored?) versus illegal (single move skipped?), movement limits per turn, start-of-turn
   availability, and the safe no-op.
4. Recommended strategy outline: the decision procedure you would implement (target
   allocation, travel and payback, opponent prediction, denial, endgame), with the key
   thresholds or formulas for this game.
5. The 5 most likely bugs that would make a strategy for this game lose matches, each with
   how to avoid it.
