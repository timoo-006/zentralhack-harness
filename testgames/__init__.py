"""Our own games of the hackathon's game family, for testing the harness on games it was not
tuned on. Each subpackage is an arena game package (exports GAME, BASELINES and SPEC), e.g.

    arena make-task --game testgames.gamma --out tasks/gamma-B ...
    arena run --game testgames.delta --strategies testgames.delta.baselines:GreedyBuilder ...

Development and test use only: the harness itself must never import this package.
"""
