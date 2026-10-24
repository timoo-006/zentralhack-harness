"""Champion selection in the pipeline: who becomes champion, and who is protected."""

from types import SimpleNamespace as NS

from harness import pipeline


def _run(rows: list, h2h=(2, 0, 2)):
    run = pipeline.Run.__new__(pipeline.Run)  # no LLM, no sandbox: only the selection logic
    run.profile = NS(robust_rank=True)
    run.events = []
    run.event = lambda kind, **data: run.events.append(kind)
    run.promoted = []

    def promote(c, reason):
        run.promoted.append((c.id, reason))
        run.champion = c
        run.elected = run.elected or reason.startswith("tournament")

    run._promote = promote
    run.cands = {"a": NS(id="a"), "b": NS(id="b")}
    run.rows = rows
    run.ranking = lambda: run.rows
    run.tournament = NS(head_to_head=lambda x, y: h2h)
    return run


def test_provisional_champion_is_not_protected() -> None:
    # The first draft to finish is only provisional: a better-ranked draft takes over even
    # within the noise margin (experiment 1: 2 of 18 runs delivered the weaker draft).
    run = _run([(0.80, 0.5, 10.0, "a"), (0.79, 0.5, 5.0, "b")])
    run.champion, run.elected = run.cands["b"], False
    run.promote_best()
    assert run.promoted == [("a", "tournament")]
    assert run.elected


def test_elected_champion_is_protected_within_the_margin() -> None:
    run = _run([(0.81, 0.5, 5.0, "b"), (0.80, 0.5, 10.0, "a")])
    run.champion, run.elected = run.cands["a"], True
    run.promote_best()
    assert run.champion.id == "a" and run.events[-1] == "kept_champion"


def test_clearly_better_challenger_replaces_elected_champion() -> None:
    gain = pipeline.CLEAR_MARGIN + 0.01
    run = _run([(0.80 + gain, 0.5, 5.0, "b"), (0.80, 0.5, 10.0, "a")], h2h=(0, 0, 4))
    run.champion, run.elected = run.cands["a"], True
    run.promote_best()
    assert run.champion.id == "b"


def test_confirmed_provisional_champion_becomes_elected() -> None:
    run = _run([(0.80, 0.5, 10.0, "a"), (0.79, 0.5, 5.0, "b")])
    run.champion, run.elected = run.cands["a"], False
    run.promote_best()
    assert run.promoted == [] and run.elected
