"""The inspections themselves, independent of the LSP plumbing."""

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .markers import (
    FunctionNode,
    Marker,
    collect_aliases,
    decorator_marker,
    marked_functions,
)
from .resolve import Resolver

CONTROL_FLOW: dict[type[ast.stmt], str] = {
    ast.If: "if",
    ast.Match: "match",
    ast.Try: "try",
    ast.TryStar: "try",
    ast.For: "for",
    ast.AsyncFor: "async for",
    ast.While: "while",
}


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

    def position(self, lineno: int, byte_col: int) -> Position:
        line = lineno - 1
        text = self.lines[line] if line < len(self.lines) else ""
        return Position(line, len(text.encode()[:byte_col].decode(errors="ignore")))

    def span(self, node: ast.expr | ast.stmt) -> tuple[Position, Position]:
        end_lineno = node.end_lineno or node.lineno
        end_col = node.end_col_offset if node.end_col_offset is not None else node.col_offset
        return self.position(node.lineno, node.col_offset), self.position(end_lineno, end_col)

    def header(self, fn: FunctionNode) -> tuple[Position, Position]:
        """From `async def` up to the end of the function name."""
        start = self.position(fn.lineno, fn.col_offset)
        text = self.lines[start.line]
        name_at = text.find(fn.name, text.find("def", start.character) + len("def"))
        return start, Position(start.line, name_at + len(fn.name) if name_at >= 0 else len(text))

    def keyword(self, stmt: ast.stmt, keyword: str) -> tuple[Position, Position]:
        start = self.position(stmt.lineno, stmt.col_offset)
        return start, Position(start.line, start.character + len(keyword))


def _body_nodes(fn: FunctionNode) -> Iterator[ast.AST]:
    """Everything lexically inside the function body, including nested defs and lambdas."""
    for stmt in fn.body:
        yield from ast.walk(stmt)


def parse(source: str, path: Path) -> ast.Module | None:
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError:
        return None


def check(module: ast.Module, source: str, path: Path, resolver: Resolver) -> list[Issue]:
    aliases = collect_aliases(module)
    functions = marked_functions(module, aliases)
    if not functions:
        return []

    loc = _Locator(source)
    current = {fn.lineno: markers for fn, markers in functions}
    script = None
    issues: list[Issue] = []

    for fn, markers in functions:
        if Marker.PURE in markers and Marker.SIDE_EFFECT in markers:
            for decorator in fn.decorator_list:
                if decorator_marker(decorator, aliases) is Marker.SIDE_EFFECT:
                    issues.append(Issue("JWP004", f"'{fn.name}' cannot be both @pure and @side_effect", *loc.span(decorator)))

        if Marker.PURE in markers:
            if isinstance(fn, ast.AsyncFunctionDef):
                issues.append(Issue("JWP001", f"@pure function '{fn.name}' must not be async", *loc.header(fn)))
            for node in _body_nodes(fn):
                if not isinstance(node, ast.Call):
                    continue
                script = script or resolver.script(source, path)
                if Marker.SIDE_EFFECT in resolver.markers_of_call(script, node, current):
                    callee = ast.unparse(node.func)
                    issues.append(
                        Issue(
                            "JWP002",
                            f"@pure function '{fn.name}' calls @side_effect function '{callee}'",
                            *loc.span(node.func),
                        )
                    )

        if Marker.SHELL in markers:
            for node in _body_nodes(fn):
                if keyword := CONTROL_FLOW.get(type(node)):
                    assert isinstance(node, ast.stmt)
                    issues.append(
                        Issue(
                            "JWP003",
                            f"@shell function '{fn.name}' must not contain control flow ('{keyword}')",
                            *loc.keyword(node, keyword),
                        )
                    )

    return sorted(issues, key=lambda i: (i.start.line, i.start.character, i.code))
