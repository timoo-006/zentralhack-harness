"""Tests for harness.tournament: incremental, cached round robin on a simulator."""

import time
from pathlib import Path

from harness.guard import assemble
from harness.tournament import Tournament

FIXTURES = Path(__file__).parent / "fixtures"
BETA_SIM = FIXTURES / "beta_sim.py"
TINY_SIM = FIXTURES / "tiny_sim.py"


def test_round_robin_standings_and_cache(tmp_path):
    t = Tournament(TINY_SIM, tmp_path, seeds=[0, 1, 2], workers=2)
    t.add("lister", FIXTURES / "list_player.py")
    t.add("raiser", FIXTURES / "bad_raise_turn5.py")
    t.add("idle", None)
    t.run(budget_s=120)
    assert not t.pending()
    assert len(t.results) == 3 * 6  # 3 seeds x 3 ordered pairs x 2 seats
    rows = {s.id: s for s in t.standings()}
    assert t.standings()[0].id == "lister"
    assert rows["lister"].wins == 12 and rows["lister"].win_rate == 1.0
    assert rows["raiser"].problems["forfeits"] == 12
    assert rows["raiser"].problems["exceptions"] == 12
    assert rows["idle"].problems["malformed"] > 0
    assert t.head_to_head("lister", "raiser") == (6, 0, 0)
    assert t.head_to_head("raiser", "lister") == (0, 0, 6)
    assert rows["lister"].mean_margin > 0

    # cached: adding an entry only plays its own new pairings
    t.add("lister2", FIXTURES / "list_player.py")
    assert len(t.pending()) == 3 * 6
    t.run(budget_s=120)
    assert len(t.results) == 36
    assert t.head_to_head("lister", "lister2") == (0, 6, 0)

    report = t.report(focus="raiser")
    assert "Standings" in report and "Focus: raiser" in report
    assert "boom on turn 5" in report
    assert "as seat 0" in report
    assert len(t.report(max_chars=300)) <= 300


def test_budget_is_respected_and_seeds_play_in_order(tmp_path):
    t = Tournament(BETA_SIM, tmp_path, seeds=list(range(400)), workers=2)
    t.add("greedy", FIXTURES / "greedy_beta.py")
    t.add("idle", None)
    t.run(budget_s=0.01)  # no estimate yet: still one full round of the first seed
    assert sorted((r.players[0], r.seed) for r in t.results) == [("greedy", 0), ("idle", 0)]
    t.run(budget_s=0.0)
    assert len(t.results) == 2
    t0 = time.monotonic()
    t.run(budget_s=1.0)
    assert time.monotonic() - t0 < 2.5
    seeds = sorted({r.seed for r in t.results})
    assert seeds == list(range(len(seeds)))  # a prefix of the seed list
    assert 2 < len(t.results) < 800
    rows = {s.id: s for s in t.standings()}
    assert rows["greedy"].win_rate > 0.5


def test_sim_errors_do_not_count_and_are_reported(tmp_path):
    t = Tournament(TINY_SIM, tmp_path, seeds=[98, 0], workers=1)
    t.add("lister", FIXTURES / "list_player.py")
    t.add("idle", None)
    t.run(budget_s=60)
    rows = {s.id: s for s in t.standings()}
    assert rows["lister"].played == 2  # seed 98 matches are invalid
    assert "simulator bug at turn 3" in t.report()


def test_guarded_entries_report_guard_counters(tmp_path):
    core = (FIXTURES / "bad_raise_turn5.py").read_text(encoding="utf-8")
    path = tmp_path / "guarded.py"
    path.write_text(assemble(core), encoding="utf-8")
    t = Tournament(TINY_SIM, tmp_path, seeds=[0], workers=1)
    t.add("guarded", path)
    t.add("idle", None)
    t.run(budget_s=60)
    rows = {s.id: s for s in t.standings()}
    assert rows["guarded"].problems["forfeits"] == 0
    assert rows["guarded"].problems["core_exceptions"] == 2
    assert "guard:" in t.report(focus="guarded")


def test_readding_an_entry_with_a_new_file_replays_it(tmp_path):
    t = Tournament(TINY_SIM, tmp_path, seeds=[0], workers=1)
    t.add("a", FIXTURES / "bad_raise_turn5.py")
    t.add("idle", None)
    t.run(budget_s=60)
    assert {s.id: s for s in t.standings()}["a"].losses == 2
    t.add("a", FIXTURES / "list_player.py")
    assert len(t.pending()) == 2
    t.run(budget_s=60)
    assert {s.id: s for s in t.standings()}["a"].wins == 2


SLOW_SIM = """
import time


class Game:
    n_players = 2

    def __init__(self, seed=None):
        self.seed = seed
        self.turn = 0
        self.points = [0.0, 0.0]

    def observe(self, player_id):
        return {"turn": self.turn}

    def step(self, actions):
        if self.seed == 7:
            time.sleep(0.9)  # 30 turns: a match that never fits a small budget
        for i, a in enumerate(actions):
            self.points[i] += isinstance(a, list)
        self.turn += 1

    def is_over(self):
        return self.turn >= 30

    def scores(self):
        return list(self.points)
"""


def test_a_match_too_slow_for_the_budget_does_not_block_later_runs(tmp_path):
    sim = tmp_path / "slow_sim.py"
    sim.write_text(SLOW_SIM, encoding="utf-8")
    t = Tournament(sim, tmp_path, seeds=[7, 0, 1], workers=2)
    t.add("lister", FIXTURES / "list_player.py")
    t.add("idle", None)
    for _ in range(6):
        if not t.pending():
            break
        t.run(budget_s=1.0)
    valid = [r for r in t.results if r.sim_error is None]
    assert sorted(r.seed for r in valid) == [0, 0, 1, 1]  # the fast seeds were played
    assert not t.pending()  # the slow matches were given up after MAX_KILLS attempts
    assert {s.id: s for s in t.standings()}["lister"].played == 4
    assert "never finished within the time budget" in t.report()
