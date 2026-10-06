"""Branch conditions (ADR 0027): the runtime-neutral grammar of `flows_to` `props.when`.

    amount > 10000
    amount > 10000 and not urgent
    category in ["it", "facilities"] or amount <= 500
    else

Comparisons (`= != < <= > >=`, `in [...]`) of a case attribute with a literal (number,
quoted string, true, false), joined by `and`, `or`, `not` and parentheses. A bare
attribute means `= true`. `else` is the default branch: taken when no other condition
holds.

`canonical` prints one form for every way of writing the same condition (keyword case,
`==` and `<>`, spacing, quotes, literal-first comparisons, the order of `and` and `or`
operands), so two sources stating the same condition assert the same edge: props are part
of an edge's identity. Workflow adapters compile the parsed form into their runtime's
expression language.
"""

from __future__ import annotations

import json
import re
from typing import Any

ELSE = "else"

Node = tuple[Any, ...]

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<number>-?\d+(?:\.\d+)?(?![\w.]))
      | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
      | (?P<op><=|>=|!=|<>|==|=|<|>)
      | (?P<punct>[()\[\],])
      | (?P<word>[A-Za-z_][\w.:]*)
    )""",
    re.VERBOSE,
)
_KEYWORDS = {"and", "or", "not", "in", "true", "false", "else"}
_FLIP = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "=": "=", "!=": "!="}


class ConditionError(ValueError):
    """A condition that does not parse."""


class MissingAttribute(KeyError):
    """A condition names an attribute the case does not have."""


def tokens(text: str) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ConditionError(f"cannot read {text[pos:].strip()[:20]!r} in condition {text!r}")
        pos = m.end()
        kind = m.lastgroup
        value = m.group(kind)
        if kind == "number":
            out.append(("lit", float(value) if "." in value else int(value)))
        elif kind == "string":
            out.append(("lit", _unquote(value)))
        elif kind == "op":
            out.append(("op", {"==": "=", "<>": "!="}.get(value, value)))
        elif kind == "punct":
            out.append((value, value))
        elif value.lower() in _KEYWORDS:
            word = value.lower()
            out.append(("lit", word == "true") if word in ("true", "false") else (word, word))
        else:
            out.append(("attr", value))
    return out


def _unquote(text: str) -> str:
    body = text[1:-1]
    return re.sub(r"\\(.)", r"\1", body)


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.toks = tokens(text)
        self.i = 0

    def peek(self) -> str | None:
        return self.toks[self.i][0] if self.i < len(self.toks) else None

    def take(self, kind: str) -> Any:
        if self.peek() != kind:
            found = self.toks[self.i][1] if self.i < len(self.toks) else "the end"
            raise ConditionError(f"expected {kind}, found {found!r} in condition {self.text!r}")
        value = self.toks[self.i][1]
        self.i += 1
        return value

    def parse(self) -> Node:
        if not self.toks:
            raise ConditionError("a condition cannot be empty")
        if self.peek() == "else":
            self.take("else")
            if self.peek() is not None:
                raise ConditionError(f"else stands alone, in condition {self.text!r}")
            return ("else",)
        node = self.or_()
        if self.peek() is not None:
            raise ConditionError(f"unexpected {self.toks[self.i][1]!r} in condition {self.text!r}")
        return node

    def or_(self) -> Node:
        parts = [self.and_()]
        while self.peek() == "or":
            self.take("or")
            parts.append(self.and_())
        return parts[0] if len(parts) == 1 else ("or", *parts)

    def and_(self) -> Node:
        parts = [self.not_()]
        while self.peek() == "and":
            self.take("and")
            parts.append(self.not_())
        return parts[0] if len(parts) == 1 else ("and", *parts)

    def not_(self) -> Node:
        if self.peek() == "not":
            self.take("not")
            return ("not", self.not_())
        return self.primary()

    def primary(self) -> Node:
        if self.peek() == "(":
            self.take("(")
            node = self.or_()
            self.take(")")
            return node
        left = self.operand()
        if self.peek() == "in":
            self.take("in")
            if left[0] != "attr":
                raise ConditionError(f"in needs an attribute on its left, in condition {self.text!r}")
            return ("in", left[1], self.list_())
        if self.peek() != "op":
            if left[0] == "attr":
                return ("cmp", "=", left[1], True)
            raise ConditionError(f"a literal alone is not a condition: {self.text!r}")
        op = self.take("op")
        right = self.operand()
        if left[0] == "lit" and right[0] == "attr":
            left, right, op = right, left, _FLIP[op]
        if left[0] != "attr" or right[0] != "lit":
            raise ConditionError(f"compare an attribute with a literal, in condition {self.text!r}")
        return ("cmp", op, left[1], right[1])

    def operand(self) -> tuple[str, Any]:
        kind = self.peek()
        if kind not in ("attr", "lit"):
            found = self.toks[self.i][1] if self.i < len(self.toks) else "the end"
            raise ConditionError(
                f"expected an attribute or a literal, found {found!r} in condition {self.text!r}"
            )
        return kind, self.take(kind)

    def list_(self) -> tuple[Any, ...]:
        self.take("[")
        items = [self.take("lit")]
        while self.peek() == ",":
            self.take(",")
            items.append(self.take("lit"))
        self.take("]")
        return tuple(items)


def parse(text: str) -> Node:
    """The condition's parsed form; raises ConditionError."""
    return _Parser(text).parse()


def literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _print(node: Node, parent: str | None = None) -> str:
    kind = node[0]
    if kind == "else":
        return ELSE
    if kind == "cmp":
        return f"{node[2]} {node[1]} {literal(node[3])}"
    if kind == "in":
        items = sorted({literal(v) for v in node[2]})
        return f"{node[1]} in [{', '.join(items)}]"
    if kind == "not":
        inner = node[1]
        return f"not {_print(inner)}" if inner[0] == "not" else f"not ({_print(inner)})"
    parts = sorted({_print(p, kind) for p in node[1:]})
    text = f" {kind} ".join(parts)
    return f"({text})" if parent == "and" and kind == "or" else text


def canonical(text: str) -> str:
    """One printed form for the condition; raises ConditionError."""
    return _print(_flatten(parse(text)))


def _flatten(node: Node) -> Node:
    kind = node[0]
    if kind in ("and", "or"):
        parts: list[Node] = []
        for p in node[1:]:
            p = _flatten(p)
            parts.extend(p[1:] if p[0] == kind else [p])
        return (kind, *parts)
    if kind == "not":
        return ("not", _flatten(node[1]))
    return node


def attributes(node: Node) -> set[str]:
    """The case attributes a condition reads."""
    kind = node[0]
    if kind in ("cmp", "in"):
        return {node[2] if kind == "cmp" else node[1]}
    if kind in ("and", "or"):
        return set().union(*(attributes(p) for p in node[1:]))
    if kind == "not":
        return attributes(node[1])
    return set()


def holds(node: Node, attrs: dict[str, Any]) -> bool:
    """Whether the condition holds for a case with these attributes. `else` never holds by
    itself: it is what is left when no other branch's condition does. Raises
    MissingAttribute for an attribute the case lacks."""
    kind = node[0]
    if kind == "else":
        return False
    if kind == "and":
        return all(holds(p, attrs) for p in node[1:])
    if kind == "or":
        return any(holds(p, attrs) for p in node[1:])
    if kind == "not":
        return not holds(node[1], attrs)
    name = node[2] if kind == "cmp" else node[1]
    if name not in attrs or attrs[name] is None:
        raise MissingAttribute(name)
    value = attrs[name]
    if kind == "in":
        return any(_equal(value, v) for v in node[2])
    op, lit = node[1], node[3]
    if op == "=":
        return _equal(value, lit)
    if op == "!=":
        return not _equal(value, lit)
    if (_number(value) and _number(lit)) or (isinstance(value, str) and isinstance(lit, str)):
        a, b = value, lit
    else:
        return False
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def _number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    return bool(a == b)
