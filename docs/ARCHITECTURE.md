# Architecture: module contracts

> The build contract for `src/harness/`. Every module is written against this file. If you
> change an interface, change it here first. Design rationale is in [`PLAN.md`](PLAN.md);
> measurements are in [`STATUS.md`](STATUS.md).

## 0 One run, end to end

```
t=0   __main__     clock starts (process age from /proc), read spec.md and task.json,
                   write FALLBACK strategy (guard with no core: plays None) -> /output/strategy.py,
                   SIGTERM handler
      Run.__init__ clock, budget, LLM gate, specdoc parse, profile from task.json (budget,
                   deadline, models; never from the task letter), prompts
batch 1 (<= 4 concurrent LLM calls, submitted in priority order)
      simulator    LLM writes sim.py (Game + action_report + selftest), from the spec;
                   verified and repaired inside this step
      strategies   LLM writes 3-4 candidate strategies with different "personas"; each is
                   probed, played on the simulator when it exists, and repaired on failure
      analysis     (large profile only) strategic notes for the later calls
select
      tournament   round robin on sim.py: valid candidates + idle, both seats, several seeds
      promote      best by robust ranking -> guard.assemble -> final probe -> atomic write
improve (while time and money allow)
      challengers  champion code + tournament report -> new candidates (parallel in large)
      tournament   challengers vs pool; promote if clearly better (incumbent protection)
polish            time left after the last call: more self-play seeds, re-rank
finish            final probe of the written file, write logs, exit 0
```

Before a simulator exists, the first valid candidate is promoted as a provisional champion, so
a run that never gets a simulator still delivers a probed strategy. `__main__.run` returns 0 and
`main` ends with `os._exit`, so a request still in flight cannot keep the process alive past the
deadline. Locally (`harness TASK_DIR`) the output goes to `runs/<task>/<time>-<pid>/`, with a
`latest` link.

**Invariants** (never break):

1. `/output/<strategy_file>` holds a valid strategy from the first second; it is only ever
   replaced with `os.replace` from a temp file in the same directory, and only after the
   replacement passed the final probe (examples, and two matches against idle on the simulator
   when it exists).
2. No LLM call is made unless `Budget.reserve` accepted its worst-case cost.
3. No harness code path depends on the game: everything game-specific comes from the spec,
   through the LLM, or from the spec's parsed literals.
4. The harness process itself never crashes: every stage is wrapped; on any exception the run
   continues with what it has, and finally exits 0.

## 1 `clock.py`

```python
IMPORT_MONO: float         # time.monotonic() when clock.py was imported
MAX_AGE_S = 120.0          # process ages are clamped to [0, MAX_AGE_S]

class Clock:
    def __init__(self, deadline_s: float | None, *, margin_s: float = 12.0,
                 default_deadline_s: float = 600.0, container: bool = False) -> None
        # start = IMPORT_MONO - process_age_s('1' if container else 'self')
    deadline_s: float          # task value, or default_deadline_s if None / not positive finite
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
    # non-Linux. Clamped to [0, MAX_AGE_S].

def install_sigterm_handler(on_term: Callable[[], None], *, timeout_s: float = 15.0) -> None
    # SIGTERM -> run on_term() in a helper thread (exceptions swallowed), wait at most
    # timeout_s for it (the signal can interrupt the main thread while it holds a lock that
    # on_term needs), flush stdout/stderr, then os._exit(0). A second SIGTERM while on_term
    # runs is ignored. A no-op off the main thread or where SIGTERM cannot be handled.

def start_watchdog(clock: Clock, *, lead_s: float = 4.0, exit=os._exit) -> threading.Timer
    # Hard stop: a daemon timer calls exit(0) lead_s before the deadline (hard_remaining()),
    # whatever the run is doing. The stages stop by themselves margin_s (12 s) earlier; this
    # is the backstop for anything that blocks (a timeout is an invalid run at evaluation).
    # Safe because the strategy file is valid at every moment. __main__ starts it right after
    # the t=0 fallback file and the SIGTERM handler.
```

`__main__` imports `clock` before the heavy imports, so the age is measured as early as
possible.

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
        # limit_usd missing, negative or not finite -> default_limit_usd
    limit_usd: float                              # the task's budget
    limit: float                                  # usable = limit_usd * safety_frac
    def worst_case(self, model: str, input_tokens: int, max_output_tokens: int) -> float
        # upper bound of harness.pricing.cost: every input token at the highest input-side
        # price (uncached, cached or cache write: a cache write costs more than plain input),
        # every output token at the output price; above long_context.above_input_tokens the
        # higher of both price tiers. Unknown model or malformed price entry: infinity
        # (never affordable). Never raises.
    def reserve(self, model: str, input_tokens: int, max_output_tokens: int) -> Reservation | None
        # thread-safe; None if spent + reserved + worst_case > limit
    def settle(self, r: Reservation, usage) -> float
        # thread-safe; actual = harness.pricing.cost(usage, prices[model]); releases the
        # reservation; adds actual to spent; returns actual. usage None or unpriceable -> keep
        # the full reservation as spent (a failed/timed-out call may have been billed).
        # Settling twice books nothing twice.
    def release(self, r: Reservation) -> None     # call made no billable request: free it
    spent: float; reserved: float
    def remaining(self) -> float                  # limit - spent - reserved
    def can_afford(self, model, input_tokens, max_output_tokens) -> bool
    def in_flight(self) -> int                    # open reservations
    def summary(self) -> dict                     # numbers for logs

def estimate_tokens(text: str) -> int             # conservative: ceil(len(text) / 3.0) + 50
```

`reserved` is exactly 0.0 when no reservation is open, so float residue never looks like a
call in flight (a measured bug: a run once sat idle for about 285 s because of it).

`pricing.py` holds `cost(usage, price)`, the formula the evaluation charges by. Do not change
it.

## 3 `llm.py`

```python
MAX_CONCURRENCY = 4        # also enforced process-wide, across LLM instances

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
    MIN_CALL_S = 20.0; CLOCK_SLACK_S = 3.0; MAX_ATTEMPTS = 3
    def __init__(self, task: dict, budget: Budget, clock: Clock, log_path: Path,
                 client=None, max_concurrency: int = 4) -> None
        # client None -> openai.OpenAI(max_retries=0). A semaphore caps in-flight calls at 4.
    def call(self, *, label: str, model: str, instructions: str, input: str | list,
             effort: str = 'medium', max_output_tokens: int = 16000,
             timeout_s: float | None = None) -> LLMResult
        # 1. refuse models not in task['allowed_models'] (status 'error').
        # 2. effective timeout = min(timeout_s or inf, clock.remaining() - 3); if < 20 s ->
        #    status 'skipped_time', no request.
        # 3. reserve worst case (estimate_tokens(instructions + input text)); None ->
        #    'skipped_budget', no request.
        # 4. wait for a concurrency slot (at most as long as a request could still run), then
        #    client.responses.create(model=..., instructions=..., input=...,
        #    max_output_tokens=..., store=False, timeout=effective_timeout), plus
        #    reasoning={'effort': effort} when effort is set. Function tools are not used.
        # 5. retry (up to 3 attempts in all) on rate-limit / connection (incl. timeout) / 5xx
        #    errors with backoff (Retry-After if <= 20 s), only if time and budget still allow;
        #    each retry reserves again. Never retry 4xx other than 429. timeout_s bounds the
        #    whole request including retries.
        # 6. settle the reservation with response.usage. A failed attempt keeps its
        #    reservation as spent unless the server rejected it with a 4xx (429 included):
        #    then it is released.
        # 7. append one JSON line to log_path (thread-safe): label, model, effort,
        #    max_output_tokens, attempts, t, status, secs, usage, cost, spent_total,
        #    reserved_total, error, the output text, and instructions and each input message
        #    as {sha1, chars, role, text}, where text is present only the first time that
        #    sha1 is logged.
        # Never raises.
    def models(self) -> list[str]                     # allowed and priced models, cheapest first
                                                      # (input + output price, then name)
    def cheapest(self) -> str
    def strongest(self) -> str                        # most expensive allowed model

def extract_code(text: str) -> str | None
    # The Python file in an answer: among the fenced ```python / ```py / bare ``` blocks (also
    # ~~~, an unclosed last fence, indented blocks dedented) the longest one, preferring blocks
    # that define a class, then blocks that parse (a file with a syntax error still beats a
    # snippet, so a repair sees the error). With no fence: the whole text if it parses as
    # Python and defines a class. None otherwise.
```

## 4 `specdoc.py`

```python
@dataclass
class SpecInfo:
    code_blocks: list[str]                 # every fenced code block, any language
    python_blocks: list[str]               # fenced python / py / pycon blocks
    constants: dict[str, Any]              # NAME = <literal or simple arithmetic>; first wins
    example_observations: list[dict]       # see below; at most 32
    obs_keys: list[str] | None             # the observation's key set (schema or examples)
    action_examples: list[Any]             # literal lists / tuples / non-observation dicts from
                                           # inline `...` spans and block-level expressions
                                           # (e.g. [(0, 1, 10), (0, 3, 10)]); at most 60
    schemas: list[list[str]]               # keys of every schema-like dict in the spec

def parse_spec(spec: str) -> SpecInfo
```

- Literals are searched in python, JSON and untagged / plain-text blocks (JSON `true` /
  `false` / `null` accepted outside python blocks). Python blocks go through `ast.parse`
  (console prompts `>>>` stripped); a block that does not parse is scanned for top-level
  bracketed segments. Every top-level `Expr` and `Assign` value is tried with
  `ast.literal_eval`. Blocks over 300,000 characters are kept but not parsed.
- A dict display whose keys are all string constants but whose values are not literal (e.g.
  `{"turn": int, ...}`), a dict whose values are type names (`{"turn": "int"}`) and a
  `TypedDict` class are **schemas**: their keys are a candidate `obs_keys`.
- Dicts assigned to an upper-case name are tables (unit types, costs), not observations,
  unless nothing else qualifies. Observation candidates have at least 3 keys.
- If a schema matches the key set of some literal dict, the largest such schema wins and those
  dicts are the examples. Otherwise the most common key set among the literal dicts is used.
  If a larger schema exists and the literal dicts show only part of it (a subset, or less than
  half overlap), `obs_keys` is the schema and there are no examples: partial dicts are not
  usable examples.
- `example_observations`: the literal dicts with the chosen key set, deduplicated. Values
  containing `Ellipsis` (`[...]  # unchanged`) are replaced by the same key's value from the
  nearest other example without Ellipsis (earlier ones first); if none exists, the example is
  dropped.
- `constants` also take simple arithmetic of numbers and known constants, and tuple
  unpacking; the first assignment of a name wins.
- Never raises; on failure returns empty fields.

## 5 `guard.py`: the never-forfeit wrapper

```python
def assemble(core_src: str | None, *, strategy_class: str = 'Strategy',
             core_class: str = 'Strategy', fallback_src: str | None = None,
             fallback_class: str = 'Strategy', act_limit_s: float = 0.80,
             core_share: float = 0.75, time_budget_s: float = 0.25,
             header: str = '') -> str
    # Returns the source of a self-contained strategy file (stdlib only). header lines become
    # comments. A strategy_class that is not an identifier becomes 'Strategy'.

def sanitize(src: str) -> str
    # Rewrites a bare `except:` and every `BaseException` name to `Exception`, so LLM code
    # cannot swallow the guard's timeout. Unchanged if nothing needs it or it does not parse.
```

The pipeline delivers files assembled with `time_budget_s=0.25` and `act_limit_s=0.70`
(`FINAL_TIME_BUDGET_S`, `FINAL_ACT_LIMIT_S`); self-play uses the same two.

The generated file:

- embeds each source (core and fallback, both passed through `sanitize`) as a Python string
  literal, and loads it at import into a fresh `types.ModuleType` registered in `sys.modules`
  under a private name (so dataclasses, typing and pickle work), with `print` replaced by a
  no-op in that module and the module global `TIME_BUDGET` set to `time_budget_s`. Any
  exception while loading -> that part is `None`.
- limits start-up so that import plus `__init__` stay under arena's 10 s: all imports together
  4.5 s (the fallback at most 1.5 s of it), core `__init__` 2.5 s, fallback `__init__` 1.5 s.
- defines `class <strategy_class>` with `__init__(self, player_id)` and `act(self, obs)`:
  - `__init__`: builds core and fallback instances (each in try/except BaseException, each
    under its timer); never raises.
  - `act`: runs `core.act(obs)` under a hard timer of `act_limit_s * core_share`; on timeout,
    exception, or a result that is not a plain literal (it must round-trip
    `ast.literal_eval(repr(x)) == x` and be at most 900,000 characters and bytes), runs the
    fallback with the remaining time of `act_limit_s` (skipped if under 50 ms are left), on a
    copy of the observation taken before the core ran; if that fails too, returns `None`.
    Never raises.
  - hard timer = `signal.setitimer(signal.ITIMER_REAL, t, 0.05)` with a handler raising
    `_GuardTimeout(BaseException)` (a BaseException, so `except Exception` in LLM code does not
    swallow it). After the first alarm it fires again every 50 ms until the call returns. The
    guard's own functions are exempt, so its bookkeeping cannot be interrupted. The timer is
    always cancelled in `finally`, and the previous handler and timer are restored. Only used
    where `signal.setitimer` exists and we are on the main thread; otherwise no hard timer
    (local Windows dev).
  - during a core or fallback call `sys.stdout` and `sys.stderr` are a sink and `sys.stdin` is
    `None`: strategy code must not flood or read the worker's pipes.
  - keeps counters, exposed by the instance method `guard_stats() -> dict`: turns, core_ok,
    core_exceptions, core_timeouts, core_bad_output, fallback_used, fallback_failures,
    none_used, max_core_s, first_core_traceback (str | None), core_loaded, fallback_loaded,
    core_init_error, fallback_init_error, fallback_load_error, first_fallback_error.
- `assemble(None)` (no core, no fallback) is the t=0 fallback: always returns `None`.

```python
def write_atomic(path: Path, text: str) -> None    # tmp file in path.parent, fsync, chmod 0644,
                                                   # os.replace
```

## 6 `sandbox.py` + `runner_child.py`: run untrusted code in subprocesses

`runner_child.py` is a **stdlib-only** script, run as
`[sys.executable, '-s', '-S', '-P', runner_child.py, job.json]` (that is `-I` without `-E`, so
`PYTHONHASHSEED=0` holds as in arena's workers); it prints one JSON object on stdout. `-S` keeps
site-packages out, so a strategy that imports a third-party package fails here exactly as it
would in the tournament. The child gets a minimal environment (no API keys), its own process
group (killed as a whole on timeout), and stdin from `/dev/null`.

Inside the child, `set_limits` sets each rlimit separately (`RLIMIT_CORE` 0, `RLIMIT_FSIZE`
64 MB, `RLIMIT_AS` 1536 MB), because macOS rejects `RLIMIT_AS` and the others must still hold.
Output of untrusted code goes to stderr, capped per strategy and match at 16 MB (past it writes
raise, like arena's worker). Every call into untrusted code runs under a `setitimer` timer
(Linux only; elsewhere it is measured, not interrupted).

```python
@dataclass
class ProbeResult:
    ok: bool                     # loaded, and every call returned in time without exception
    load_error: str | None       # traceback if the file/class failed to load or __init__ raised
    calls: list[dict]            # per observation: {player_id, ok, secs, action_repr (<=300 chars),
                                 #   error (traceback | None), literal_ok}
    max_secs: float

def probe(strategy_path: Path, observations: list[dict], *, strategy_class: str = 'Strategy',
          player_ids: tuple[int, ...] = (0, 1), act_timeout_s: float = 1.0,
          timeout_s: float = 60.0, raw: bool = False, work_dir: Path | None = None) -> ProbeResult
    # Instantiates the class once per player id (init limit 10 s) and calls act on every
    # observation in order. A call is ok only if it returned within act_timeout_s; act is
    # interrupted at 2 * act_timeout_s. literal_ok is False if the reply is not a literal that
    # round-trips or is over 1,000,000 bytes. raw=True: the file is a bare LLM core (no guard);
    # the module is registered in sys.modules so dataclasses work.

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
    in_progress: bool            # sim_error 'timeout' and the child was killed in this match

def play_matches(sim_path: Path, entries: dict[str, Path | None],
                 pairings: list[tuple[str, str, int]],
                 *, strategy_class: str = 'Strategy', sim_class: str = 'Game',
                 act_timeout_s: float = 1.0, max_turns: int = 5000, workers: int = 2,
                 timeout_s: float = 300.0, draw_tolerance: float = 0.01,
                 work_dir: Path | None = None) -> list[MatchResult]
    # pairings: (id_seat0, id_seat1, seed). entries: id -> strategy file. The id 'idle' (or a
    # None path) needs no file: it always returns None. Unknown ids give an invalid result.
    # Splits pairings over `workers` child processes; each child plays its share in order,
    # loading fresh strategy classes for every match (module-level state never leaks between
    # matches, as arena starts a fresh worker per match). game = Game(seed); per turn:
    # obs_i = game.observe(i) (must be a literal: round-trip check), act_i timed; an exception,
    # an act over act_timeout_s (interrupted at max(2x, +0.5 s)) or a reply over 1,000,000
    # bytes -> that seat forfeits: plays None for the rest of the match (like arena). A reply
    # that is not a literal is passed on as None (malformed), not a forfeit. After
    # game.step(actions), if the game has action_report(i) -> {'well_formed': bool,
    # 'n_illegal': int}, accumulate malformed turns and illegal moves. Each simulator call is
    # limited to 30 s. Stop at is_over() or max_turns. winner: higher score wins unless
    # |s0 - s1| <= draw_tolerance * (|s0| + |s1|) (then None); a seat that forfeited loses
    # unless both did. A child that exceeds timeout_s is killed: its unfinished matches come
    # back with sim_error='timeout' (in_progress=True on the one being played). A child that
    # dies: the crash report on the match being played, 'not started: ...' on the rest. Results
    # finished before a kill are kept (the child appends them to a progress file).

def run_selftest(sim_path: Path, timeout_s: float = 60.0, *, work_dir=None) -> tuple[bool, str]
    # imports sim.py in a child, calls selftest() -> list[str] failures; (passed, report)

def check_simulator(sim_path: Path, spec_info: SpecInfo, *, seeds=(0, 1, 2),
                    timeout_s: float = 60.0, sim_class: str = 'Game', max_turns: int = 5000,
                    work_dir=None) -> tuple[bool, list[str]]
    # generic checks, no LLM. Only spec_info.obs_keys and example_observations are used.
    # Game(seed) constructs; observe(0/1) are literal dicts whose keys equal obs_keys (when
    # known) with value kinds matching the examples (below); playing idle-vs-idle and
    # garbage-vs-idle to the end raises nothing; action_report, if present, returns
    # {'well_formed': bool, 'n_illegal': int} (a missing one is only a 'warning:' problem);
    # scores() is a list of 2 finite numbers; is_over() becomes True within max_turns; same
    # seed -> same first observation (determinism). Returns (ok, problems); problems starting
    # with 'warning:' do not fail the check.
```

**Value kinds** (`runner_child.type_problems`). A simulator value is compared with the spec's
example values at the same position: the field in every example observation, and every element
of the example lists. It passes if it has the kind of **any** of them: `bool` only for bool,
`int` (not bool) for int, `int` or `float` for float, otherwise the exact type (list and tuple
stay distinct). `None` is accepted only where some example shows `None` (e.g. `list[int | None]`
for hidden information); positions where the examples show only `None` are not checked. For
lists the first element of the value is checked against all elements of the matching examples,
for dicts every key present, to depth 3. This is the rule that replaced a first-element-only
comparison, which rejected correct simulators of games with hidden information (see
`STATUS.md`).

## 7 `tournament.py`

```python
@dataclass
class Standing:
    id: str
    played: int; wins: int; draws: int; losses: int
    points: float                 # wins + 0.5 * draws
    win_rate: float               # points / played (0 if none)
    mean_margin: float            # mean (own - opponent) score, per match
    problems: dict                # summed forfeits, exceptions, timeouts, malformed, illegal
                                  # and the guard's core_exceptions, core_timeouts,
                                  # core_bad_output, fallback_used, none_used

class Tournament:
    def __init__(self, sim_path: Path, workdir: Path, *, seeds: list[int], workers: int = 2,
                 act_timeout_s: float = 1.0, max_turns: int = 5000,
                 draw_tolerance: float = 0.01) -> None
    def add(self, entry_id: str, strategy_path: Path | None) -> None   # None only for 'idle'
                                                  # re-adding an id with a new file replays it
    def pending(self) -> list[tuple[str, str, int]]    # unplayed (seat 0, seat 1, seed)
    def run(self, *, budget_s: float) -> None
        # plays every not-yet-played (a, b, seed) for all pairs of entries, both seats,
        # within budget_s (estimates match cost from earlier matches; plays seeds in order and
        # cuts at a seed boundary so a partial run is balanced). Results are cached: adding an
        # entry later only plays its new pairings. A match cut off by the time budget is
        # retried last; after MAX_KILLS = 2 cuts it is cached as invalid.
    def standings(self) -> list[Standing]               # best first: win rate, points, margin
    def head_to_head(self, a: str, b: str) -> tuple[int, int, int]   # wins, draws, losses of a
    def report(self, focus: str | None = None, max_chars: int = 4000) -> str
        # plain-text table for prompts and logs: standings, per-opponent results of `focus`
        # (by seat), per-seat margins, act times, guard counters, first errors.
    seeds: list[int]                                    # may grow (polish appends seeds)
    results: list[MatchResult]
```

## 8 `pipeline.py`

```python
FINAL_TIME_BUDGET_S = 0.25   # TIME_BUDGET of the delivered file; self-play uses it too
FINAL_ACT_LIMIT_S = 0.70     # the guard's act limit in the delivered file; self-play too
PROMOTE_MARGIN = 0.03        # a challenger needs this much more mean per-opponent score ...
CLEAR_MARGIN = 0.12          # ... and must not lose the head-to-head, unless it has this much
MAX_POLISH_SEEDS = 24
SELFPLAY_CLASS = 'Strategy'  # class name of internal self-play files

@dataclass
class Profile: ...           # models, efforts, token caps and counts per stage; see below

def make_profile(task: dict, llm: LLM, clock: Clock) -> Profile
def apply_ablations(p: Profile, flags: str | None) -> Profile

class Run:
    def __init__(self, *, spec, task, strategy_path, work_dir, log_dir, container=False)
    def run(self) -> None    # never raises: whatever fails, the file at strategy_path stays valid
```

**Profile.** The tier comes from `task.json`, never from a task name: `tiny` if
`budget_usd < 0.10` or `deadline_s < 300`; `mid` if `budget_usd < 1.50`; else `large`.
"Cheapest" and "strongest" are the least and most expensive of `allowed_models` (equal when
only one is allowed). The spending limit is always 90% of the task's `budget_usd`.

| tier | drafts | simulator | repairs | improvement per round | analysis |
|------|--------|-----------|---------|-----------------------|----------|
| tiny | 3x strongest @ low | cheapest @ low, 2 repairs | cheapest @ low | 2 parallel @ low (strongest, cheapest) | none |
| mid | 3x strongest @ low | strongest @ low, 2 repairs | strongest @ low | 1 strongest @ medium | none |
| large | strongest, cheapest, cheapest, strongest @ low | cheapest @ medium, 3 repairs, escalation to strongest | cheapest @ medium | 3 parallel (strongest, 2x cheapest) @ medium | strongest @ medium, in parallel with the drafts |

Other fields per tier (tiny / mid / large): self-play seeds 3 / 4 / 6, strategy repairs per
candidate 1 / 1 / 2, call timeout 90 s or half the deadline / 300 s / 360 s, `min_improve_s`
(time one improvement round needs) 50 / 130 / 220 s. In the evaluation's tasks the cheapest
model (`gpt-6-luna`) only ever runs at low effort (`tests/test_profiles.py`; at high effort it
returned no code in calibration). Calibration and the evidence for each choice are in
`STATUS.md` and `report.md`.

**Stages.**

- *First batch.* The simulator builder, one draft per persona (the first `n_candidates` of
  `personas.json`, models cycled) and the analysis go to a thread pool in that priority order;
  `LLM` lets at most 4 requests run, the rest queue. The wait ends when `clock.remaining()`
  drops to `min_improve_s * 0.25 + 15` s; unfinished work is not waited for.
- *Simulator.* One attempt with the profile's simulator model, up to `max_sim_repairs` repairs
  (`repair_sim.md`, each needs 60 s left), then, if the strongest model differs, a second
  attempt with it (not if under 150 s are left). Accepted when `selftest()` passes, the generic
  checks pass, and up to two verified candidates play on it without a simulator exception.
- *Candidate.* `verify`: the raw core is probed on the spec's example observations (load only,
  if there are none), then, if a simulator exists, played against idle in both seats. Problems
  are tracebacks, act over 0.5 s on an example, non-literal replies, act timeouts, malformed
  actions in more than a tenth of the turns, illegal moves in more than half, a loss against
  idle. Failing candidates get `max_strategy_repairs` repair calls (`repair_strategy.md`)
  while 60 s are left. Candidates verified before the simulator existed are played on it in
  `select`, and repaired once if that fails.
- *Selection.* A tournament of idle and all valid candidates (seeds `0..seeds-1`) runs for up
  to half of the remaining time. The ranking per candidate is the mean over its opponents of its
  head-to-head score (every opponent weighs the same, idle included), then its worst pairing,
  then the score margin. The first valid draft is only a *provisional* champion; once
  self-play has chosen a champion, a challenger replaces it only if it gains `PROMOTE_MARGIN`
  and does not lose the head-to-head, or gains `CLEAR_MARGIN` (incumbent protection against
  selection noise). A non-safe draft replaces a provisional safe one. The "safe" persona (the
  name containing `safe`, else the last) also serves as the guard's fallback core.
- *Promotion.* `guard.assemble(champion, fallback=safe candidate)`, written to a staging file,
  accepted only if the final probe passes (examples loaded, every call ok, max act at most
  0.9 s; with a simulator, two matches against idle without forfeit and max act at most
  0.95 s); then `guard.write_atomic`.
- *Improvement.* While `clock.remaining() > min_improve_s`, a champion exists and fewer than 3
  empty rounds in a row: `improve_parallel` challengers (`improve.md`, with the champion's code,
  the tournament report and a focus text built from its worst pairings; the first uses the
  improvement model, the others the cheapest model with a fixed alternative focus), each
  verified like a draft. They join the tournament (60% of the remaining time), then promotion
  as above.
- *Budget rescue* (`Profile.rescue`, on by default). Reservations are worst cases, so a run can
  end with money left that no full improvement call fits (measured on B: $0.07 to $0.14
  stranded). When no call is in flight and the full call would not be affordable, the call
  goes out at low effort with the drafts' token cap instead. Adopted by experiment 3's rule on
  weak evidence; it can also act near the end of C runs.
- *Polish.* While more than 30 s remain and fewer than `MAX_POLISH_SEEDS` seeds were added: add
  a seed when nothing is pending, play, re-rank.
- *Finish.* Final probe of `strategy_path`; if it fails and a champion exists, the t=0 fallback
  is written back. Then `end` is logged and `tournament.txt` written.

**Calls.** `Run.ask` sends `instructions = system.md + playbook.md` and
`input = [spec message, stage message]`. If the worst case does not fit but other calls are in
flight, it waits (polling every second while more than 60 s remain) for them to settle instead
of skipping. The call timeout is `min(profile.call_timeout_s, clock.remaining() - 10)`.

**Development switches.** `HARNESS_ABLATE` is a comma-separated list; unknown names are
ignored and the evaluation sets none. `improve`, `selfplay`, `repair`, `analysis`,
`robust_rank`, `oneshot`, `polish`, `improve_low`, `rescue` (what each does: `README.md`,
"Settings").

## 9 Prompts (`src/harness/prompts/`)

All calls share one stable prefix, for prompt caching: `instructions = system.md + playbook.md`,
`input = [spec message, task message]`, where the spec message (`GAME SPECIFICATION` plus the
spec) is identical in every call and the task message is stage specific. Files and their
`str.format` slots:

- `system.md`: role, the strategy contract, the output format (one fenced ```python block,
  complete file), hard rules (stdlib only, never raise, speed, `TIME_BUDGET`). Not formatted.
- `playbook.md`: game-family lessons (the "lessons file"), curated offline from the tuning
  games. General, never naming game specifics. Not formatted.
- `simulator.md`: write `sim.py`: `Game` (arena interface + `action_report`) and `selftest()`.
  Slots `{obs_keys}`, `{examples_note}`.
- `strategy.md`: write a candidate. Slots `{persona}`, `{time_budget}`, `{analysis}`.
- `repair_strategy.md`, `repair_sim.md`: fix a strategy or simulator given failures and
  tracebacks. Slots `{code}`, `{problems}`.
- `improve.md`: beat the current pool given the champion code and tournament report. Slots
  `{champion_code}`, `{report}`, `{analysis}`, `{focus}`.
- `analysis.md`: plain-text notes on the game (at most 600 words, no code), fed to later
  calls. No slots.
- `personas.json`: list of `{name, persona}`; the approach each draft is told to take.

Literal braces in the templates are doubled. `tests/test_prompts.py` checks the slots and that
no prompt names a dev game.

## 10 Strategy core contract (what the LLM writes)

```python
TIME_BUDGET = 0.25   # seconds per act; the harness overrides this module global

class Strategy:
    def __init__(self, player_id: int) -> None: ...   # cheap: no heavy work here
    def act(self, obs): ...                           # exact action format of the spec
```

Standard library only; deterministic or seeded randomness; build map data lazily on the first
`act`; use `time.perf_counter()` against `TIME_BUDGET` for any search loop.

## 11 Simulator contract (what the LLM writes, `sim.py`)

```python
class Game:
    n_players = 2
    def __init__(self, seed: int | None = None) -> None   # keyword-only extras after seed are
                                                       # allowed (arbitrary states for selftest)
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

## 12 Files and logs

```
/output/strategy.py            the deliverable (always valid)
/output/logs/transcript.jsonl  every LLM call (llm.py)
/output/logs/events.jsonl      pipeline decisions: stage start/end, promotions, spend, times
/output/logs/candidates/       every generated strategy / sim source, by id; analysis.md
/output/logs/tournament.txt    last tournament report
/tmp/work/                     scratch: sims, candidates, child job files
```

Locally the same tree lives under `runs/<task>/<time>-<pid>/` (`output/` and `work/`).
`events.jsonl` records one line per decision (`start`, `llm`, `candidate`, `sim_checked`,
`ranking`, `promoted`, `kept_champion`, `improve_round`, `rescue`, `polished`, `final_probe`,
`end`, `error`, ...), each with the elapsed time.
