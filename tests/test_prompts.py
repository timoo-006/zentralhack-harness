"""Tests for the prompt templates in src/harness/prompts/: format slots, personas, generality."""

import json
import re
import string
from pathlib import Path

import pytest

PROMPTS = Path(__file__).resolve().parents[1] / "src" / "harness" / "prompts"

# template -> the only str.format slots it may use (None: used verbatim, never formatted)
SLOTS: dict[str, set[str] | None] = {
    "system.md": None,
    "playbook.md": None,
    "simulator.md": {"obs_keys", "examples_note"},
    "strategy.md": {"persona", "time_budget", "analysis"},
    "repair_strategy.md": {"code", "problems"},
    "repair_sim.md": {"code", "problems"},
    "improve.md": {"champion_code", "report", "analysis", "focus"},
    "analysis.md": set(),
}

# words that would tie a prompt to the dev games instead of the game family
FORBIDDEN = re.compile(r"drone|upkeep|alpha|beta", re.IGNORECASE)


def _read(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(SLOTS))
def test_template_slots(name: str) -> None:
    text = _read(name)
    assert text.strip()
    assert "\r" not in text
    allowed = SLOTS[name]
    if allowed is None:
        return
    fields = {f for _, f, _, _ in string.Formatter().parse(text) if f is not None}
    assert fields == allowed, f"{name}: slots {sorted(fields)} != {sorted(allowed)}"
    dummy = {k: f"<{k} {{with}} braces>" for k in allowed}
    out = text.format(**dummy)  # must not raise KeyError / IndexError / ValueError
    for k in allowed:
        assert f"<{k} {{with}} braces>" in out
    assert "{{" not in out


def test_strategy_time_budget_float() -> None:
    out = _read("strategy.md").format(persona="p", time_budget=0.25, analysis="(none)")
    assert "TIME_BUDGET = 0.25" in out


@pytest.mark.parametrize("name", [*sorted(SLOTS), "personas.json"])
def test_no_dev_game_specifics(name: str) -> None:
    hits = FORBIDDEN.findall(_read(name))
    assert not hits, f"{name} mentions {sorted(set(hits))}"


def test_personas() -> None:
    personas = json.loads(_read("personas.json"))
    assert isinstance(personas, list) and 4 <= len(personas) <= 8
    names = [p["name"] for p in personas]
    assert len(set(names)) == len(names)
    assert "safe" in names
    assert "sparring_naive" in names  # pipeline.SPARRING_PERSONA: self-play sparring partner
    for p in personas:
        assert set(p) == {"name", "persona"}
        assert isinstance(p["name"], str) and re.fullmatch(r"[a-z_]+", p["name"])
        assert isinstance(p["persona"], str) and len(p["persona"]) > 200
        # personas are inserted into strategy.md via str.format: any text is fine as a value
        assert _read("strategy.md").format(persona=p["persona"], time_budget=0.5, analysis="x")


def test_output_contract_mentioned() -> None:
    system = _read("system.md")
    for needle in ("TIME_BUDGET", "class Strategy", "```python", "standard library"):
        assert needle in system
    for name in ("simulator.md", "strategy.md", "repair_strategy.md", "repair_sim.md"):
        assert "```python block" in _read(name)
    assert "```python block" in _read("improve.md")
    assert "600 words" in _read("analysis.md")
    sim = _read("simulator.md")
    for needle in ("action_report", "selftest", "n_players", "well_formed", "n_illegal"):
        assert needle in sim
