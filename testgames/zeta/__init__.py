from pathlib import Path

from testgames.zeta.baselines import DoNothing, GreedyOrders, RandomStrategy
from testgames.zeta.game import ZetaGame

GAME = ZetaGame
BASELINES = [DoNothing, RandomStrategy, GreedyOrders]
SPEC = Path(__file__).with_name("spec.md")

__all__ = ["BASELINES", "GAME", "SPEC", "ZetaGame"]
