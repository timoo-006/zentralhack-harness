from pathlib import Path

from testgames.iota.baselines import DoNothing, GreedyDispatch, RandomStrategy
from testgames.iota.game import IotaGame

GAME = IotaGame
BASELINES = [DoNothing, RandomStrategy, GreedyDispatch]
SPEC = Path(__file__).with_name("spec.md")

__all__ = ["BASELINES", "GAME", "SPEC", "IotaGame"]
