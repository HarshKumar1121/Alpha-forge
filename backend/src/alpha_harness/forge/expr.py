"""A small Fast Expression parser, so an Alpha can be checked *before* it costs a simulation.

Why this matters: every simulation spends daily quota. A typo, a wrong field type, or a Power
Pool Alpha with 9 operators is a wasted simulation. Linting offline is the cheapest
research-hygiene win there is.

Grammar handled (enough for real BRAIN Alphas)::

    program    := statement (';' statement)* ';'?
    statement  := NAME '=' expr | expr
    expr       := ternary
    ternary    := or ('?' expr ':' expr)?
    or / and   := '||' / '&&'
    compare    := sum (('<'|'<='|'>'|'>='|'=='|'!=') sum)*
    sum        := term (('+'|'-') term)*
    term       := power (('*'|'/') power)*
    power      := unary ('^' unary)*
    unary      := ('-'|'!') unary | call
    call       := NAME '(' args ')' | NAME | NUMBER | STRING | '(' expr ')'
    args       := (NAME '=' expr | expr) (',' ...)*

Operator counting follows the Power Pool rule as BRAIN counts it (measured by alpha-harness):
every call, every infix/comparison/logic op, every ternary and every unary minus counts;
``ts_backfill`` and ``group_backfill`` do not.

Ported from alpha-copilot ``expr.py``.
"""

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

UNCOUNTED = frozenset({"ts_backfill", "group_backfill"})
GROUPING_FIELDS = frozenset(
    {
        "market",
        "sector",
        "industry",
        "subindustry",
        "country",
        "exchange",
        "currency",
        "split",
        "adjfactor",
    }
)

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<num>\d+\.\d*|\.\d+|\d+(?:[eE][-+]?\d+)?)
      | (?P<str>"[^"]*"|'[^']*')
      | (?P<name>[A-Za-z_][A-Za-z0-9_.]*)
      | (?P<op><=|>=|==|!=|&&|\|\||[-+*/^<>!?:(),;=])
    )""",
    re.VERBOSE,
)

type Token = tuple[str, str]


class ParseError(ValueError):
    pass


@dataclass(slots=True)
class Node:
    kind: str  # call | binop | unary | ternary | name | num | str
    value: str = ""
    children: list[Node] = field(default_factory=list)
    kwargs: dict[str, Node] = field(default_factory=dict)


@dataclass(slots=True)
class Program:
    assignments: list[tuple[str, Node]]
    result: Node


def tokenize(text: str) -> list[Token]:
    # Strip BRAIN-style comments: researchers paste them from notes even though BRAIN
    # rejects them in some contexts.
    text = re.sub(r"(#|//)[^\n]*", "", text).strip()
    pos = 0
    out: list[Token] = []
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ParseError(f"Unexpected character at {pos}: {text[pos : pos + 15]!r}")
        kind = m.lastgroup or ""
        out.append((kind, m.group(kind)))
        pos = m.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return out


class _Parser:
    _LEVELS = (
        ("||",),
        ("&&",),
        ("<", "<=", ">", ">=", "==", "!="),
        ("+", "-"),
        ("*", "/"),
        ("^",),
    )

    def __init__(self, tokens: list[Token]) -> None:
        self.t = tokens
        self.i = 0

    def peek(self, k: int = 0) -> Token | None:
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    def eat(self, value: str | None = None) -> Token:
        tok = self.peek()
        if tok is None:
            raise ParseError("Unexpected end of expression")
        if value is not None and tok[1] != value:
            raise ParseError(f"Expected {value!r}, got {tok[1]!r}")
        self.i += 1
        return tok

    def at(self, *values: str) -> bool:
        tok = self.peek()
        return tok is not None and tok[0] == "op" and tok[1] in values

    def program(self) -> Program:
        assigns: list[tuple[str, Node]] = []
        last: Node | None = None
        while self.peek() is not None:
            if self.at(";"):
                self.eat()
                continue
            nxt, after = self.peek(), self.peek(1)
            if nxt and nxt[0] == "name" and after == ("op", "="):
                name = self.eat()[1]
                self.eat("=")
                assigns.append((name, self.expr()))
                last = Node("name", name)
            else:
                last = self.expr()
            if self.peek() is not None:
                self.eat(";")
        if last is None:
            raise ParseError("Empty expression")
        return Program(assigns, last)

    def expr(self) -> Node:
        cond = self.binary(0)
        if self.at("?"):
            self.eat()
            a = self.expr()
            self.eat(":")
            b = self.expr()
            return Node("ternary", "?:", [cond, a, b])
        return cond

    def binary(self, level: int) -> Node:
        if level == len(self._LEVELS):
            return self.unary()
        left = self.binary(level + 1)
        while self.at(*self._LEVELS[level]):
            op = self.eat()[1]
            right = self.binary(level + 1)
            left = Node("binop", op, [left, right])
        return left

    def unary(self) -> Node:
        if self.at("-", "!"):
            op = self.eat()[1]
            return Node("unary", op, [self.unary()])
        if self.at("+"):
            self.eat()
            return self.unary()
        return self.atom()

    def atom(self) -> Node:
        kind, value = self.eat()
        if kind == "num":
            return Node("num", value)
        if kind == "str":
            return Node("str", value)
        if kind == "op" and value == "(":
            node = self.expr()
            self.eat(")")
            return node
        if kind == "name":
            if not self.at("("):
                return Node("name", value)
            self.eat("(")
            call = Node("call", value)
            while not self.at(")"):
                nxt, after = self.peek(), self.peek(1)
                if nxt and nxt[0] == "name" and after == ("op", "="):
                    key = self.eat()[1]
                    self.eat("=")
                    call.kwargs[key] = self.expr()
                else:
                    call.children.append(self.expr())
                if self.at(","):
                    self.eat()
                elif not self.at(")"):
                    raise ParseError(f"Expected ',' or ')' in {value}(...)")
            self.eat(")")
            return call
        raise ParseError(f"Unexpected token {value!r}")


def parse(text: str) -> Program:
    return _Parser(tokenize(text)).program()


def walk(node: Node) -> Iterator[Node]:
    yield node
    for c in node.children:
        yield from walk(c)
    for c in node.kwargs.values():
        yield from walk(c)


@dataclass(frozen=True, slots=True)
class ExprInfo:
    """Everything an offline lint needs to know about one Alpha."""

    operator_count: int
    operators: tuple[str, ...]  # distinct call names
    fields: tuple[str, ...]  # distinct data fields (grouping fields excluded)
    grouping: tuple[str, ...]
    lookbacks: tuple[int, ...]  # integer literals given as the 2nd arg of ts_* calls
    depth: int


def _depth(node: Node) -> int:
    """Nesting of calls only: ``rank(a + b)`` is depth 1, however long the arithmetic."""
    kids = [*node.children, *node.kwargs.values()]
    deepest = max((_depth(k) for k in kids), default=0)
    return 1 + deepest if node.kind == "call" else deepest


def analyse(text: str) -> ExprInfo:
    prog = parse(text)
    locals_ = {name for name, _ in prog.assignments}
    roots = [n for _, n in prog.assignments] + [prog.result]
    count = 0
    ops: set[str] = set()
    names: set[str] = set()
    lookbacks: list[int] = []
    for root in roots:
        for n in walk(root):
            if n.kind == "call":
                ops.add(n.value)
                if n.value not in UNCOUNTED:
                    count += 1
                if n.value.startswith("ts_") and len(n.children) >= 2:
                    window = n.children[1]
                    if window.kind == "num":
                        lookbacks.append(int(float(window.value)))
            elif n.kind in {"binop", "ternary", "unary"}:
                count += 1
            elif n.kind == "name" and n.value not in locals_:
                names.add(n.value)
    return ExprInfo(
        operator_count=count,
        operators=tuple(sorted(ops)),
        fields=tuple(sorted(names - GROUPING_FIELDS)),
        grouping=tuple(sorted(names & GROUPING_FIELDS)),
        lookbacks=tuple(lookbacks),
        depth=max(_depth(r) for r in roots),
    )


def render_template(template: str, values: dict[str, str]) -> str:
    """Fill ``$name`` placeholders. Longest names first so ``$x1`` is not eaten by ``$x``."""
    out = template
    for key in sorted(values, key=len, reverse=True):
        out = out.replace(f"${key}", str(values[key]))
    if re.search(r"\$[A-Za-z_]", out):
        raise ParseError(f"Unfilled placeholder in: {out}")
    return out


def on_result(expression: str, wrap: Callable[[str], str]) -> str:
    """Apply ``wrap`` to an expression's result. In a multi-statement program the result is
    the last statement, so only it is wrapped: ``ts_delay(a = x; a, 5)`` would not parse."""
    head, sep, last = expression.strip().rstrip(";").rpartition(";")
    return f"{head}{sep} {wrap(last.strip())}" if sep else wrap(last.strip())
