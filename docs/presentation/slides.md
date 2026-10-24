---
marp: true
theme: default
paginate: true
title: The LLM as programmer - Zentral Hack 2026
---

# The LLM is a programmer, not a player

**Zentral Hack 2026 - our harness**

An unattended pipeline that turns a game specification into a strategy that never forfeits.

> Notes: (20 s) Introduce the team and the one-line claim. Everything on the next slides is
> measured on our own runs or by the organisers' midpoint evaluation. Numbers come from
> `docs/STATUS.md` and `report.md`. Total talk: 5-7 minutes, 9 slides.

---

## 1. The problem

- A **game nobody has seen**, from a known family (2 players, simultaneous moves, units on a graph).
- One unattended run reads `spec.md` + `task.json` and must leave **one** `strategy.py`.
- Hard limits per run: **budget** and **deadline**, three levels:

| task | models | budget | deadline | weight |
|------|--------|-------:|---------:|-------:|
| A | luna | $0.03 | 3 min | 10% |
| B | luna, sol | $0.50 | 10 min | 35% |
| C | sol, astra | $10 | 30 min | 55% |

- Scored by **wins minus losses** against other teams. A crash, a timeout or a malformed answer **forfeits** the match.

> Notes: (40 s) Source: docs/PLAN.md section 2.1. 2 unseen games x 3 tasks x 5 runs = 30
> strategies. The tension: more LLM calls could give a better strategy, but money and time
> are capped, and one broken strategy loses every match it plays. So robustness comes
> first, strength second.

---

## 2. Our idea in one sentence

**The LLM writes programs, not moves: several competing strategies plus a verified simulator, picked by self-play, wrapped in a guard that makes a forfeit impossible.**

- **Several strategies** with different personas (not one big bet).
- **A simulator** written from the spec, trusted only after it reproduces the spec's worked examples.
- **Self-play** on that simulator decides which strategy ships, then improvement rounds make it better.
- **A harness-owned guard** assembles the final file: whatever the LLM wrote, the file is valid.

> Notes: (40 s) Why programmer, not player: at tournament time no LLM is called, and code
> plus search beats an LLM as a policy (docs/PLAN.md section 4, principle 3). The LLM is good
> at writing code from a written spec; the harness is good at checking it. A typical team
> ships one draft and hopes. We verify and select.

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

> Notes: (40 s) Source: report.md "Pipeline". Walk top to bottom; stress the
> two verification gates (probe on real example observations, simulator selftest). Profiles
> tiny (A), mid (B) and large (C) are picked by budget and deadline, so a differently sized
> task still gets a sensible plan. After the last LLM call, leftover time plays more
> self-play seeds, which costs no money. If a full improvement call no longer fits the
> money left, a budget rescue sends it at low effort.

---

## 4. Never forfeit, never overspend

- **Guard:** LLM code is loaded into private modules, so a syntax or import error cannot break the file. A `SIGALRM` timer limits each turn. Exception, timeout or bad output falls back to a simpler strategy, then to `None`.
- **Fallback at t=0**, then **atomic replace** (temp file + `os.replace`), only after the new file passed a probe.
- **Budget:** every call **reserves its worst case** before it is sent. Hard cap at 90% of `budget_usd`.
- **Deadline:** the clock counts from **container start**. SIGTERM handler, stop starting work at a margin, hard exit.
- **Checked:** 385 tests pass in Docker. In the midpoint evaluation 10 of 10 runs were valid, at $0.30-0.39 and 8:29-9:32 of 10 min.

> Notes: (40 s) Sources: report.md "Design decisions", docs/STATUS.md. Tested with arena's
> real isolated worker on deliberately broken cores: raise, sleep, infinite loop, numpy
> import, print flood. A hanging HTTP call cannot keep the process alive past the deadline
> because of the hard exit. Task C was checked at reduced size (C-lite, $2, 900 s): 865 s,
> $1.18, check PASS, 32-0-0 against the baselines.

---

## 5. Evidence 1: calibration and experiment 1

**Calibration** (one-shot strategy from the beta spec, 128 matches each):

- luna at high effort returns **no code**; luna at medium is slower and weaker than at low.
- **sol @ low = sol @ medium = astra** on first drafts (104 / 104 / 106 wins of 128). So we buy more drafts and rounds, not more effort.

**Experiment 1** (24 task-B runs, 3 games, one feature off per arm). Net per strategy in the cross-tournament:

| arm | total |
|-----|------:|
| full pipeline | +63.5 |
| no sparring draft | **+113** (removed) |
| no improvement | -61 |
| one-shot baseline | -115.5 |

- Full vs one-shot head-to-head: **48-0-0** (beta), **46-1-1** (gamma).
- Improvement rounds are the biggest single gain. The sparring draft did not help, so it is gone.

> Notes: (55 s) Sources: docs/STATUS.md "What we measured" and "Experiment 1" (24 runs,
> beta, gamma, epsilon). The one-shot arm is what a typical team ships: one draft plus the
> guard. Honest caveat: 2 runs per arm is noisy, so we trust directions that agree across
> games. Experiment 2 (18 runs) kept the extra self-play seeds and improvement at medium
> effort (low was worse).

---

## 6. Evidence 2: the midpoint evaluation and the bug it exposed

Organisers' midpoint (task B, beta + a hidden game h1, 4 teams, 5 runs each): we are **2nd of 4**.

- On h1 our best strategy was **the best of the whole field** (94.8%).
- But 2 of our 5 h1 runs were weak (29%, 38%). **Variance between runs is our main loss.**
- **Cause we found:** the simulator check rejected correct simulators of games with hidden information (`list[int | None]`). Those runs got no self-play and no improvement.
- **Fix:** a field may take any kind the spec's examples show. Replay of 51 saved simulators, no LLM: delta 0 -> 3 verified, held-out theta 0 -> 6.
- **Real run with the fix** (delta, task B): simulator verified first try, PASS, and it beats the 3 pre-fix strategies **44-1-15**.

> Notes: (60 s) Sources: docs/STATUS.md "Fix (14:00)" and the midpoint report. We scored
> +1,209 (beta +760, h1 +449); the first team scored +1,645, mostly on beta. If our two weak
> h1 runs had been at our median, h1 would be about +850 (an estimate). We cannot prove the
> bug caused those two runs without their logs, but it is the most likely kind of failure
> and it was real: 6 of 24 experiment-4 runs had no verified simulator. The fix acts in A,
> B and C because they share the check.

---

## 7. We measure, we cut; and against overfitting

Every feature stays only if a rule written **before the runs** says so.

| experiment | feature | result |
|---|---|---|
| 1 | sparring draft | no gain, **removed** |
| 2 | self-play polish, improvement at medium | **kept** |
| 3 | budget rescue | **kept**, weak evidence (3 of 13 attempts fitted) |
| 4 | plan first; five drafts | both **rejected** |

- **Held-out games** (zeta, theta): never used for tuning. Full beats one-shot **44-1-3** (theta) and **32-0-16** (zeta).
- Plan first won on theta (+24) but lost on the tuning games (-15 vs +18), so it was cut.
- **Robust ranking** and self-play under the delivered file's own time limits, both seats, many seeds.
- **No game-specific content** in the harness; a test enforces it for prompts.

> Notes: (45 s) Sources: docs/STATUS.md experiments 1-4, report.md. Experiments 3 and 4 were
> 14 and 24 runs, task B. All 24 experiment-4 strategies pass the check, 0 forfeits.
> Nothing persists between evaluation runs; learning across games happens offline.
> Likely questions: how do you trust the simulator (selftest against worked examples),
> what if the game has hidden information (this is exactly the bug we fixed), what stops
> overspending (worst-case reservation).

---

## 8. What we would do next

- **More runs of the plan-first variant.** Its held-out result was strong (theta +24, 12-0-0 against both other arms), but one run per arm cannot settle it.
- **A stronger reference opponent** to measure progress on beta. Our dev baselines are too weak: the best team at the midpoint beat us on beta.
- **Prompt caching** for task B: put the spec where it is cached. Estimated saving about $0.03-0.04 per run, not measured yet.
- **More runs per arm** before trusting small effects. One or two runs is noisy.

**Takeaway:** verify, select, guard, and measure before you keep a feature. The LLM writes the code; the harness decides what ships.

> Notes: (30 s) Sources: docs/STATUS.md experiment 4 and "Open proposals", facts from the
> midpoint report. Close on the takeaway and open for questions. No promises beyond these
> items.
