"""Resolve call sites to their definitions with Jedi and report the callee's markers."""

import ast
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import jedi

from .markers import Marker, collect_aliases, marked_functions

log = logging.getLogger(__name__)

type MarkerTable = dict[int, set[Marker]]  # def line (1-based) -> markers


def marker_table(module: ast.Module) -> MarkerTable:
    aliases = collect_aliases(module)
    return {fn.lineno: markers for fn, markers in marked_functions(module, aliases)}


@dataclass(frozen=True)
class Target:
    """What a callee or function reference resolves to."""

    markers: frozenset[Marker]
    full_names: frozenset[str]  # e.g. "builtins.print", "pathlib.Path.read_text"
    # The subset Jedi knows to be a function or class, so not a constant like
    # `logging.INFO`. Some stdlib functions are missing: `random.randint` is a
    # module-level assignment.
    function_names: frozenset[str]


NOTHING = Target(frozenset(), frozenset(), frozenset())


def name_position(expr: ast.expr) -> tuple[int, int] | None:
    """Position (1-based line, 0-based column) of the last name segment of `expr`."""
    match expr:
        case ast.Name() as name:
            return name.lineno, name.col_offset
        case ast.Attribute(
            attr=attr, end_lineno=int() as line, end_col_offset=int() as col
        ):
            return line, col - len(attr)
    return None


class Resolver:
    def __init__(
        self,
        project: jedi.Project,
        open_source: Callable[[Path], str | None] = lambda _: None,
    ):
        self.project = project
        self._open_source = (
            open_source  # unsaved editor buffers take precedence over disk
        )
        self._cache: dict[Path, tuple[object, MarkerTable]] = {}

    def script(self, source: str, path: Path) -> jedi.Script:
        return jedi.Script(code=source, path=path, project=self.project)

    def resolve(
        self, script: jedi.Script, expr: ast.expr, current: MarkerTable
    ) -> Target:
        """Resolve a callee or a reference to a function (`roll` in `map(roll, xs)`)."""
        if (pos := name_position(expr)) is None:
            return NOTHING
        try:
            names = script.goto(*pos, follow_imports=True)
        except Exception:
            log.exception("jedi goto failed at %s:%s", script.path, pos)
            return NOTHING
        markers: set[Marker] = set()
        full_names = {name.full_name for name in names if name.full_name}
        function_names = {
            name.full_name
            for name in names
            if name.full_name and name.type in ("function", "class")
        }
        for name in names:
            if name.type != "function" or name.module_path is None or name.line is None:
                continue
            table = (
                current
                if name.module_path == script.path
                else self._table(name.module_path)
            )
            markers |= table.get(name.line, set())
        return Target(
            frozenset(markers), frozenset(full_names), frozenset(function_names)
        )

    def _table(self, path: Path) -> MarkerTable:
        if (source := self._open_source(path)) is not None:
            return self._parse(source, path)
        try:
            key: object = path.stat().st_mtime_ns
        except OSError:
            return {}
        if (cached := self._cache.get(path)) and cached[0] == key:
            return cached[1]
        try:
            table = self._parse(path.read_text(encoding="utf-8"), path)
        except (OSError, UnicodeDecodeError):
            table = {}
        self._cache[path] = (key, table)
        return table

    @staticmethod
    def _parse(source: str, path: Path) -> MarkerTable:
        try:
            return marker_table(ast.parse(source, filename=str(path)))
        except SyntaxError:
            return {}
