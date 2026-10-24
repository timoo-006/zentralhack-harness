from pathlib import Path

from testgames.theta.baselines import DoNothing, GreedyScout, RandomStrategy
from testgames.theta.game import ThetaGame

GAME = ThetaGame
BASELINES = [DoNothing, RandomStrategy, GreedyScout]
SPEC = Path(__file__).with_name("spec.md")

__all__ = ["BASELINES", "GAME", "SPEC", "ThetaGame"]
