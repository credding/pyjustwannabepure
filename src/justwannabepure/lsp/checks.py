"""The inspections themselves, independent of the LSP plumbing."""

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from justwannabepure import pure, shell, side_effect

from .effects import BUILTIN_EFFECTS, Effects
from .markers import (
    Aliases,
    FunctionNode,
    Marker,
    collect_aliases,
    decorator_marker,
    marked_functions,
)
from .resolve import Resolver, Target, marker_table

CONTROL_FLOW: dict[type[ast.stmt], str] = {
    ast.If: "if",
    ast.Match: "match",
    ast.Try: "try",
    ast.TryStar: "try",
    ast.For: "for",
    ast.AsyncFor: "async for",
    ast.While: "while",
    ast.Raise: "raise",
    ast.Assert: "assert",
}

MUTATORS = frozenset(
    {
        "append",
        "extend",
        "insert",
        "pop",
        "popitem",
        "remove",
        "clear",
        "update",
        "setdefault",
        "add",
        "discard",
        "sort",
        "reverse",
    }
)

# `# jwp: ignore` or `# jwp: ignore[JWP005, JWP007]`
_IGNORE = re.compile(r"#\s*jwp:\s*ignore(?:\[(?P<codes>[^\]]*)\])?")


@dataclass(frozen=True)
class Position:
    line: int  # 0-based
    character: int  # 0-based, in code points


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    start: Position
    end: Position


class _Locator:
    """Turns `ast` locations (1-based lines, UTF-8 byte columns) into `Position`s."""

    def __init__(self, source: str):
        self.lines = source.splitlines()

    @pure
    def position(self, lineno: int, byte_col: int) -> Position:
        line = lineno - 1
        text = self.lines[line] if line < len(self.lines) else ""
        return Position(line, len(text.encode()[:byte_col].decode(errors="ignore")))

    @pure
    def span(self, node: ast.expr | ast.stmt) -> tuple[Position, Position]:
        end_lineno = node.end_lineno or node.lineno
        end_col = (
            node.end_col_offset if node.end_col_offset is not None else node.col_offset
        )
        return self.position(node.lineno, node.col_offset), self.position(
            end_lineno, end_col
        )

    @pure
    def header(self, fn: FunctionNode) -> tuple[Position, Position]:
        """From `async def` up to the end of the function name."""
        start = self.position(fn.lineno, fn.col_offset)
        text = self.lines[start.line]
        name_at = text.find(fn.name, text.find("def", start.character) + len("def"))
        return start, Position(
            start.line, name_at + len(fn.name) if name_at >= 0 else len(text)
        )

    @pure
    def keyword(self, stmt: ast.stmt, keyword: str) -> tuple[Position, Position]:
        start = self.position(stmt.lineno, stmt.col_offset)
        return start, Position(start.line, start.character + len(keyword))


@pure
def _body_nodes(fn: FunctionNode) -> Iterator[ast.AST]:
    """Everything lexically inside the function body, including nested defs and lambdas."""
    for stmt in fn.body:
        yield from ast.walk(stmt)


@pure
def _own_nodes(fn: FunctionNode) -> Iterator[ast.AST]:
    """Like `_body_nodes`, but without descending into nested defs, lambdas or classes."""
    stack: list[ast.AST] = list(fn.body)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef
        ):
            stack.extend(ast.iter_child_nodes(node))


@pure
def _passed_values(node: ast.AST) -> list[ast.expr]:
    """Expressions handed to something else: call arguments and container elements."""
    match node:
        case ast.Call(args=args, keywords=keywords):
            return [*args, *(keyword.value for keyword in keywords)]
        case ast.List(elts=elts) | ast.Tuple(elts=elts) | ast.Set(elts=elts):
            return elts
        case ast.Dict(values=values):
            return values
    return []


@pure
def _references(fn: FunctionNode) -> Iterator[tuple[ast.expr, bool]]:
    """Callees (`True`) and names passed around as values (`False`), like `roll` in `map(roll, xs)`."""
    for node in _body_nodes(fn):
        if isinstance(node, ast.Call):
            yield node.func, True
        for value in _passed_values(node):
            if isinstance(value, ast.Name | ast.Attribute) and isinstance(
                value.ctx, ast.Load
            ):
                yield value, False


@pure
def _root_name(expr: ast.expr) -> str | None:
    """`x` in `x.a[0].b`."""
    while isinstance(expr, ast.Attribute | ast.Subscript):
        expr = expr.value
    return expr.id if isinstance(expr, ast.Name) else None


@pure
def _mutations(fn: FunctionNode) -> Iterator[tuple[ast.expr, str]]:
    """Attribute/item stores, deletes and mutating method calls on a parameter."""
    args = fn.args
    params = {
        arg.arg
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]
        + [arg for arg in (args.vararg, args.kwarg) if arg]
    }
    nodes = list(_body_nodes(fn))
    # A parameter rebound anywhere (`xs = list(xs)`) no longer reliably names the argument.
    params -= {
        node.id
        for node in nodes
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del)
    }
    for node in nodes:
        match node:
            case (
                ast.Attribute(ctx=ast.Store() | ast.Del())
                | ast.Subscript(ctx=ast.Store() | ast.Del())
            ):
                target = node
            case ast.Call(func=ast.Attribute(attr=attr) as func) if attr in MUTATORS:
                target = func
            case _:
                continue
        if (name := _root_name(target)) in params:
            yield target, name


@pure
def _suppressed(issue: "Issue", lines: list[str]) -> bool:
    line = lines[issue.start.line] if issue.start.line < len(lines) else ""
    if (found := _IGNORE.search(line)) is None:
        return False
    codes = found["codes"]
    return codes is None or issue.code in {c.strip() for c in codes.split(",")}


@pure
def _is_capture(pattern: ast.pattern) -> bool:
    """`_` or a bare name: matches anything."""
    return isinstance(pattern, ast.MatchAs) and pattern.pattern is None


@pure
def _is_type_pattern(pattern: ast.pattern) -> bool:
    """A pattern that only checks types: `Ok(x)`, `Err(error=e)`, `None`, `A() | B()`."""
    match pattern:
        case ast.MatchClass(patterns=positional, kwd_patterns=keyword):
            return all(
                _is_capture(sub) or _is_type_pattern(sub)
                for sub in [*positional, *keyword]
            )
        case ast.MatchSingleton(value=None):
            return True
        case ast.MatchAs(pattern=ast.pattern() as inner):
            return _is_type_pattern(inner)
        case ast.MatchOr(patterns=alternatives):
            return all(_is_type_pattern(alt) for alt in alternatives)
        case _:
            return False


@pure
def _is_type_narrowing(node: ast.Match) -> bool:
    """Every case narrows by type, except possibly a trailing catch-all."""
    *cases, last = node.cases
    if last.guard is not None:
        return False
    if not _is_capture(last.pattern):
        cases.append(last)
    return bool(cases) and all(
        case.guard is None and _is_type_pattern(case.pattern) for case in cases
    )


@pure
def parse(source: str, path: Path) -> ast.Module | None:
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError:
        return None


@dataclass(frozen=True)
class Reference:
    """A callee or a name passed around as a value inside a `@pure` or `@side_effect` function."""

    function: str  # the referencing function's name
    role: Marker  # its marker: PURE or SIDE_EFFECT
    expr: ast.expr
    called: bool


# Markers whose functions restrict what they may reference.
_REFERENCING = (Marker.PURE, Marker.SIDE_EFFECT)


@pure
def references_to_resolve(
    functions: list[tuple[FunctionNode, set[Marker]]],
) -> list[Reference]:
    return [
        Reference(fn.name, role, expr, called)
        for fn, markers in functions
        for role in _REFERENCING
        if role in markers
        for expr, called in _references(fn)
    ]


@pure
def local_issues(
    functions: list[tuple[FunctionNode, set[Marker]]], aliases: Aliases, loc: _Locator
) -> list[Issue]:
    """Everything that needs no name resolution: JWP001, JWP003, JWP004, JWP006, JWP007."""
    issues: list[Issue] = []
    for fn, markers in functions:
        if Marker.PURE in markers and Marker.SIDE_EFFECT in markers:
            for decorator in fn.decorator_list:
                if decorator_marker(decorator, aliases) is Marker.SIDE_EFFECT:
                    issues.append(
                        Issue(
                            "JWP004",
                            f"'{fn.name}' cannot be both @pure and @side_effect",
                            *loc.span(decorator),
                        )
                    )

        if Marker.PURE in markers:
            if isinstance(fn, ast.AsyncFunctionDef):
                issues.append(
                    Issue(
                        "JWP001",
                        f"@pure function '{fn.name}' must not be async",
                        *loc.header(fn),
                    )
                )
            for node in _body_nodes(fn):
                if isinstance(node, ast.Global):
                    issues.append(
                        Issue(
                            "JWP006",
                            f"@pure function '{fn.name}' must not use 'global'",
                            *loc.keyword(node, "global"),
                        )
                    )
            # `nonlocal` in a nested def only rebinds the pure function's own locals.
            for node in _own_nodes(fn):
                if isinstance(node, ast.Nonlocal):
                    issues.append(
                        Issue(
                            "JWP006",
                            f"@pure function '{fn.name}' must not use 'nonlocal'",
                            *loc.keyword(node, "nonlocal"),
                        )
                    )
            for target, param in _mutations(fn):
                issues.append(
                    Issue(
                        "JWP007",
                        f"@pure function '{fn.name}' mutates its parameter '{param}'",
                        *loc.span(target),
                    )
                )

        if Marker.SHELL in markers:
            for node in _body_nodes(fn):
                if isinstance(node, ast.Match) and _is_type_narrowing(node):
                    continue
                if keyword := CONTROL_FLOW.get(type(node)):
                    assert isinstance(node, ast.stmt)
                    issues.append(
                        Issue(
                            "JWP003",
                            f"@shell function '{fn.name}' must not contain control flow ('{keyword}')",
                            *loc.keyword(node, keyword),
                        )
                    )
    return issues


# (caller role, callee marker) -> code
_FORBIDDEN_CALLS: dict[tuple[Marker, Marker], str] = {
    (Marker.PURE, Marker.SIDE_EFFECT): "JWP002",
    (Marker.PURE, Marker.SHELL): "JWP008",
    (Marker.SIDE_EFFECT, Marker.SIDE_EFFECT): "JWP009",
    (Marker.SIDE_EFFECT, Marker.SHELL): "JWP010",
}


@pure
def reference_issues(
    refs: list[Reference], targets: list[Target], effects: Effects, loc: _Locator
) -> list[Issue]:
    """JWP002, JWP005, JWP008, JWP009 and JWP010, from each reference and what it resolved to."""
    issues: list[Issue] = []
    for ref, target in zip(refs, targets, strict=True):
        verb = "calls" if ref.called else "references"
        name = ast.unparse(ref.expr)
        caller = f"@{ref.role.value} function '{ref.function}'"
        for callee in (Marker.SIDE_EFFECT, Marker.SHELL):
            if callee in target.markers and (
                code := _FORBIDDEN_CALLS.get((ref.role, callee))
            ):
                issues.append(
                    Issue(
                        code,
                        f"{caller} {verb} @{callee.value} function '{name}'",
                        *loc.span(ref.expr),
                    )
                )
        if ref.role is not Marker.PURE:
            continue
        full_names = target.full_names if ref.called else target.function_names
        if effectful := min(filter(effects, full_names), default=None):
            issues.append(
                Issue(
                    "JWP005",
                    f"{caller} {verb} effectful '{effectful}'",
                    *loc.span(ref.expr),
                )
            )
    return issues


@pure
def finalize(issues: list[Issue], lines: list[str]) -> list[Issue]:
    """Drop suppressed issues and sort by position."""
    return sorted(
        (issue for issue in issues if not _suppressed(issue, lines)),
        key=lambda i: (i.start.line, i.start.character, i.code),
    )


@side_effect
def resolve_all(
    resolver: Resolver,
    source: str,
    path: Path,
    refs: list[Reference],
    module: ast.Module,
) -> list[Target]:
    if not refs:
        return []  # don't pay for a Jedi script or the marker table
    script = resolver.script(source, path)
    current = marker_table(module)
    return [resolver.resolve(script, ref.expr, current) for ref in refs]


@shell
def check(
    module: ast.Module,
    source: str,
    path: Path,
    resolver: Resolver,
    effects: Effects = BUILTIN_EFFECTS,
) -> list[Issue]:
    aliases = collect_aliases(module)
    functions = marked_functions(module, aliases)
    loc = _Locator(source)
    refs = references_to_resolve(functions)
    targets = resolve_all(resolver, source, path, refs, module)
    return finalize(
        [
            *local_issues(functions, aliases, loc),
            *reference_issues(refs, targets, effects, loc),
        ],
        loc.lines,
    )
