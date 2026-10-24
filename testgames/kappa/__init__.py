from pathlib import Path

from testgames.kappa.baselines import DoNothing, GreedyMajority, RandomStrategy
from testgames.kappa.game import KappaGame

GAME = KappaGame
BASELINES = [DoNothing, RandomStrategy, GreedyMajority]
SPEC = Path(__file__).with_name("spec.md")

__all__ = ["BASELINES", "GAME", "SPEC", "KappaGame"]
