"""Resolve call sites to their definitions with Jedi and report the callee's markers."""

import ast
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import jedi
from jedi.api.classes import Name

from .markers import Marker, collect_aliases, markers_of, qualified_functions

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarkerTable:
    """The marked functions of one module.

    Jedi reports a definition by its line on disk, but an open editor buffer may
    have moved it, so a line only counts if the function there has the expected
    qualified name.
    """

    by_line: dict[
        int, tuple[str, set[Marker]]
    ]  # def line (1-based) -> (qualname, markers)
    by_qualname: dict[str, set[Marker]]  # merged across same-named defs (`if`/`else`)

    def lookup(self, line: int, qualname: str) -> set[Marker]:
        if (entry := self.by_line.get(line)) is not None and entry[0] == qualname:
            return entry[1]
        return self.by_qualname.get(qualname, set())


EMPTY_TABLE = MarkerTable({}, {})


def marker_table(module: ast.Module) -> MarkerTable:
    aliases = collect_aliases(module)
    by_line: dict[int, tuple[str, set[Marker]]] = {}
    by_qualname: dict[str, set[Marker]] = {}
    for qualname, fn in qualified_functions(module):
        if markers := markers_of(fn, aliases):
            by_line[fn.lineno] = (qualname, markers)
            by_qualname.setdefault(qualname, set()).update(markers)
    return MarkerTable(by_line, by_qualname)


def _qualname(name: Name) -> str:
    """`Dice.roll` for `pkg.effects.Dice.roll`."""
    prefix = f"{name.module_name}."
    if name.full_name and name.full_name.startswith(prefix):
        return name.full_name.removeprefix(prefix)
    return name.name


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


class FileScript:
    """A Jedi script plus an index of every name in it by position.

    `jedi.Script.goto(line, column)` finds its name with a linear walk of the whole
    module, which is quadratic over a file's call sites. One `get_names` pass
    gives the same `Name`s up front.
    """

    def __init__(self, script: jedi.Script):
        self.script = script
        self.path = script.path
        self._names: dict[tuple[int, int], Name] | None = None

    def goto(self, pos: tuple[int, int]) -> list[Name]:
        if self._names is None:
            self._names = self._index()
        if (name := self._names.get(pos)) is not None:
            return name.goto(follow_imports=True)
        return self.script.goto(*pos, follow_imports=True)

    def _index(self) -> dict[tuple[int, int], Name]:
        try:
            names = self.script.get_names(
                all_scopes=True, definitions=True, references=True
            )
        except Exception:
            log.exception("jedi get_names failed for %s", self.path)
            return {}
        return {(name.line, name.column): name for name in names}


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

    def script(self, source: str, path: Path) -> FileScript:
        return FileScript(jedi.Script(code=source, path=path, project=self.project))

    def resolve(
        self, script: FileScript, expr: ast.expr, current: MarkerTable
    ) -> Target:
        """Resolve a callee or a reference to a function (`roll` in `map(roll, xs)`)."""
        if (pos := name_position(expr)) is None:
            return NOTHING
        try:
            names = script.goto(pos)
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
            markers |= table.lookup(name.line, _qualname(name))
        return Target(
            frozenset(markers), frozenset(full_names), frozenset(function_names)
        )

    def _table(self, path: Path) -> MarkerTable:
        # Keyed by the buffer text for open documents, by mtime for files on disk.
        source = self._open_source(path)
        if source is not None:
            key: object = source
        else:
            try:
                key = path.stat().st_mtime_ns
            except OSError:
                return EMPTY_TABLE
        if (cached := self._cache.get(path)) and cached[0] == key:
            return cached[1]
        if source is None:
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                source = ""
        table = self._parse(source, path)
        self._cache[path] = (key, table)
        return table

    @staticmethod
    def _parse(source: str, path: Path) -> MarkerTable:
        try:
            return marker_table(ast.parse(source, filename=str(path)))
        except SyntaxError:
            return EMPTY_TABLE
