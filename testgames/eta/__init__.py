from pathlib import Path

from testgames.eta.baselines import DoNothing, RandomStrategy, SafeGreedy
from testgames.eta.game import EtaGame

GAME = EtaGame
BASELINES = [DoNothing, RandomStrategy, SafeGreedy]
SPEC = Path(__file__).with_name("spec.md")

__all__ = ["BASELINES", "GAME", "SPEC", "EtaGame"]
