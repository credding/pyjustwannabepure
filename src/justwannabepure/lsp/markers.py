"""Find which functions carry which marker decorator, using only the AST."""

import ast
import enum
from dataclasses import dataclass

PACKAGE = "justwannabepure"

type FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


class Marker(enum.Enum):
    PURE = "pure"
    SIDE_EFFECT = "side_effect"
    SHELL = "shell"


_BY_NAME = {m.value: m for m in Marker}


@dataclass(frozen=True)
class Aliases:
    """How the marker decorators are reachable by name in one module."""

    names: dict[str, Marker]  # `from justwannabepure import pure as p` -> {"p": PURE}
    modules: frozenset[str]  # `import justwannabepure as j` -> {"j"}


def collect_aliases(module: ast.Module) -> Aliases:
    names: dict[str, Marker] = {}
    modules = {PACKAGE}  # fully qualified `@justwannabepure.pure` always works
    for node in ast.walk(module):
        if isinstance(node, ast.ImportFrom) and node.module == PACKAGE and node.level == 0:
            for alias in node.names:
                if marker := _BY_NAME.get(alias.name):
                    names[alias.asname or alias.name] = marker
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PACKAGE:
                    modules.add(alias.asname or alias.name)
    return Aliases(names, frozenset(modules))


def decorator_marker(decorator: ast.expr, aliases: Aliases) -> Marker | None:
    match decorator:
        case ast.Name(id=name):
            return aliases.names.get(name)
        case ast.Attribute(value=ast.Name(id=module), attr=attr) if module in aliases.modules:
            return _BY_NAME.get(attr)
    return None


def markers_of(fn: FunctionNode, aliases: Aliases) -> set[Marker]:
    return {m for d in fn.decorator_list if (m := decorator_marker(d, aliases))}


def marked_functions(module: ast.Module, aliases: Aliases) -> list[tuple[FunctionNode, set[Marker]]]:
    return [
        (node, markers)
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (markers := markers_of(node, aliases))
    ]
