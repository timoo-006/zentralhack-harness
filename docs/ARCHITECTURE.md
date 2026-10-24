# Architecture: module contracts

> The build contract for `src/harness/`. Every module is written against this file. If you
> change an interface, change it here first. Design rationale is in [`PLAN.md`](PLAN.md).

## 0 One run, end to end

```
t=0   __main__     clock starts (process age from /proc), SIGTERM handler, read task.json,
                   write FALLBACK strategy (guard with no core: plays None) -> /output/strategy.py
      specdoc      deterministic spec parse: python code blocks, literal example observations,
                   observation key schema, constants
      pipeline     profile from task.json (budget, deadline, models) — never from the task letter
batch 1 (<= 4 concurrent LLM calls)
      simulator    LLM writes sim.py (Game + action_report + selftest), from the spec
      strategies   LLM writes 2-3 candidate strategies with different "personas"
verify
      probe        every candidate on every example observation (subprocess, stdlib only)
      sim checks   selftest() + generic checks + fuzz; repair loop on failure
      repair       failing candidates get one repair call with the traceback
select
      tournament   round robin on sim.py: candidates + idle, both seats, many seeds
      promote      best by win rate -> guard.assemble -> atomic write to /output/strategy.py
improve (while time and money allow)
      improve      strongest model gets champion code + tournament diagnostics -> challenger
      tournament   challenger vs pool; promote if better
finalize at deadline - margin: final probe of the written file, exit 0
```

**Invariants** (never break):

1. `/output/<strategy_file>` holds a valid strategy from the first second; it is only ever
   replaced with `os.replace` from a temp file in the same directory, and only after the
   replacement passed `sandbox.probe` on the example observations (and the sim, if verified).
2. No LLM call is made unless `Budget.reserve` accepted its worst-case cost.
3. No harness code path depends on the game: everything game-specific comes from the spec,
   through the LLM, or from the spec's parsed literals.
4. The harness process itself never crashes: every stage is wrapped; on any exception the run
   continues with what it has, and finally exits 0.

## 1 `clock.py`

```python
IMPORT_MONO: float         # time.monotonic() when clock.py was imported

class Clock:
    def __init__(self, deadline_s: float | None, *, margin_s: float = 12.0,
                 default_deadline_s: float = 600.0, container: bool = False) -> None
        # start = IMPORT_MONO - process_age_s('1' if container else 'self')
    deadline_s: float          # task value, or default_deadline_s if None
    def elapsed(self) -> float                 # seconds since container start (best estimate)
    def remaining(self) -> float               # deadline_s - margin_s - elapsed(); may be < 0
    def hard_remaining(self) -> float          # deadline_s - elapsed()  (no margin)
    def frac(self) -> float                    # elapsed() / deadline_s

def process_age_s(pid: str = 'self') -> float
    # How long ago process `pid` started, at the moment clock.py was imported. Linux:
    # (uptime from /proc/uptime) - (starttime from /proc/<pid>/stat, field 22, in clock ticks
    # / os.sysconf('SC_CLK_TCK')); parse field 22 after the last ')' (comm may contain
    # spaces). In the container (no CLI argument), pid '1' is the container's init, so this
    # measures from container start, which is when the deadline starts counting. Locally
    # (harness TASK_DIR) use 'self': PID 1 is the OS init there. 0.0 on any error or on
    # non-Linux. Clamped to [0, 120].

def install_sigterm_handler(on_term: Callable[[], None]) -> None
    # SIGTERM -> call on_term() (best effort, exceptions swallowed) then os._exit(0).
    # No-op on platforms without SIGTERM handling issues (Windows: just install if possible).
```

## 2 `budget.py`

```python
@dataclass
class Reservation:
    model: str
    amount: float        # USD reserved (worst case)
    id: int

class Budget:
    def __init__(self, limit_usd: float | None, prices: dict[str, dict], *,
                 safety_frac: float = 0.90, default_limit_usd: float = 1.0) -> None
    limit: float                                  # usable = limit_usd * safety_frac
    def worst_case(self, model: str, input_tokens: int, max_output_tokens: int) -> float
        # upper bound: input at full (uncached) input price, or long_context prices if
        # input_tokens > long_context.above_input_tokens; output at output price.
    def reserve(self, model: str, input_tokens: int, max_output_tokens: int) -> Reservation | None
        # thread-safe; None if spent + reserved + worst_case > limit
    def settle(self, r: Reservation, usage) -> float
        # thread-safe; actual = harness.pricing.cost(usage, prices[model]); releases the
        # reservation; adds actual to spent; returns actual. usage None -> keep the full
        # reservation as spent (a failed/timed-out call may have been billed).
    def release(self, r: Reservation) -> None     # call made no request at all: free it
    spent: float; reserved: float
    def remaining(self) -> float                  # limit - spent - reserved
    def can_afford(self, model, input_tokens, max_output_tokens) -> bool

def estimate_tokens(text: str) -> int             # conservative: ceil(len(text) / 3.0) + 50
```

## 3 `llm.py`

```python
@dataclass
class LLMResult:
    ok: bool                  # a response came back (possibly incomplete)
    text: str                 # response.output_text ('' on failure)
    model: str
    status: str               # 'completed' | 'incomplete' | 'error' | 'skipped_budget' | 'skipped_time'
    usage: dict | None
    cost: float
    secs: float
    error: str | None
    label: str

class LLM:
    def __init__(self, task: dict, budget: Budget, clock: Clock, log_path: Path,
                 client=None, max_concurrency: int = 4) -> None
        # client None -> openai.OpenAI(max_retries=0). The semaphore caps in-flight calls at 4.
    def call(self, *, label: str, model: str, instructions: str, input: str | list,
             effort: str = 'medium', max_output_tokens: int = 16000,
             timeout_s: float | None = None) -> LLMResult
        # 1. refuse models not in task['allowed_models'] (status 'error').
        # 2. effective timeout = min(timeout_s or inf, clock.remaining() - 3); if < 20 s ->
        #    status 'skipped_time', no request.
        # 3. reserve worst case (estimate_tokens(instructions + input text)); None ->
        #    'skipped_budget', no request.
        # 4. client.responses.create(model=..., instructions=..., input=...,
        #    reasoning={'effort': effort}, max_output_tokens=..., store=False,
        #    timeout=effective_timeout). Function tools are not used.
        # 5. retry up to 2x on rate-limit / connection / 5xx errors with short backoff, only if
        #    time and budget still allow. Never retry 4xx other than 429.
        # 6. settle the reservation with response.usage; on exception keep it spent.
        # 7. append one JSON line to log_path (thread-safe): label, model, effort, status,
        #    secs, usage, cost, spent_total, error, the full output text, and the input text
        #    (input once per distinct sha1, else just the hash).
        # Never raises.
    def models(self) -> list[str]                     # allowed models, cheapest first
    def cheapest(self) -> str
    def strongest(self) -> str                        # most expensive allowed model

def extract_code(text: str) -> str | None
    # The longest fenced ```python (or ```py, or bare ```) block; if the text has no fence but
    # parses as Python with a class in it, the whole text. None otherwise.
```

## 4 `specdoc.py`

```python
@dataclass
class SpecInfo:
    code_blocks: list[str]                 # every fenced code block, any language
    python_blocks: list[str]               # fenced ```python blocks
    constants: dict[str, Any]              # NAME = <literal> assignments in python blocks
    example_observations: list[dict]       # see below
    obs_keys: list[str] | None             # the observation's key set (schema or examples)
    action_examples: list[Any]             # inline `...` code spans / blocks that are literal
                                           # lists or tuples (e.g. [(0, 1, 10), (0, 3, 10)])

def parse_spec(spec: str) -> SpecInfo
```

- Parse each python block with `ast.parse` (skip blocks that fail). For every top-level
  `Expr` and `Assign` value, try `ast.literal_eval` on the node. Literal dicts are candidate
  observations; `NAME = literal` assignments go to `constants`.
- A dict display whose keys are all string constants but whose values are not literal (e.g.
  `{"turn": int, ...}`) is a **schema**: its keys are a candidate `obs_keys`.
- `example_observations`: the literal dicts whose key set equals the most common key set among
  literal dicts with >= 3 keys (or matches a schema's keys). Values containing `Ellipsis`
  (`[...]  # unchanged`) are replaced by the same key's value from another example without
  Ellipsis; if none exists, the example is dropped.
- `obs_keys`: the schema's keys if a schema exists, else the examples' common key set.
- Never raises; on failure returns empty fields.

## 5 `guard.py` — the never-forfeit wrapper

```python
def assemble(core_src: str | None, *, strategy_class: str = 'Strategy',
             core_class: str = 'Strategy', fallback_src: str | None = None,
             fallback_class: str = 'Strategy', act_limit_s: float = 0.80,
             core_share: float = 0.75, time_budget_s: float = 0.25,
             header: str = '') -> str
    # Returns the source of a self-contained strategy file (stdlib only).
```

The generated file:

- embeds each source as a Python string literal (`repr(src)`), and loads it at import into a
  fresh `types.ModuleType` registered in `sys.modules` under a private name (so dataclasses,
  typing and pickle work), with `print` replaced by a no-op in that module and the module
  global `TIME_BUDGET` set to `time_budget_s`. Any exception while loading -> that part is
  `None`. Import-time work is also limited by a timer (5 s).
- defines `class <strategy_class>` with `__init__(self, player_id)` and `act(self, obs)`:
  - `__init__`: builds core and fallback instances (each in try/except BaseException, each
    under a 3 s timer); never raises.
  - `act`: runs `core.act(obs)` under a hard timer of `act_limit_s * core_share`; on timeout,
    exception, or a result that is not a plain literal (it must round-trip
    `ast.literal_eval(repr(x)) == x` and `len(repr(x)) <= 900_000`), runs the fallback under
    the remaining time; if that fails too, returns `None`. Never raises.
  - hard timer = `signal.setitimer(signal.ITIMER_REAL, t)` with a handler raising
    `_GuardTimeout(BaseException)` (a BaseException, so `except Exception` in LLM code does not
    swallow it); always cancelled in `finally`. Only used where `signal.setitimer` exists and
    we are on the main thread; otherwise no hard timer (local Windows dev).
  - keeps counters, exposed by `guard_stats() -> dict`: turns, core_ok, core_exceptions,
    core_timeouts, core_bad_output, fallback_used, none_used, max_core_s,
    first_core_traceback (str | None).
- `assemble(None)` (no core, no fallback) is the t=0 fallback: always returns `None`.

```python
def write_atomic(path: Path, text: str) -> None    # tmp file in path.parent, fsync, os.replace
```

## 6 `sandbox.py` + `runner_child.py` — run untrusted code in subprocesses

`runner_child.py` is a **stdlib-only** script, run as
`[sys.executable, '-I', '-S', runner_child.py, job.json]`; it prints one JSON object on stdout.
`-S` keeps site-packages out, so a strategy that imports a third-party package fails here
exactly as it would in the tournament.

```python
@dataclass
class ProbeResult:
    ok: bool                     # loaded, and every call returned without exception/timeout
    load_error: str | None       # traceback if the file/class failed to load or __init__ raised
    calls: list[dict]            # per observation: {player_id, ok, secs, action_repr (<=300 chars),
                                 #   error (traceback | None), literal_ok}
    max_secs: float

def probe(strategy_path: Path, observations: list[dict], *, strategy_class: str = 'Strategy',
          player_ids: tuple[int, ...] = (0, 1), act_timeout_s: float = 1.0,
          timeout_s: float = 60.0, raw: bool = False) -> ProbeResult
    # Instantiates the class once per player id and calls act on every observation in order.
    # raw=True: the file is a bare LLM core (no guard): exceptions are reported with tracebacks.
    # On Linux the child interrupts act calls over act_timeout_s * 2 with setitimer when raw.

@dataclass
class MatchResult:
    players: list[str]           # entry ids, by seat
    seed: int
    scores: list[float] | None   # None if the simulator failed
    winner: int | None           # seat, None = draw / invalid
    turns: int
    sim_error: str | None        # traceback from the simulator: the match does not count
    player_stats: list[dict]     # per seat: {max_act_s, mean_act_s, exceptions, timeouts,
                                 #   forfeit_turn, first_error, malformed, illegal,
                                 #   guard_stats (dict | None)}

def play_matches(sim_path: Path, entries: dict[str, Path], pairings: list[tuple[str, str, int]],
                 *, strategy_class: str = 'Strategy', sim_class: str = 'Game',
                 act_timeout_s: float = 1.0, max_turns: int = 5000, workers: int = 2,
                 timeout_s: float = 300.0, draw_tolerance: float = 0.01) -> list[MatchResult]
    # pairings: (id_seat0, id_seat1, seed). entries: id -> strategy file. The special id
    # 'idle' needs no file: it always returns None.
    # Splits pairings into `workers` child processes; each child plays its share in order:
    # game = Game(seed); per turn: obs_i = game.observe(i) (must be a literal: round-trip
    # check), act_i timed; exception or act over act_timeout_s -> that seat forfeits: plays
    # None for the rest of the match (like arena). After game.step(actions), if the game has
    # action_report(i) -> {'well_formed': bool, 'n_illegal': int}, accumulate malformed turns
    # and illegal moves. Stop at is_over() or max_turns. winner: higher score wins unless
    # |s0 - s1| <= draw_tolerance * (|s0| + |s1|) (then None); a seat that forfeited loses
    # unless both did. Children that exceed timeout_s are killed; their unfinished matches
    # come back with sim_error='timeout'.

def run_selftest(sim_path: Path, timeout_s: float = 60.0) -> tuple[bool, str]
    # imports sim.py in a child, calls selftest() -> list[str] failures; (passed, report)

def check_simulator(sim_path: Path, spec_info: SpecInfo, *, seeds=(0, 1, 2),
                    timeout_s: float = 60.0) -> tuple[bool, list[str]]
    # generic checks, no LLM: Game(seed) constructs; observe(0/1) are literal dicts whose keys
    # equal spec_info.obs_keys (when known) with matching value types against the examples
    # (int vs float tolerant: int where example int, number where example float, list/tuple
    # kept distinct); playing idle-vs-idle and random-garbage-vs-idle to the end raises
    # nothing; scores() is a list of 2 finite floats; is_over() becomes True within
    # max_turns; same seed -> same first observation (determinism). Returns (ok, problems).
```

## 7 `tournament.py`

```python
@dataclass
class Standing:
    id: str
    played: int; wins: int; draws: int; losses: int
    points: float                 # wins + 0.5 * draws
    win_rate: float               # points / played (0 if none)
    mean_margin: float            # mean (own - opponent) score, per match
    problems: dict                # summed exceptions, timeouts, malformed, illegal, forfeits

class Tournament:
    def __init__(self, sim_path: Path, workdir: Path, *, seeds: list[int], workers: int = 2,
                 act_timeout_s: float = 1.0) -> None
    def add(self, entry_id: str, strategy_path: Path | None) -> None   # None only for 'idle'
    def run(self, *, budget_s: float) -> None
        # plays every not-yet-played (a, b, seed) for all pairs of entries, both seats,
        # within budget_s (estimates match cost from earlier matches; plays seeds in order so
        # a partial run is balanced). Results are cached: adding an entry later only plays
        # its new pairings.
    def standings(self) -> list[Standing]               # sorted best first
    def head_to_head(self, a: str, b: str) -> tuple[int, int, int]   # wins, draws, losses of a
    def report(self, focus: str | None = None, max_chars: int = 4000) -> str
        # plain-text table for prompts and logs: standings, per-opponent results of `focus`,
        # per-seat results, problem counters, first errors.
    results: list[MatchResult]
```

## 8 Prompts (`src/harness/prompts/`)

All calls share one stable prefix, for prompt caching: `instructions = system.md + playbook.md`,
`input = [spec message, task message]`, where the spec message is identical in every call and
the task message is stage specific. Files:

- `system.md` — role, the strategy contract, the output format (one fenced ```python block,
  complete file), hard rules (stdlib only, never raise, speed, `TIME_BUDGET`).
- `playbook.md` — game-family lessons (the "lessons file"): curated offline from dev and test
  games. General, never naming alpha/beta specifics.
- `simulator.md` — write `sim.py`: `Game` (arena interface + `action_report`) and `selftest()`.
- `strategy.md` — write a candidate; `{persona}` slot for diversity.
- `repair.md` — fix a strategy or simulator given failures/tracebacks.
- `improve.md` — beat the current pool given the champion code and tournament report.

Formatting slots use `str.format` names in braces; literal braces in examples are doubled.

## 9 Strategy core contract (what the LLM writes)

```python
TIME_BUDGET = 0.25   # seconds per act; the harness overrides this module global

class Strategy:
    def __init__(self, player_id: int) -> None: ...   # cheap: no heavy work here
    def act(self, obs): ...                           # exact action format of the spec
```

Standard library only; deterministic or seeded randomness; build map data lazily on the first
`act`; use `time.perf_counter()` against `TIME_BUDGET` for any search loop.

## 10 Simulator contract (what the LLM writes, `sim.py`)

```python
class Game:
    n_players = 2
    def __init__(self, seed: int | None = None) -> None
    def observe(self, player_id: int) -> dict          # exactly the spec's observation
    def step(self, actions: list) -> None              # one action per player; malformed and
                                                       # illegal handled exactly as the spec says;
                                                       # never raises on any action value
    def is_over(self) -> bool
    def scores(self) -> list[float]
    def action_report(self, player_id: int) -> dict    # last step: {'well_formed': bool,
                                                       #             'n_illegal': int}

def selftest() -> list[str]   # reproduces the spec's worked examples; [] = all pass
```

## 11 Files and logs

```
/output/strategy.py            the deliverable (always valid)
/output/logs/transcript.jsonl  every LLM call (llm.py)
/output/logs/events.jsonl      pipeline decisions: stage start/end, promotions, spend, times
/output/logs/candidates/       every generated strategy/sim source, by id
/output/logs/tournament.txt    last tournament report
/tmp/work/                     scratch: sims, candidates, child job files
```
