"""A digest of what other modules' diagnostics can depend on in a module.

Checking one module looks into others for two things: the markers on the
functions it calls, and the types Jedi infers on the way there. Neither depends
on the body of a function with a return annotation, except a method's
`self.x = ...` assignments, which give the class its attributes. Everything
else counts, unannotated function bodies included, since Jedi infers their
return types from them.
"""

import ast
import hashlib
from collections.abc import Iterator

from justwannabepure import pure

from .markers import FunctionNode

# The digest of a module that doesn't parse: its markers read as none at all.
UNPARSABLE = ""


@pure
def interface_digest(source: str) -> str:
    try:
        module = ast.parse(source)
    except SyntaxError:
        return UNPARSABLE
    summary = "\n".join(_summary(module.body, in_class=False))
    return hashlib.sha256(summary.encode()).hexdigest()


@pure
def _summary(body: list[ast.stmt], in_class: bool) -> Iterator[str]:
    for stmt in body:
        match stmt:
            case (
                ast.FunctionDef(returns=ast.expr())
                | ast.AsyncFunctionDef(returns=ast.expr())
            ):
                yield _signature(stmt)
                if in_class:
                    yield from map(ast.dump, _self_assignments(stmt))
            case ast.ClassDef(body=class_body):
                yield _class_header(stmt)
                yield from _summary(class_body, in_class=True)
                yield f"end {stmt.name}"
            case _:
                yield ast.dump(stmt)


@pure
def _signature(fn: FunctionNode) -> str:
    parts = [fn.args, fn.returns, *fn.decorator_list, *fn.type_params]
    return " ".join([type(fn).__name__, fn.name, *map(ast.dump, filter(None, parts))])


@pure
def _class_header(cls: ast.ClassDef) -> str:
    parts = [*cls.bases, *cls.keywords, *cls.decorator_list, *cls.type_params]
    return " ".join(["class", cls.name, *map(ast.dump, parts)])


@pure
def _self_assignments(fn: FunctionNode) -> list[ast.stmt]:
    """Assignments to an attribute of the first parameter, anywhere in the method."""
    params = [*fn.args.posonlyargs, *fn.args.args]
    if not params:
        return []
    owner = params[0].arg
    return [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign)
        and any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == owner
            for assigned in _targets(node)
            for target in ast.walk(assigned)
        )
    ]


@pure
def _targets(node: ast.Assign | ast.AnnAssign | ast.AugAssign) -> list[ast.expr]:
    return node.targets if isinstance(node, ast.Assign) else [node.target]
