from pathlib import Path

from testgames.delta.baselines import DoNothing, GreedyBuilder, RandomStrategy
from testgames.delta.game import DeltaGame

GAME = DeltaGame
BASELINES = [DoNothing, RandomStrategy, GreedyBuilder]
SPEC = Path(__file__).with_name("spec.md")

__all__ = ["BASELINES", "GAME", "SPEC", "DeltaGame"]
