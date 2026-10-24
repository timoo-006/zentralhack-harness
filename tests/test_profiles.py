"""make_profile: which model runs at which effort, for the evaluation's three tasks."""

from types import SimpleNamespace as NS

import pytest

from harness import pipeline
from harness.llm import LLM

LUNA, SOL, ASTRA = "gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra"
PRICES = {
    LUNA: {"input": 0.1, "cached_input": 0.01, "cache_write": 0.125, "output": 0.5},
    SOL: {"input": 2.0, "cached_input": 0.1, "cache_write": 2.5, "output": 10.0},
    ASTRA: {"input": 10.0, "cached_input": 1.0, "cache_write": 12.5, "output": 50.0},
}
# The evaluation's tasks (docs/TASK.md, scripts/make_tasks.py): budget, deadline, models.
TASKS = {
    "A": (0.03, 180, [LUNA]),
    "B": (0.5, 600, [LUNA, SOL]),
    "C": (10.0, 1800, [SOL, ASTRA]),
}
TIERS = {"A": "tiny", "B": "mid", "C": "large"}


def _profile(task: str, flags: str | None = None) -> pipeline.Profile:
    budget, deadline, models = TASKS[task]
    t = {"budget_usd": budget, "deadline_s": deadline, "allowed_models": models}
    llm = LLM({**t, "prices": PRICES}, None, None, "/dev/null", client=object())
    return pipeline.apply_ablations(pipeline.make_profile(t, llm, NS(deadline_s=deadline)), flags)


def _roles(p: pipeline.Profile) -> list[tuple[str, str | None, str]]:
    roles = [("candidate", m, p.cand_effort) for m in p.cand_models]
    roles += [
        ("simulator", p.sim_model, p.sim_effort),
        ("simulator escalation", p.strong, p.sim_effort),
        ("analysis", p.analysis_model, p.analysis_effort),
        ("repair", p.repair_model, p.repair_effort),
        ("improvement", p.improve_model, p.improve_effort),
    ]
    if p.improve_parallel > 1:  # challengers k > 0 use the cheaper model at the same effort
        roles.append(("parallel challenger", p.cheap, p.improve_effort))
    return [r for r in roles if r[1] is not None]


@pytest.mark.parametrize("task", sorted(TASKS))
@pytest.mark.parametrize("flags", [None, *pipeline.ABLATIONS])
def test_luna_only_runs_at_low_effort(task: str, flags: str | None) -> None:
    # Calibration: luna at medium is slower and weaker than at low, at high it returns no code.
    p = _profile(task, flags)
    assert p.name == TIERS[task]
    for role, model, effort in _roles(p):
        assert model in TASKS[task][2], (role, model)
        if model == LUNA:
            assert effort == "low", (task, role, effort)


def test_unknown_flags_are_ignored() -> None:
    assert _profile("B", "no_such_flag, ,improve_low").ablated == ("improve_low",)
    assert _profile("B", "no_such_flag").__dict__ == _profile("B").__dict__
