---
marp: true
theme: default
paginate: true
title: The LLM as programmer - Zentral Hack 2026
---

# The LLM is a programmer, not a player

**Zentral Hack 2026 - our harness**

An unattended pipeline that turns a game specification into a strategy that never forfeits.

> Notes: (30 s) Introduce the team and the one-line claim. Everything on the next slides is
> measured on our own runs; every number comes from `docs/STATUS.md` and the section is cited
> in the notes of each slide. Total talk: 5-7 minutes, 9 slides.

---

## 1. The problem

- A **game nobody has seen**, from a known family (2 players, simultaneous moves, units on a graph).
- Our harness gets only `spec.md` + `task.json`, runs **unattended** in a sandbox, and must write one Python strategy file.
- Hard limits per run: **budget** and **deadline**, three levels:

| task | models | budget | deadline | weight |
|------|--------|-------:|---------:|-------:|
| A | luna | $0.03 | 3 min | 10% |
| B | luna, sol | $0.50 | 10 min | 35% |
| C | sol, astra | $10 | 30 min | 55% |

- Scored by **wins minus losses** in a tournament against other teams. A crash, a timeout or a malformed answer **forfeits** the match.

> Notes: (45 s) Source: docs/PLAN.md section 2.1. 2 unseen games x 3 tasks x 5 runs = 30
> strategies. Only results count, margins do not. The key tension: more LLM calls could give
> a better strategy, but money and time are capped, and a single broken strategy loses every
> match it plays. So robustness comes first, strength second.

---

## 2. Our idea in one sentence

**The LLM writes programs, not moves: several competing strategies plus a verified simulator, picked by self-play, wrapped in a guard that makes a forfeit impossible.**

- **Several strategies** with different personas (not one big bet).
- **A simulator** written from the spec, trusted only after it reproduces the spec's worked examples.
- **Self-play** on that simulator decides which strategy ships.
- **A harness-owned guard** assembles the final file: whatever the LLM wrote, the file is valid.

> Notes: (45 s) Why programmer, not player: at tournament time no LLM is called, and code
> plus search beats an LLM as a policy (docs/PLAN.md section 4, principle 3). The LLM's
> strength is writing code from a written spec; the harness's strength is checking it.
> The difference to a typical one-shot team: we verify and select, they hope.

---

## 3. The pipeline (one run)

```
t=0     write fallback strategy (plays None)  -> /output/strategy.py is ALWAYS valid
        parse the spec, no LLM: example observations, key schema, constants
batch   simulator (LLM)  ||  3-4 strategies, different personas (LLM)   (parallel)
verify  probe each strategy on the spec's example observations,
        play it vs idle on the simulator, repair from tracebacks;
        simulator must pass its selftest of the spec's worked examples
select  round robin on the simulator (candidates + idle, both seats, many seeds)
improve strongest model sees champion + tournament report -> challengers -> re-rank
finish  guard.assemble(champion, fallback=safe candidate), atomic replace
```

The plan (calls, models, effort) is derived from `task.json`, never from the task letter.

> Notes: (45 s) Source: docs/STATUS.md "How one run works" and docs/ARCHITECTURE.md section 0.
> Walk top to bottom; stress the two verification gates (probe on real example observations,
> simulator selftest). Profiles tiny (task A), mid (B), large (C) are picked by budget and
> deadline, so a differently sized task still gets a sensible plan. After the last LLM call,
> leftover time plays more self-play seeds, which costs no money.

---

## 4. Never forfeit, never overspend

- **Guard:** LLM code is embedded as strings and loaded into private modules, so a syntax or import error cannot break the file. A `SIGALRM` timer (a `BaseException`) limits each turn. Exception, timeout or non-literal output falls back to a simpler strategy, then to `None`.
- **Fallback at t=0**, then **atomic replace** (temp file + `os.replace`), and only after the new file passed a probe.
- **Budget:** every call **reserves its worst case** (input at the higher of input and cache-write price, plus max output tokens) before it is sent; hard cap at 90% of `budget_usd` ($0.027 / $0.45 / $9). Measured B runs: $0.31-0.40.
- **Deadline:** the clock counts from **container start** (PID 1 age), SIGTERM handler, stop starting work at a margin, hard exit.

> Notes: (50 s) Sources: report.md "Design decisions", docs/STATUS.md "How one run works"
> and "What is built". Tested with arena's real isolated worker on deliberately broken
> cores: raise, sleep, infinite loop, numpy import, print flood (199 tests pass on Linux).
> A hanging HTTP call cannot keep the process alive past the deadline because of the hard exit.

---

## 5. Calibration: what the measurements told us

One-shot strategy from the beta spec, then 128 matches against the other drafts and beta's baselines.

| model | effort | seconds | USD | wins (of 128) |
|-------|--------|--------:|----:|-----:|
| luna | low | 19 | 0.001 | 32 |
| luna | medium | 104 | 0.006 | 16 |
| luna | high | >456 | 0.021 | cut off, **no code** |
| sol | low | 49 | 0.041 | 104 |
| sol | medium | 145 | 0.083 | 104 |
| astra | low | 29 | 0.182 | 106 |
| astra | medium | 149 | 0.439 | 102 |

**So:** never luna at high effort; **sol @ low** matches sol @ medium and astra at a fraction of the cost, so we buy more drafts and improvement rounds instead of more effort.

> Notes: (50 s) Source: docs/STATUS.md "What we measured (calibration...)". Luna at high
> effort reasons up to the token cap and returns nothing, so the harness never uses it.
> In task A the deadline binds, not money (one luna-medium call took 104 of 180 s). Any
> advantage of astra has to come from improvement rounds, not from drafts.

---

## 6. Evidence: is each feature worth it?

Experiment 1: 24 task-B runs, 3 games, one feature off per arm; cross-tournament between the arms' strategies (+1/0/-1, both seats, 6 seeds). Net per strategy:

| arm | beta | gamma | epsilon | total |
|-----|-----:|------:|--------:|------:|
| full pipeline | +48.0 | +32.5 | -17.0 | +63.5 |
| no sparring draft | +48.0 | +28.5 | +36.5 | **+113** |
| no improvement | -48.5 | -14.5 | +2.0 | -61 |
| one-shot baseline | -47.5 | -46.5 | -21.5 | -115.5 |

- Full vs one-shot head-to-head: **48-0-0** (beta), **46-1-1** (gamma).
- Improvement rounds: the biggest single gain.
- Sparring draft: no gain, and on epsilon the full pipeline lost 11-1-36 to the arm without it - **removed by data**.

> Notes: (60 s) Source: docs/STATUS.md "Experiment 1" (commit 61610da, $5.31 in total).
> The one-shot arm is what a typical team ships: one draft plus the guard. Honest caveat: 2
> runs per arm is noisy, so we trust directions that agree across games, not single cells.
> The sparring result is the example of a feature we built, measured, and cut.

---

## 7. Evidence: transfer and the large task

**Held-out exam** (zeta, theta: never used for tuning; task B, 2 runs per arm)

| game | full vs one-shot (head-to-head) | vs baselines: full / one-shot |
|------|--------------------------------|------------------------------:|
| theta | **44-1-3** | 100% / 93% |
| zeta | **32-0-16** | 94% / 89% |

**Task C check** (C-lite: $2, 900 s, beta and gamma): both runs exit 0 with `arena check` PASS and 100% vs baselines, $0.96-1.11, 869-874 of 900 s. Time binds before money.

| experiment | result |
|-----|--------|
| **Experiment 2** (18 B runs): extra self-play seeds in spare time; improvement effort | both kept: the arm without polish and the low-effort arm each lost on 2 of 3 games, tied on beta (STATUS.md, "Experiment 2") |

> Notes: (50 s) Sources: docs/STATUS.md "Held-out exam" and "Task C check". All 8 held-out
> strategies pass the conformance check and every run stayed within budget and deadline.
> Notes: Experiment 2 confirmed the defaults; the decision rules were fixed before the results.

---

## 8. Against overfitting

We are scored on two games nobody has seen, so every change is judged by how it transfers.

- **Held-out games:** tune on six games, only *measure* on zeta and theta; never read their transcripts to fix prompts.
- **Robust ranking:** mean score per opponent (every opponent weighs the same), then worst pairing, then margin; a challenger must win by a margin and not lose the head-to-head.
- **Self-play under tournament conditions:** the delivered file's own time settings (0.25 s search per turn, 0.70 s hard limit), both seats, many seeds.
- **No game-specific content** in the harness (a test enforces it for prompts); a lesson enters the playbook only if it helped on at least two tuning games.
- **Data-driven feature decisions:** a feature stays if it holds or improves the cross-tournament, otherwise it is cut (the sparring draft).

> Notes: (50 s) Source: docs/STATUS.md "Against overfitting". The simulator is trusted only
> after it reproduces the spec's worked examples, and strategies are written from the spec,
> never from the simulator's code, so they cannot be tuned to its bugs. Nothing persists
> between evaluation runs: learning across games is offline, in the playbook file.

---

## 9. What we would do next

- **Opponent-aware search** inside the strategy, using the verified simulator code.
- **Population-based training** across more seeds with a larger opponent pool.
- More runs per experiment arm (2 per arm is noisy) before trusting small effects.
- Use the full task-C window: at 1800 s and $10 there is room for about twice the improvement rounds we saw in C-lite.

**Takeaway:** verify, select, guard. The LLM writes the code; the harness decides what ships.

> Notes: (30 s) Sources: report.md "What we would do next", docs/STATUS.md "Task C check".
> Close on the takeaway and open for questions. Likely questions: how do you trust the
> simulator (selftest against worked examples), what if the game has hidden information
> (theta covers that), what stops overspending (worst-case reservation).
