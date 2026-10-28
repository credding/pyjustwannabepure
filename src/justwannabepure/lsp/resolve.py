"""Resolve call sites to their definitions with Jedi and report the callee's markers."""

import ast
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import jedi
from jedi.api.classes import Name

from justwannabepure import pure, shell, side_effect

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

    @pure
    def lookup(self, line: int, qualname: str) -> set[Marker]:
        if (entry := self.by_line.get(line)) is not None and entry[0] == qualname:
            return entry[1]
        return self.by_qualname.get(qualname, set())


EMPTY_TABLE = MarkerTable({}, {})


@pure
def marker_table(module: ast.Module) -> MarkerTable:
    aliases = collect_aliases(module)
    by_line: dict[int, tuple[str, set[Marker]]] = {}
    by_qualname: dict[str, set[Marker]] = {}
    for qualname, fn in qualified_functions(module):
        if markers := markers_of(fn, aliases):
            by_line[fn.lineno] = (qualname, markers)
            by_qualname.setdefault(qualname, set()).update(markers)
    return MarkerTable(by_line, by_qualname)


@pure
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


@pure
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


@dataclass(frozen=True)
class Definition:
    """The parts of a Jedi `Name` that `target_from` needs."""

    full_name: str | None
    type: str
    module_path: Path | None
    line: int | None  # 1-based
    qualname: str


def _definition(name: Name) -> Definition:
    return Definition(
        name.full_name, name.type, name.module_path, name.line, _qualname(name)
    )


@pure
def located_functions(defs: list[Definition]) -> list[tuple[Path, int, str]]:
    """(module path, def line, qualname) of each definition that is a function on disk."""
    return [
        (d.module_path, d.line, d.qualname)
        for d in defs
        if d.type == "function" and d.module_path is not None and d.line is not None
    ]


@pure
def target_from(defs: list[Definition], tables: dict[Path, MarkerTable]) -> Target:
    """Merge the definitions a name resolved to; `tables` has each located function's module."""
    markers: set[Marker] = set()
    for path, line, qualname in located_functions(defs):
        markers |= tables[path].lookup(line, qualname)
    return Target(
        frozenset(markers),
        frozenset(d.full_name for d in defs if d.full_name),
        frozenset(
            d.full_name for d in defs if d.full_name and d.type in ("function", "class")
        ),
    )


class FileScript:
    """A Jedi script plus an index of every name in it by position.

    `jedi.Script.goto(line, column)` finds its name with a linear walk of the whole
    module, which is quadratic over a file's call sites. One `get_names` pass
    gives the same `Name`s up front.
    """

    def __init__(self, script: jedi.Script, names: dict[tuple[int, int], Name]):
        self.script = script
        self.path = script.path
        self._names = names

    @side_effect
    def definitions(self, pos: tuple[int, int]) -> list[Definition]:
        """What the name at `pos` resolves to; nothing if Jedi fails."""
        name = self._names.get(pos)
        try:
            names = (
                name.goto(follow_imports=True)
                if name is not None
                else self.script.goto(*pos, follow_imports=True)
            )
        except Exception:
            log.exception("jedi goto failed at %s:%s", self.path, pos)
            return []
        return [_definition(name) for name in names]


@side_effect
def index_names(script: jedi.Script) -> dict[tuple[int, int], Name]:
    try:
        names = script.get_names(all_scopes=True, definitions=True, references=True)
    except Exception:
        log.exception("jedi get_names failed for %s", script.path)
        return {}
    return {(name.line, name.column): name for name in names}


@side_effect
def mtime(path: Path) -> int | None:
    """`None` for a file that can't be stat'ed, so it gets an empty table."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


@side_effect
def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


@pure
def cached(entry: tuple[object, MarkerTable] | None, key: object) -> MarkerTable | None:
    return entry[1] if entry is not None and entry[0] == key else None


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

    @shell
    def script(self, source: str, path: Path) -> FileScript:
        script = jedi.Script(code=source, path=path, project=self.project)
        return FileScript(script, index_names(script))

    @shell
    def resolve(
        self, script: FileScript, expr: ast.expr, current: MarkerTable
    ) -> Target:
        """Resolve a callee or a reference to a function (`roll` in `map(roll, xs)`)."""
        pos = name_position(expr)
        defs = script.definitions(pos) if pos is not None else []
        tables = {
            path: current if path == script.path else self._table(path)
            for path, _, _ in located_functions(defs)
        }
        return target_from(defs, tables)

    @shell
    def _table(self, path: Path) -> MarkerTable:
        # Keyed by the buffer text for open documents, by mtime for files on disk.
        source = self._open_source(path)
        key: object = source if source is not None else mtime(path)
        match cached(self._cache.get(path), key):
            case MarkerTable() as table:
                return table
            case None:
                text = source if source is not None else read_text(path)
                table = self._parse(text, path)
                self._cache[path] = (key, table)
                return table

    @staticmethod
    @pure
    def _parse(source: str, path: Path) -> MarkerTable:
        try:
            return marker_table(ast.parse(source, filename=str(path)))
        except SyntaxError:
            return EMPTY_TABLE
