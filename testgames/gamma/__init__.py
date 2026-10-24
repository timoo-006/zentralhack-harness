from pathlib import Path

from testgames.gamma.baselines import DoNothing, GreedyHarvest, RandomStrategy
from testgames.gamma.game import GammaGame

GAME = GammaGame
BASELINES = [DoNothing, RandomStrategy, GreedyHarvest]
SPEC = Path(__file__).with_name("spec.md")

__all__ = ["BASELINES", "GAME", "SPEC", "GammaGame"]
