from pathlib import Path

from testgames.epsilon.baselines import DoNothing, GreedyCapture, RandomStrategy
from testgames.epsilon.game import EpsilonGame

GAME = EpsilonGame
BASELINES = [DoNothing, RandomStrategy, GreedyCapture]
SPEC = Path(__file__).with_name("spec.md")

__all__ = ["BASELINES", "GAME", "SPEC", "EpsilonGame"]
