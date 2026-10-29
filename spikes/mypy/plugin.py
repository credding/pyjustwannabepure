"""Spike: the pyjustwannabepure rules as a mypy plugin.

Enable with `plugins = ["spikes/mypy/plugin.py"]` in the mypy config. The rule
logic is reused from `justwannabepure.lsp`; only the resolution changes, from
Jedi to mypy's own call hooks and types.

- Call-site rules (JWP002/005/008/009/010) run from `get_function_hook` and
  `get_method_hook`, which fire on every call mypy resolves.
- Value references (`map(roll, xs)`) are taken from those calls' arguments.
- Body rules (JWP001/003/004/006/007/011) run from the hook on the marker
  decorator itself, which mypy calls after checking the decorated function's body.
"""

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from mypy.checker import TypeChecker
from mypy.errorcodes import ErrorCode
from mypy.nodes import (
    CallExpr,
    Context,
    Decorator,
    Expression,
    FuncDef,
    FuncItem,
    LambdaExpr,
    MemberExpr,
    NameExpr,
    OverloadedFuncDef,
    RefExpr,
    TypeInfo,
)
from mypy.options import Options
from mypy.plugin import FunctionContext, MethodContext, Plugin, ReportConfigContext
from mypy.types import CallableType, Instance, Type, get_proper_type

from justwannabepure.lsp.checks import (
    _FORBIDDEN_CALLS,
    FunctionNode,
    _Locator,
    finalize,
    local_issues,
)
from justwannabepure.lsp.effects import Effects, load_effects
from justwannabepure.lsp.markers import Aliases, Marker, collect_aliases, markers_of
from justwannabepure.lsp.resolve import MarkerTable, marker_table

MARKER_DECORATORS = {
    "justwannabepure.pure",
    "justwannabepure.shell",
    "justwannabepure.side_effect",
}
CODES = {
    f"JWP{n:03}": ErrorCode(f"JWP{n:03}", "pyjustwannabepure", "pyjustwannabepure")
    for n in range(1, 12)
}


@dataclass(frozen=True)
class Parsed:
    source: str
    module: ast.Module
    aliases: Aliases
    table: MarkerTable
    functions: dict[int, FunctionNode]  # def line -> node


def parse(source: str, path: str) -> Parsed | None:
    # mypy asks about every module, typeshed included; most can't use the markers.
    if "justwannabepure" not in source:
        return None
    try:
        module = ast.parse(source, filename=path)
    except SyntaxError:
        return None
    return Parsed(
        source,
        module,
        collect_aliases(module),
        marker_table(module),
        {
            node.lineno: node
            for node in ast.walk(module)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        },
    )


class PurityPlugin(Plugin):
    def __init__(self, options: Options) -> None:
        super().__init__(options)
        root = Path(options.config_file).parent if options.config_file else Path.cwd()
        self.effects: Effects = load_effects(root)
        # pylsp-mypy's live mode checks an unsaved buffer through --shadow-file.
        self.shadows = {src: shadow for src, shadow in options.shadow_file or ()}
        self._parsed: dict[str, tuple[object, Parsed | None]] = {}

    def report_config_data(self, ctx: ReportConfigContext) -> object:
        # Markers don't change a decorated function's type, so a module whose
        # markers changed would look unchanged to its dependents. mypy hashes
        # this data into the module's interface, which makes them recheck.
        parsed = self.parsed(ctx.path)
        markers = (
            sorted(
                (qualname, sorted(m.value for m in found))
                for qualname, found in parsed.table.by_qualname.items()
            )
            if parsed is not None
            else []
        )
        return {"effects": list(self.effects.patterns), "markers": markers}

    def get_function_hook(
        self, fullname: str
    ) -> Callable[[FunctionContext], Type] | None:
        if fullname in MARKER_DECORATORS:
            return self.on_decorator
        return lambda ctx: self.on_call(ctx, fullname)

    def get_method_hook(self, fullname: str) -> Callable[[MethodContext], Type] | None:
        return lambda ctx: self.on_call(ctx, fullname)

    # -- source tables -------------------------------------------------------

    def parsed(self, path: str) -> Parsed | None:
        real = self.shadows.get(path, path)
        try:
            stat = Path(real).stat()
        except OSError:
            return None
        key = (real, stat.st_mtime_ns, stat.st_size)
        if (entry := self._parsed.get(path)) is not None and entry[0] == key:
            return entry[1]
        try:
            source = Path(real).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            result = None
        else:
            result = parse(source, path)
        self._parsed[path] = (key, result)
        return result

    def markers_for(self, api: TypeChecker, fullname: str) -> set[Marker]:
        """Markers of the function `fullname` (`pkg.mod.Class.method`), from its module's source."""
        parts = fullname.split(".")
        for i in range(len(parts) - 1, 0, -1):
            module = api.modules.get(".".join(parts[:i]))
            if module is not None:
                parsed = self.parsed(module.path)
                if parsed is None:
                    return set()
                return parsed.table.by_qualname.get(".".join(parts[i:]), set())
        return set()

    def caller(self, api: TypeChecker) -> tuple[str, set[Marker]] | None:
        """The outermost marked function enclosing the expression being checked."""
        parsed = self.parsed(api.path)
        if parsed is None:
            return None
        for item in api.scope.stack:
            if isinstance(item, FuncItem) and not isinstance(item, LambdaExpr):
                entry = parsed.table.by_line.get(item.line)
                if entry is not None:
                    return entry[0].rsplit(".", 1)[-1], entry[1]
        return None

    # -- reporting -----------------------------------------------------------

    def report_target(
        self,
        api: TypeChecker,
        caller: tuple[str, set[Marker]],
        fullname: str,
        shown: str,
        context: Context,
        verb: str,
        is_function: bool,
    ) -> None:
        name, markers = caller
        callee_markers = self.markers_for(api, fullname)
        for role in (Marker.PURE, Marker.SIDE_EFFECT):
            if role not in markers:
                continue
            for callee in (Marker.SIDE_EFFECT, Marker.SHELL):
                code = _FORBIDDEN_CALLS.get((role, callee))
                if code and callee in callee_markers:
                    api.fail(
                        f"@{role.value} function '{name}' {verb} @{callee.value} function '{shown}'",
                        context,
                        code=CODES[code],
                    )
        if Marker.PURE in markers and is_function and self.effects(fullname):
            api.fail(
                f"@pure function '{name}' {verb} effectful '{fullname}'",
                context,
                code=CODES["JWP005"],
            )

    # -- hooks ---------------------------------------------------------------

    def on_call(self, ctx: FunctionContext | MethodContext, fullname: str) -> Type:
        api = cast(TypeChecker, ctx.api)
        if (caller := self.caller(api)) is not None:
            if isinstance(ctx, MethodContext):
                fullname = _defining_fullname(ctx.type, fullname)
            call = ctx.context
            shown = (
                _shown(call.callee)
                if isinstance(call, CallExpr)
                else fullname.rsplit(".", 1)[-1]
            )
            self.report_target(api, caller, fullname, shown, call, "calls", True)
            for expr, target, is_function in _passed_references(api, ctx.args):
                self.report_target(
                    api, caller, target, _shown(expr), expr, "references", is_function
                )
        return ctx.default_return_type

    def on_decorator(self, ctx: FunctionContext) -> Type:
        api = cast(TypeChecker, ctx.api)
        decorator = ctx.context
        parsed = self.parsed(api.path)
        if not isinstance(decorator, Decorator) or parsed is None:
            return ctx.default_return_type
        fn = parsed.functions.get(decorator.func.line)
        if fn is None:
            return ctx.default_return_type
        markers = markers_of(fn, parsed.aliases)
        loc = _Locator(parsed.source)
        for issue in finalize(
            local_issues([(fn, markers)], parsed.aliases, loc), loc.lines
        ):
            api.fail(
                issue.message,
                Context(line=issue.start.line + 1, column=issue.start.character),
                code=CODES[issue.code],
            )
        return ctx.default_return_type


def _defining_fullname(receiver: Type, fullname: str) -> str:
    """`pathlib.PurePath.with_suffix` for `Path().with_suffix`, like Jedi reports it.

    mypy names a method after the receiver's static type, which would miss a
    marker on an inherited method and hit `pathlib.Path.*` for pure path methods.
    """
    obj = get_proper_type(receiver)
    if isinstance(obj, CallableType) and obj.is_type_obj():
        obj = obj.ret_type  # a classmethod or staticmethod called on the class
    obj = get_proper_type(obj)
    name = fullname.rsplit(".", 1)[-1]
    if isinstance(obj, Instance) and (info := obj.type.get_containing_type_info(name)):
        return f"{info.fullname}.{name}"
    return fullname


def _shown(expr: object) -> str:
    match expr:
        case NameExpr(name=name):
            return name
        case MemberExpr(expr=inner, name=name):
            return f"{_shown(inner)}.{name}"
    return "<expr>"


def _passed_references(
    api: TypeChecker, args: list[list[Expression]]
) -> list[tuple[RefExpr, str, bool]]:
    """(expr, fullname, is function or class) for each name or attribute passed as an argument.

    Compiled mypy won't let a plugin subclass `TraverserVisitor`, so container
    elements (`handlers = [roll]`) aren't seen; only call arguments are.
    """
    found = []
    for expr in (e for group in args for e in group):
        if not isinstance(expr, NameExpr | MemberExpr):
            continue
        node = expr.node
        if isinstance(node, FuncDef | Decorator | OverloadedFuncDef | TypeInfo):
            found.append((expr, node.fullname, True))
        elif isinstance(expr, MemberExpr) and expr.fullname:
            found.append((expr, expr.fullname, False))  # module constant
        elif isinstance(expr, MemberExpr) and api.has_type(expr.expr):
            # `d.roll` on an instance: semantic analysis can't know the class.
            obj = get_proper_type(api.lookup_type(expr.expr))
            if isinstance(obj, Instance) and (
                info := next((b for b in obj.type.mro if expr.name in b.names), None)
            ):
                member = info.names[expr.name].node
                found.append(
                    (
                        expr,
                        f"{info.fullname}.{expr.name}",
                        isinstance(member, FuncDef | Decorator | OverloadedFuncDef),
                    )
                )
    return found


def plugin(version: str) -> type[Plugin]:
    return PurityPlugin
