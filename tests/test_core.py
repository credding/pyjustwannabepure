"""The pure core of the checker, without Jedi or files on disk."""

import ast
from pathlib import Path
from textwrap import dedent

import pytest

from justwannabepure.lsp.checks import (
    Issue,
    Position,
    Reference,
    _Locator,
    finalize,
    local_issues,
    reference_issues,
    references_to_resolve,
)
from justwannabepure.lsp.effects import BUILTIN_EFFECTS, Effects
from justwannabepure.lsp.markers import Marker, collect_aliases, marked_functions
from justwannabepure.lsp.resolve import (
    NOTHING,
    Definition,
    MarkerTable,
    Target,
    cached,
    located_functions,
    target_from,
)

HEADER = "from justwannabepure import pure, shell, side_effect\n"


def parse(source: str) -> tuple[ast.Module, str]:
    source = HEADER + dedent(source)
    return ast.parse(source), source


def local(source: str) -> list[tuple[str, int]]:
    module, source = parse(source)
    aliases = collect_aliases(module)
    issues = local_issues(marked_functions(module, aliases), aliases, _Locator(source))
    return [(i.code, i.start.line) for i in issues]


def refs(source: str) -> list[Reference]:
    module, _ = parse(source)
    return references_to_resolve(marked_functions(module, collect_aliases(module)))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def f(): ...\n", []),
        ("@pure\ndef f(x):\n    return x\n", []),
        ("@pure\nasync def f(): ...\n", [("JWP001", 2)]),
        ("@pure\n@side_effect\ndef f(): ...\n", [("JWP004", 2)]),
        ("@pure\ndef f():\n    global g\n", [("JWP006", 3)]),
        ("@pure\ndef f():\n    x = 0\n    def g():\n        nonlocal x\n", []),
        ("@pure\ndef f(xs):\n    xs.append(1)\n", [("JWP007", 3)]),
        ("@shell\ndef f(x):\n    if x: pass\n", [("JWP003", 3)]),
        ("@shell\ndef f(x):\n    return [y for y in x if y]\n", []),
        # The same code under `@side_effect` is not checked.
        ("@side_effect\ndef f(xs):\n    global g\n    xs.append(1)\n", []),
    ],
)
def test_local_issues(source: str, expected: list[tuple[str, int]]) -> None:
    assert local(source) == expected


def test_references_from_pure_and_side_effect_functions() -> None:
    found = refs(
        """
        @pure
        def f(xs):
            return map(g, xs) + h()

        @side_effect
        def e():
            return h()

        @shell
        def s():
            return h()
        """
    )
    assert [(r.function, r.role, ast.unparse(r.expr), r.called) for r in found] == [
        ("f", Marker.PURE, "map", True),
        ("f", Marker.PURE, "g", False),
        ("f", Marker.PURE, "xs", False),
        ("f", Marker.PURE, "h", True),
        ("e", Marker.SIDE_EFFECT, "h", True),
    ]


def test_references_none() -> None:
    assert refs("@shell\ndef s():\n    g()\n") == []


def target(
    *markers: Marker, names: tuple[str, ...] = (), functions: bool = True
) -> Target:
    return Target(
        frozenset(markers), frozenset(names), frozenset(names if functions else ())
    )


@pytest.mark.parametrize(
    ("called", "resolved", "effects", "expected"),
    [
        (True, NOTHING, BUILTIN_EFFECTS, []),
        (True, target(Marker.PURE), BUILTIN_EFFECTS, []),
        (True, target(Marker.SIDE_EFFECT), BUILTIN_EFFECTS, [("JWP002", "calls")]),
        (
            False,
            target(Marker.SIDE_EFFECT),
            BUILTIN_EFFECTS,
            [("JWP002", "references")],
        ),
        (True, target(Marker.SHELL), BUILTIN_EFFECTS, [("JWP008", "calls")]),
        (
            True,
            target(Marker.SIDE_EFFECT, Marker.SHELL),
            BUILTIN_EFFECTS,
            [("JWP002", "calls"), ("JWP008", "calls")],
        ),
        (
            True,
            target(names=("builtins.print",)),
            BUILTIN_EFFECTS,
            [("JWP005", "calls")],
        ),
        # A referenced constant like `logging.INFO` is not a function.
        (False, target(names=("logging.INFO",), functions=False), BUILTIN_EFFECTS, []),
        (
            True,
            target(names=("logging.INFO",), functions=False),
            BUILTIN_EFFECTS,
            [("JWP005", "calls")],
        ),
        (True, target(names=("requests.get",)), BUILTIN_EFFECTS, []),
        (
            True,
            target(names=("requests.get",)),
            Effects(("requests.*",)),
            [("JWP005", "calls")],
        ),
    ],
)
def test_reference_issues(
    called: bool,
    resolved: Target,
    effects: Effects,
    expected: list[tuple[str, str]],
) -> None:
    module, source = parse("@pure\ndef f():\n    g()\n")
    expr = ast.parse("g", mode="eval").body
    ref = Reference("f", Marker.PURE, ast.copy_location(expr, module.body[-1]), called)
    issues = reference_issues([ref], [resolved], effects, _Locator(source))
    assert [(i.code, i.message.split()[3]) for i in issues] == expected


@pytest.mark.parametrize(
    ("called", "resolved", "expected"),
    [
        (True, NOTHING, []),
        (True, target(Marker.PURE), []),
        (True, target(Marker.SIDE_EFFECT), [("JWP009", "calls")]),
        (False, target(Marker.SIDE_EFFECT), [("JWP009", "references")]),
        (True, target(Marker.SHELL), [("JWP010", "calls")]),
        (False, target(Marker.SHELL), [("JWP010", "references")]),
        (
            True,
            target(Marker.SIDE_EFFECT, Marker.SHELL),
            [("JWP009", "calls"), ("JWP010", "calls")],
        ),
        # Side effects may use effectful builtins and stdlib: that's their job.
        (True, target(names=("builtins.print",)), []),
    ],
)
def test_side_effect_reference_issues(
    called: bool, resolved: Target, expected: list[tuple[str, str]]
) -> None:
    module, source = parse("@side_effect\ndef f():\n    g()\n")
    expr = ast.parse("g", mode="eval").body
    ref = Reference(
        "f", Marker.SIDE_EFFECT, ast.copy_location(expr, module.body[-1]), called
    )
    issues = reference_issues([ref], [resolved], BUILTIN_EFFECTS, _Locator(source))
    assert [(i.code, i.message.split()[3]) for i in issues] == expected


def test_reference_issues_names_lowest_effect() -> None:
    _, source = parse("")
    ref = Reference("f", Marker.PURE, ast.parse("x", mode="eval").body, True)
    [issue] = reference_issues(
        [ref],
        [target(names=("time.time", "builtins.print"))],
        BUILTIN_EFFECTS,
        _Locator(source),
    )
    assert issue.message == "@pure function 'f' calls effectful 'builtins.print'"


def test_reference_issues_lengths_must_match() -> None:
    ref = Reference("f", Marker.PURE, ast.parse("x", mode="eval").body, True)
    with pytest.raises(ValueError):
        reference_issues([ref], [], BUILTIN_EFFECTS, _Locator(""))


def issue(code: str, line: int, character: int = 0) -> Issue:
    at = Position(line, character)
    return Issue(code, "", at, at)


@pytest.mark.parametrize(
    ("comment", "kept"),
    [
        ("", ["JWP002", "JWP005"]),
        ("  # jwp: ignore", []),
        ("  # jwp: ignore[JWP005]", ["JWP002"]),
        ("  #jwp:ignore[JWP002, JWP005]", []),
        ("  # jwp: ignore[JWP007]", ["JWP002", "JWP005"]),
    ],
)
def test_finalize_suppression(comment: str, kept: list[str]) -> None:
    issues = [issue("JWP005", 0), issue("JWP002", 0)]
    assert [i.code for i in finalize(issues, ["x()" + comment])] == kept


def test_finalize_sorts_by_position_then_code() -> None:
    issues = [
        issue("JWP001", 2),
        issue("JWP007", 1, 4),
        issue("JWP002", 1, 4),
        issue("JWP003", 1, 0),
    ]
    assert [(i.code, i.start.line) for i in finalize(issues, [])] == [
        ("JWP003", 1),
        ("JWP002", 1),
        ("JWP007", 1),
        ("JWP001", 2),
    ]


def test_finalize_empty() -> None:
    assert finalize([], []) == []


A = Path("a.py")
B = Path("b.py")


def definition(
    full_name: str | None = None,
    type: str = "function",
    module_path: Path | None = A,
    line: int | None = 1,
    qualname: str = "f",
) -> Definition:
    return Definition(full_name, type, module_path, line, qualname)


def test_located_functions_skips_non_functions_and_unlocated() -> None:
    defs = [
        definition(qualname="f"),
        definition(type="class"),
        definition(module_path=None),
        definition(line=None),
        definition(module_path=B, line=7, qualname="C.m"),
    ]
    assert located_functions(defs) == [(A, 1, "f"), (B, 7, "C.m")]


def test_target_from_merges_markers_and_names() -> None:
    tables = {
        A: MarkerTable({1: ("f", {Marker.PURE})}, {"f": {Marker.PURE}}),
        B: MarkerTable({}, {"g": {Marker.SIDE_EFFECT}}),
    }
    defs = [
        definition("a.f"),
        definition(
            "b.g", module_path=B, line=99, qualname="g"
        ),  # moved line: falls back to qualname
        definition("a.C", type="class", module_path=None),
        definition("a.X", type="statement"),
        definition(None),
    ]
    assert target_from(defs, tables) == Target(
        frozenset({Marker.PURE, Marker.SIDE_EFFECT}),
        frozenset({"a.f", "b.g", "a.C", "a.X"}),
        frozenset({"a.f", "b.g", "a.C"}),
    )


def test_target_from_nothing() -> None:
    assert target_from([], {}) == NOTHING


TABLE = MarkerTable({1: ("f", {Marker.PURE})}, {"f": {Marker.PURE}})


@pytest.mark.parametrize(
    ("entry", "key", "expected"),
    [
        (None, 1, None),
        ((1, TABLE), 1, TABLE),
        ((1, TABLE), 2, None),
        (("src", TABLE), "src", TABLE),
        (("src", TABLE), "edited", None),
        ((None, TABLE), None, TABLE),  # a file that still can't be stat'ed
        ((None, TABLE), 1, None),
    ],
)
def test_cached(
    entry: tuple[object, MarkerTable] | None, key: object, expected: MarkerTable | None
) -> None:
    assert cached(entry, key) is expected
