from pathlib import Path
from textwrap import dedent

import jedi
import pytest

from justwannabepure.lsp.checks import Issue, check, parse
from justwannabepure.lsp.resolve import Resolver

HEADER = "from justwannabepure import pure, shell, side_effect\n"


def run(
    tmp_path: Path, source: str, name: str = "main.py", **others: str
) -> list[Issue]:
    for module, text in others.items():
        (tmp_path / f"{module}.py").write_text(HEADER + dedent(text))
    path = tmp_path / name
    source = HEADER + dedent(source)
    path.write_text(source)
    module = parse(source, path)
    assert module is not None
    return check(module, source, path, Resolver(jedi.Project(tmp_path)))


def codes(issues: list[Issue]) -> list[tuple[str, int]]:
    """(code, 0-based line in the file; line 0 is HEADER)"""
    return [(i.code, i.start.line) for i in issues]


def test_async_pure(tmp_path: Path) -> None:
    [issue] = run(tmp_path, "@pure\nasync def f(): ...\n")
    assert issue.code == "JWP001"
    assert (issue.start.character, issue.end.character) == (0, len("async def f"))


def test_pure_calls_side_effect_same_file(tmp_path: Path) -> None:
    source = """
    @side_effect
    def roll(): ...

    def helper(): ...

    class Dice:
        @side_effect
        def roll(self): ...

    @pure
    def f(d: Dice):
        helper()
        d.roll()
        return roll()
    """
    assert codes(run(tmp_path, source)) == [("JWP002", 14), ("JWP002", 15)]


def test_pure_calls_side_effect_cross_module(tmp_path: Path) -> None:
    effects = """
    @side_effect
    def roll(): ...

    def safe(): ...
    """
    source = """
    import effects
    from effects import roll as r, safe

    @pure
    def f():
        safe()
        effects.safe()
        effects.roll()
        return r()
    """
    issues = run(tmp_path, source, effects=effects)
    assert codes(issues) == [("JWP002", 9), ("JWP002", 10)]
    assert "'effects.roll'" in issues[0].message
    # The range covers exactly the callee expression.
    assert (issues[0].start.character, issues[0].end.character) == (
        4,
        len("    effects.roll"),
    )


def test_nested_calls_inside_pure_are_checked(tmp_path: Path) -> None:
    source = """
    @side_effect
    def roll(): ...

    @pure
    def f():
        return lambda: roll()
    """
    assert codes(run(tmp_path, source)) == [("JWP002", 7)]


def test_side_effect_outside_pure_is_fine(tmp_path: Path) -> None:
    source = """
    @side_effect
    def roll(): ...

    def f():
        return roll()

    @shell
    def g():
        roll()
    """
    assert run(tmp_path, source) == []


def test_pure_and_side_effect_conflict(tmp_path: Path) -> None:
    assert codes(run(tmp_path, "@pure\n@side_effect\ndef f(): ...\n")) == [
        ("JWP004", 2)
    ]


@pytest.mark.parametrize(
    ("body", "keyword"),
    [
        ("if x:\n        pass", "if"),
        ("match x:\n        case _: pass", "match"),
        ("try:\n        pass\n    finally:\n        pass", "try"),
        ("try:\n        pass\n    except* ValueError:\n        pass", "try"),
        ("for _ in x:\n        pass", "for"),
        ("while x:\n        pass", "while"),
        ("def inner():\n        if x: pass", "if"),
    ],
)
def test_shell_control_flow(tmp_path: Path, body: str, keyword: str) -> None:
    [issue] = run(tmp_path, f"@shell\ndef f(x):\n    {body}\n")
    assert issue.code == "JWP003"
    assert f"'{keyword}'" in issue.message


def test_shell_async_for(tmp_path: Path) -> None:
    [issue] = run(
        tmp_path, "@shell\nasync def f(x):\n    async for _ in x:\n        pass\n"
    )
    assert (issue.code, issue.end.character - issue.start.character) == (
        "JWP003",
        len("async for"),
    )


def test_shell_allowed_constructs(tmp_path: Path) -> None:
    source = """
    @shell
    def f(x):
        y = 1 if x else 2
        zs = [z for z in range(y) if z]
        ok = x and y
        with open("f") as fh:
            fh.read()
        assert ok
        raise ValueError(zs)
    """
    assert run(tmp_path, source) == []


@pytest.mark.parametrize(
    "cases",
    [
        "case Ok(value): pass\n        case Err(error): pass",
        "case Ok(value=v): pass\n        case Err(): pass",
        "case Ok(Some(v)): pass\n        case Ok(Nothing()): pass",
        "case Ok() as ok: pass",
        "case int() | float(): pass",
        "case None: pass\n        case str(s): pass",
        "case Ok(v): pass\n        case _: raise ValueError(x)",
        "case Ok(v): pass\n        case other: raise ValueError(other)",
    ],
)
def test_shell_type_narrowing_match_allowed(tmp_path: Path, cases: str) -> None:
    assert run(tmp_path, f"@shell\ndef f(x):\n    match x:\n        {cases}\n") == []


@pytest.mark.parametrize(
    "cases",
    [
        "case 1: pass",
        "case Color.RED: pass",
        "case Ok(v) if v: pass",
        "case Ok(v): pass\n        case _ if x: pass",
        "case [a, b]: pass",
        "case {'k': v}: pass",
        "case True: pass",
        "case Point(x=0): pass",
        "case _: pass\n        case Ok(v): pass",
    ],
)
def test_shell_value_match_flagged(tmp_path: Path, cases: str) -> None:
    [issue] = run(tmp_path, f"@shell\ndef f(x):\n    match x:\n        {cases}\n")
    assert issue.code == "JWP003"
    assert "'match'" in issue.message


def test_shell_type_narrowing_match_body_still_checked(tmp_path: Path) -> None:
    source = """
    @shell
    def f(x):
        match x:
            case Ok(v):
                if v:
                    pass
    """
    [issue] = run(tmp_path, source)
    assert issue.code == "JWP003"
    assert "'if'" in issue.message


def test_non_ascii_columns_are_code_points(tmp_path: Path) -> None:
    source = """
    @side_effect
    def roll(): ...

    @pure
    def f():
        return ("héllo", roll())
    """
    [issue] = run(tmp_path, source)
    assert issue.start.character == len('    return ("héllo", ')
