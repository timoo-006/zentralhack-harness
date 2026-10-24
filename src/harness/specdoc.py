"""Deterministic parsing of a game's spec.md: fenced code blocks, constants, literal example
observations, the observation's key set and literal action examples.

No LLM and no game knowledge: everything comes from the text of the spec. `parse_spec` never
raises; whatever cannot be parsed simply stays empty.
"""

import ast
import copy
import json
import operator
import re
import textwrap
import warnings
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

PYTHON_LANGS = frozenset({"python", "py", "python3", "py3", "pycon", "ipython"})
JSON_LANGS = frozenset({"json", "jsonc", "json5"})
# Blocks searched for literals besides python blocks: JSON and untagged / plain-text blocks.
LITERAL_LANGS = PYTHON_LANGS | JSON_LANGS | {"", "text", "txt", "plain", "plaintext"}

MAX_BLOCK_CHARS = 300_000  # longer code blocks are kept but not parsed
MAX_INLINE_CHARS = 5_000  # longer inline code spans are ignored
MAX_ACTION_EXAMPLES = 60
MAX_EXAMPLES = 32  # example observations kept (each one is probed on every candidate)
MIN_OBS_KEYS = 3  # literal dicts / schemas with fewer keys are not observation candidates

_FENCE_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_LANG_RE = re.compile(r"[A-Za-z0-9_+#.-]*")
_INLINE_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)
_PARAGRAPH_RE = re.compile(r"\n[ \t]*\n")
_JSON_NAMES = {"true": True, "false": False, "null": None}
# A string value that names a type ("int", "list[int]", "float | None"): a JSON-style schema.
_TYPE_NAME_RE = re.compile(
    r"(?:int|float|bool|str|list|dict|tuple|set|none|null|number|integer|string|boolean|any"
    r"|optional|union|sequence|mapping)\b[\w\[\], |.]*$",
    re.IGNORECASE,
)
_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


@dataclass
class SpecInfo:
    """What the spec states literally. Every field may be empty."""

    code_blocks: list[str] = field(default_factory=list)  # every fenced block, any language
    python_blocks: list[str] = field(default_factory=list)  # fenced python (py, pycon) blocks
    constants: dict[str, Any] = field(default_factory=dict)  # NAME = <literal> assignments
    example_observations: list[dict] = field(default_factory=list)
    obs_keys: list[str] | None = None  # the observation's key set (schema or examples)
    action_examples: list[Any] = field(default_factory=list)  # literal lists/tuples (and
    # non-observation dicts) from inline code spans and block-level expressions
    schemas: list[list[str]] = field(default_factory=list)  # keys of every schema-like dict


@dataclass
class _Item:
    """One literal or schema found in the spec, in document order."""

    origin: str  # 'expr' | 'assign' | 'segment' | 'inline'
    value: Any = None
    schema: list[str] | None = None
    name: str | None = None  # the assigned name, for 'assign'


def parse_spec(spec: str) -> SpecInfo:
    """Parse `spec` (markdown). Never raises: on any failure returns what it has."""
    info = SpecInfo()
    try:
        if isinstance(spec, bytes):
            spec = spec.decode("utf-8", errors="replace")
        if not isinstance(spec, str) or not spec:
            return info
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # SyntaxWarnings from spec code: not our business
            _parse(spec.replace("\r\n", "\n").replace("\r", "\n"), info)
    except Exception:
        pass
    return info


def describe_obs_keys(info: SpecInfo) -> str:
    """Text for a prompt slot listing the expected observation keys."""
    if not info.obs_keys:
        return "unknown (no observation schema or example could be parsed from the spec)"
    return ", ".join(repr(k) for k in info.obs_keys)


def describe_examples(info: SpecInfo) -> str:
    """Text for a prompt slot saying how many worked example observations were extracted."""
    n = len(info.example_observations)
    if n == 0:
        return (
            "The harness could not extract any complete example observation from the spec, so "
            "selftest() is the only check of your rules: make it reproduce every worked example "
            "the spec describes in prose or tables."
        )
    return (
        f"The harness extracted {n} complete example observation(s) from the spec's code "
        "blocks. It checks that your observe() returns dicts with exactly these keys and value "
        "types, and selftest() must reproduce every worked example exactly."
    )


# --------------------------------------------------------------------------- top level


def _parse(text: str, info: SpecInfo) -> None:
    items: list[_Item] = []
    for kind, body, lang in _split_markdown(text):
        if kind == "prose":
            _scan_inline(body, items)
            continue
        info.code_blocks.append(body)
        if lang in PYTHON_LANGS:
            info.python_blocks.append(body)
        if lang in LITERAL_LANGS and len(body) <= MAX_BLOCK_CHARS:
            try:
                _scan_block(body, lang, items, info.constants)
            except Exception:
                pass

    schemas = [it.schema for it in items if it.schema]
    info.schemas = [list(s) for s in schemas]
    found = [
        it
        for it in items
        if it.origin in ("expr", "assign", "segment") and _is_str_dict(it.value)
    ]
    dicts = [it.value for it in found]
    # Dicts bound to CONSTANT names are tables (unit types, costs), not observations.
    free = [it.value for it in found if not (it.name and it.name.isupper())]
    target, keys = _choose_keys(dicts, free, schemas)
    info.obs_keys = keys
    if target is not None:
        examples = [d for d in dicts if frozenset(d) == target][: MAX_EXAMPLES * 2]
        info.example_observations = _dedupe(_fill_ellipsis(examples))[:MAX_EXAMPLES]

    seen: set[str] = set()
    for it in items:
        if len(info.action_examples) >= MAX_ACTION_EXAMPLES:
            break
        if it.origin not in ("expr", "segment", "inline") or it.schema is not None:
            continue
        v = it.value
        if isinstance(v, dict):
            if not v or _looks_like_obs(v, target):
                continue
        elif not isinstance(v, (list, tuple)):
            continue
        if _has_ellipsis(v):
            continue
        key = repr(v)
        if key not in seen:
            seen.add(key)
            info.action_examples.append(copy.deepcopy(v))


# --------------------------------------------------------------------------- markdown


def _split_markdown(text: str) -> list[tuple[str, str, str]]:
    """Document-order pieces: ('prose', text, '') or ('code', content, lang)."""
    lines = text.split("\n")
    pieces: list[tuple[str, str, str]] = []
    prose: list[str] = []
    i = 0
    while i < len(lines):
        m = _FENCE_RE.match(lines[i])
        if m and not (m["fence"][0] == "`" and "`" in m["info"]):
            if prose:
                pieces.append(("prose", "\n".join(prose), ""))
                prose = []
            fence = m["fence"]
            close = re.compile(rf"^[ \t]*{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$")
            indent = len(m["indent"])
            body: list[str] = []
            i += 1
            while i < len(lines) and not close.match(lines[i]):
                body.append(_strip_indent(lines[i], indent))
                i += 1
            i += 1  # the closing fence (an unclosed block runs to the end of the text)
            pieces.append(("code", "\n".join(body), _lang(m["info"])))
            continue
        prose.append(lines[i])
        i += 1
    if prose:
        pieces.append(("prose", "\n".join(prose), ""))
    return pieces


def _strip_indent(line: str, n: int) -> str:
    k = 0
    while k < n and k < len(line) and line[k] in " \t":
        k += 1
    return line[k:]


def _lang(info: str) -> str:
    m = _LANG_RE.match(info.strip().lstrip("{.").strip())
    return m.group(0).lower() if m else ""


def _scan_inline(prose: str, items: list[_Item]) -> None:
    """Literal lists, tuples and dicts in inline code spans."""
    for para in _PARAGRAPH_RE.split(prose):
        if "`" not in para:
            continue
        for m in _INLINE_RE.finditer(para):
            content = m.group(2).strip()
            if not content or len(content) > MAX_INLINE_CHARS or content[0] not in "([{":
                continue
            try:
                node = ast.parse(content, mode="eval").body
            except Exception:
                continue
            ok, value = _literal(node, json_names=True)
            if ok and isinstance(value, (list, tuple, dict)):
                items.append(_Item("inline", value))


# --------------------------------------------------------------------------- code blocks


def _scan_block(body: str, lang: str, items: list[_Item], constants: dict[str, Any]) -> None:
    """Literals, schemas and constants of one fenced block, appended in order."""
    if lang in JSON_LANGS:
        try:
            value = json.loads(body)
        except Exception:
            pass
        else:
            _add_value(value, "expr", items, None)
            return
    src = textwrap.dedent(_strip_prompts(body))
    json_names = lang not in PYTHON_LANGS
    try:
        stmts = ast.parse(src).body
    except Exception:
        stmts = None
    if stmts is None:
        for seg in _segments(src):
            try:
                node = ast.parse(seg, mode="eval").body
            except Exception:
                continue
            _add_node(node, "segment", items, json_names)
        return
    for stmt in stmts:
        if isinstance(stmt, ast.Expr):
            _add_node(stmt.value, "expr", items, json_names)
        elif isinstance(stmt, ast.Assign):
            _add_node(stmt.value, "assign", items, json_names, _target_name(stmt.targets))
            _add_constants(stmt.targets, stmt.value, constants)
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            _add_node(stmt.value, "assign", items, json_names, _target_name([stmt.target]))
            _add_constants([stmt.target], stmt.value, constants)
        elif isinstance(stmt, ast.ClassDef) and _is_typed_dict(stmt):
            keys = [
                s.target.id
                for s in stmt.body
                if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
            ]
            if keys:
                items.append(_Item("expr", schema=_unique(keys)))


def _strip_prompts(body: str) -> str:
    """Drop interactive-session prompts (>>> / ...) so a console transcript parses."""
    if not body.lstrip().startswith(">>>"):
        return body
    out = []
    for line in body.split("\n"):
        s = line.lstrip()
        if s.startswith((">>> ", "... ")):
            out.append(s[4:])
        elif s in (">>>", "..."):
            out.append("")
        else:
            out.append(line)
    return "\n".join(out)


def _segments(src: str) -> list[str]:
    """Top-level bracketed segments of text that does not parse as a whole, skipping string
    literals and # comments."""
    pairs = {")": "(", "]": "[", "}": "{"}
    out: list[str] = []
    stack: list[str] = []
    start = 0
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "'\"":
            if src.startswith(c * 3, i):
                j = src.find(c * 3, i + 3)
                i = n if j < 0 else j + 3
                continue
            j = i + 1
            while j < n and src[j] not in (c, "\n"):
                j += 2 if src[j] == "\\" else 1
            i = j + 1
            continue
        if c == "#":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in "([{":
            if not stack:
                start = i
            stack.append(c)
        elif c in ")]}":
            if stack and stack[-1] == pairs[c]:
                stack.pop()
                if not stack:
                    out.append(src[start : i + 1])
            else:
                stack = []
        i += 1
    return out


def _target_name(targets: list[ast.expr]) -> str | None:
    return targets[0].id if len(targets) == 1 and isinstance(targets[0], ast.Name) else None


def _add_node(
    node: ast.expr, origin: str, items: list[_Item], json_names: bool, name: str | None = None
) -> None:
    ok, value = _literal(node, json_names=json_names)
    if ok:
        _add_value(value, origin, items, name)
        return
    keys = _schema_keys(node)
    if keys:
        items.append(_Item(origin, schema=keys, name=name))


def _add_value(value: Any, origin: str, items: list[_Item], name: str | None) -> None:
    if _is_type_name_dict(value):  # {"turn": "int", ...}: a schema written as strings
        items.append(_Item(origin, schema=_unique(list(value)), name=name))
    else:
        items.append(_Item(origin, value, name=name))


def _is_type_name_dict(v: Any) -> bool:
    return (
        _is_str_dict(v)
        and len(v) >= MIN_OBS_KEYS
        and all(isinstance(x, str) and _TYPE_NAME_RE.match(x.strip()) for x in v.values())
    )


def _literal(node: ast.AST, *, json_names: bool = False) -> tuple[bool, Any]:
    try:
        return True, ast.literal_eval(node)
    except Exception:
        pass
    if json_names:
        try:
            return True, ast.literal_eval(_JsonNames().visit(copy.deepcopy(node)))
        except Exception:
            pass
    return False, None


class _JsonNames(ast.NodeTransformer):
    """true / false / null -> True / False / None, for JSON-flavoured blocks."""

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in _JSON_NAMES:
            return ast.copy_location(ast.Constant(_JSON_NAMES[node.id]), node)
        return node


def _schema_keys(node: ast.AST) -> list[str] | None:
    """Keys of a dict display whose keys are all string constants (values may be types)."""
    if not isinstance(node, ast.Dict) or not node.keys:
        return None
    keys = []
    for k in node.keys:
        if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
            return None
        keys.append(k.value)
    return _unique(keys)


def _is_typed_dict(cls: ast.ClassDef) -> bool:
    for b in cls.bases:
        name = b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else ""
        if name == "TypedDict":
            return True
    return False


def _add_constants(targets: list[ast.expr], value: ast.expr, constants: dict[str, Any]) -> None:
    """NAME = literal (or simple arithmetic of numbers and known constants); first one wins."""
    for target in targets:
        if isinstance(target, ast.Name):
            ok, v = _constant_value(value, constants)
            if ok and target.id not in constants:
                constants[target.id] = v
        elif isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple):
            if len(target.elts) == len(value.elts):
                _add_constants_pairwise(target.elts, value.elts, constants)


def _add_constants_pairwise(
    names: list[ast.expr], values: list[ast.expr], constants: dict[str, Any]
) -> None:
    for t, v in zip(names, values, strict=True):
        if isinstance(t, ast.Name):
            ok, val = _constant_value(v, constants)
            if ok and t.id not in constants:
                constants[t.id] = val


def _constant_value(node: ast.expr, constants: dict[str, Any]) -> tuple[bool, Any]:
    ok, v = _literal(node)
    if ok:
        return (False, None) if _has_ellipsis(v) else (True, v)
    try:
        return True, _arith(node, constants, 0)
    except Exception:
        return False, None


def _arith(node: ast.AST, env: dict[str, Any], depth: int) -> int | float:
    if depth > 30:
        raise ValueError("too deep")
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.Name) and type(env.get(node.id)) in (int, float):
        return env[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _arith(node.operand, env, depth + 1)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        a = _arith(node.left, env, depth + 1)
        b = _arith(node.right, env, depth + 1)
        if isinstance(node.op, ast.Pow) and (abs(b) > 64 or abs(a) > 1e6):
            raise ValueError("power too large")
        r = _BINOPS[type(node.op)](a, b)
        if isinstance(r, int) and abs(r) > 10**30:
            raise ValueError("too large")
        return r
    raise ValueError("not arithmetic")


# --------------------------------------------------------------------------- observations


def _is_str_dict(v: Any) -> bool:
    return isinstance(v, dict) and bool(v) and all(isinstance(k, str) for k in v)


def _choose_keys(
    dicts: list[dict], free: list[dict], schemas: list[list[str]]
) -> tuple[frozenset | None, list[str] | None]:
    """(key set the example observations must have, obs_keys in display order).

    `dicts`: every literal str-keyed dict; `free`: those not bound to a CONSTANT name.
    """
    counts = Counter(frozenset(d) for d in dicts)
    big = [s for s in schemas if len(s) >= MIN_OBS_KEYS]
    # A schema that some literal dict matches exactly: those dicts are the examples. The
    # largest such schema wins: the observation is the biggest record a spec describes, so a
    # dict-shaped action or a unit record with more examples must not displace it.
    matching = [s for s in big if frozenset(s) in counts]
    if matching:
        best = max(matching, key=lambda s: (len(s), counts[frozenset(s)]))
        return frozenset(best), list(best)

    cands = [d for d in free if len(d) >= MIN_OBS_KEYS]
    if not cands:
        cands = [d for d in dicts if len(d) >= MIN_OBS_KEYS]
    common: frozenset | None = None
    if cands:
        cand_counts = Counter(frozenset(d) for d in cands)
        first: dict[frozenset, int] = {}
        for i, d in enumerate(cands):
            first.setdefault(frozenset(d), i)
        common = max(first, key=lambda ks: (cand_counts[ks], len(ks), -first[ks]))

    schema = max(big, key=len) if big else None
    if common is None:
        return None, (list(schema) if schema else None)
    if schema is not None and len(schema) > len(common):
        if common < frozenset(schema):
            # The literal dicts show only part of the observation: the schema is the key set,
            # and partial dicts are no usable examples (a strategy may read any key).
            return None, list(schema)
        overlap = len(common & frozenset(schema))
        if overlap * 2 < len(common):
            # The literal dicts are something else (e.g. parts of an observation): trust the
            # schema and keep no examples.
            return None, list(schema)
    order = next(list(d) for d in cands if frozenset(d) == common)
    return common, order


def _looks_like_obs(d: dict, target: frozenset | None) -> bool:
    if target is None or not _is_str_dict(d):
        return False
    return frozenset(d) == target or len(target & frozenset(d)) * 2 >= len(d)


def _has_ellipsis(v: Any, depth: int = 0) -> bool:
    if v is Ellipsis:
        return True
    if depth > 60:
        return False
    if isinstance(v, dict):
        return any(_has_ellipsis(k, depth + 1) or _has_ellipsis(x, depth + 1) for k, x in v.items())
    if isinstance(v, (list, tuple, set, frozenset)):
        return any(_has_ellipsis(x, depth + 1) for x in v)
    return False


def _fill_ellipsis(examples: list[dict]) -> list[dict]:
    """Replace values containing Ellipsis by the same key's value from the nearest other
    example (earlier ones first); drop an example if some key has no donor."""
    out = []
    for i, ex in enumerate(examples):
        fixed: dict | None = {}
        for k, v in ex.items():
            if not _has_ellipsis(v):
                fixed[k] = copy.deepcopy(v)
                continue
            donors = [*range(i - 1, -1, -1), *range(i + 1, len(examples))]
            for j in donors:
                if k in examples[j] and not _has_ellipsis(examples[j][k]):
                    fixed[k] = copy.deepcopy(examples[j][k])
                    break
            else:
                fixed = None
                break
        if fixed is not None:
            out.append(fixed)
    return out


def _dedupe(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    out = []
    for v in values:
        key = repr(v)
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def _unique(keys: list[str]) -> list[str]:
    return list(dict.fromkeys(keys))
