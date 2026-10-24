"""Write the task directories we test the harness on: tasks/<game>-<profile>/.

Dev-only (imports arena). One task per game and evaluation profile, exactly what the harness
gets as /task (spec.md + task.json, made by arena.task.make_task):

    uv run python scripts/make_tasks.py                      # every importable game, A B C
    uv run python scripts/make_tasks.py --games beta --profiles B C-lite
    uv run python scripts/make_tasks.py --profile X=1.5,600,gpt-6-luna+gpt-6.1-sol

Games whose package does not import (yet) are skipped with a note. evaluate.py imports
GAMES and PROFILES from here.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Our own test games (testgames.*) live in the repository, not in site-packages.
for _p in (REPO, REPO / "tests"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

LUNA, SOL, ASTRA = "gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra"

# game key (= task.json game_name) -> game package (exports GAME, BASELINES, SPEC)
GAMES: dict[str, str] = {
    "alpha": "arena_games_dev.alpha",
    "beta": "arena_games_dev.beta",
    "gamma": "testgames.gamma",
    "delta": "testgames.delta",
}


@dataclass(frozen=True)
class Profile:
    budget_usd: float
    deadline_s: float
    models: tuple[str, ...]


# The evaluation's three tasks (docs/TASK.md), plus cheaper variants for iterating.
PROFILES: dict[str, Profile] = {
    "A": Profile(0.03, 180, (LUNA,)),
    "B": Profile(0.5, 600, (LUNA, SOL)),
    "C": Profile(10.0, 1800, (SOL, ASTRA)),
    "C-lite": Profile(2.0, 900, (SOL, ASTRA)),
}
DEFAULT_PROFILES = ("A", "B", "C")


def parse_profile(text: str) -> tuple[str, Profile]:
    """Parse NAME=BUDGET,DEADLINE,MODEL[+MODEL...], e.g. C-lite=2,900,gpt-6.1-sol+gpt-6-astra."""
    name, sep, rest = text.partition("=")
    parts = rest.split(",")
    if not sep or not name or len(parts) != 3 or not parts[2]:
        raise argparse.ArgumentTypeError(
            f"expected NAME=BUDGET,DEADLINE,MODEL[+MODEL], got {text!r}"
        )
    try:
        profile = Profile(float(parts[0]), float(parts[1]), tuple(parts[2].split("+")))
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"{text!r}: {e}") from None
    return name, profile


def import_game(key: str, ref: str | None = None):
    """The game package for `key` (or module `ref`); None if it does not import or has no
    spec file (yet)."""
    from arena.loading import load_game_package

    try:
        package = load_game_package(ref or GAMES[key])
    except Exception as e:  # anything a half-written game package raises on import
        print(f"skip {key}: {ref or GAMES.get(key)} not importable ({e})", file=sys.stderr)
        return None
    if not Path(package.SPEC).is_file():
        print(f"skip {key}: no spec file {package.SPEC}", file=sys.stderr)
        return None
    return package


def make(key: str, package, name: str, profile: Profile, out_root: Path) -> Path:
    from arena.task import make_task

    out = out_root / f"{key}-{name}"
    make_task(
        Path(package.SPEC),
        out,
        game_name=key,
        budget_usd=profile.budget_usd,
        deadline_s=profile.deadline_s,
        allowed_models=list(profile.models),
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--games", nargs="+", default=list(GAMES), help=f"game keys (default: {list(GAMES)})"
    )
    parser.add_argument(
        "--game-ref",
        action="append",
        default=[],
        metavar="KEY=MODULE",
        help="add or override a game package, e.g. epsilon=testgames.epsilon",
    )
    parser.add_argument(
        "--profiles",
        nargs="+",
        default=list(DEFAULT_PROFILES),
        help=f"profile names (known: {list(PROFILES)}; default: A B C)",
    )
    parser.add_argument(
        "--profile",
        action="append",
        default=[],
        type=parse_profile,
        metavar="NAME=BUDGET,DEADLINE,MODELS",
        help="define (and select) a custom profile; models joined by '+'",
    )
    parser.add_argument("--out", type=Path, default=REPO / "tasks", help="default: tasks/")
    args = parser.parse_args(argv)

    refs = dict(GAMES)
    for item in args.game_ref:
        key, sep, ref = item.partition("=")
        if not sep:
            parser.error(f"--game-ref: expected KEY=MODULE, got {item!r}")
        refs[key] = ref
        if key not in args.games:
            args.games.append(key)
    profiles = dict(PROFILES)
    selected = list(args.profiles)
    for name, profile in args.profile:
        profiles[name] = profile
        if name not in selected:
            selected.append(name)
    unknown = [p for p in selected if p not in profiles]
    if unknown:
        parser.error(f"unknown profiles {unknown}; define them with --profile")

    written = 0
    for key in args.games:
        package = import_game(key, refs.get(key))
        if package is None:
            continue
        for name in selected:
            p = profiles[name]
            out = make(key, package, name, p, args.out)
            written += 1
            models = "+".join(p.models)
            print(f"{out}  budget ${p.budget_usd:g}  deadline {p.deadline_s:g}s  {models}")
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
