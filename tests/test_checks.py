from pathlib import Path
from textwrap import dedent

import jedi
import pytest

from justwannabepure.lsp.checks import Issue, check, parse
from justwannabepure.lsp.effects import Effects, load_effects
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


def test_side_effect_calls_side_effect_and_shell(tmp_path: Path) -> None:
    effects = """
    @side_effect
    def roll(): ...
    """
    source = """
    from effects import roll

    def helper(): ...

    @shell
    def main(): ...

    class Dice:
        @side_effect
        def roll(self):
            return roll()

    @side_effect
    def f(d: Dice):
        helper()
        print(d.roll())
        main()
        return list(map(roll, [1]))
    """
    issues = run(tmp_path, source, effects=effects)
    assert codes(issues) == [
        ("JWP009", 12),
        ("JWP009", 17),
        ("JWP010", 18),
        ("JWP009", 19),
    ]
    assert issues[0].message == (
        "@side_effect function 'roll' calls @side_effect function 'roll'"
    )
    assert issues[2].message == "@side_effect function 'f' calls @shell function 'main'"
    assert issues[3].message == (
        "@side_effect function 'f' references @side_effect function 'roll'"
    )


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
        ("raise ValueError(x)", "raise"),
        ("assert x", "assert"),
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
        print(ok, zs)
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
        "case Ok(v): pass\n        case _: print(x)",
        "case Ok(v): pass\n        case other: print(other)",
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


def test_pure_calls_effectful_stdlib(tmp_path: Path) -> None:
    source = """
    import random, time
    from datetime import datetime
    from pathlib import Path

    @pure
    def f(p: Path):
        print("hi")
        random.randint(1, 6)
        time.time()
        datetime.now()
        p.read_text()
        p.with_suffix(".txt")
        return sorted([3, 1])
    """
    issues = run(tmp_path, source)
    assert codes(issues) == [
        ("JWP005", 8),
        ("JWP005", 9),
        ("JWP005", 10),
        ("JWP005", 11),
        ("JWP005", 12),
    ]
    assert "'builtins.print'" in issues[0].message


def test_pure_os_path_string_helpers_are_fine(tmp_path: Path) -> None:
    source = """
    import os

    @pure
    def f(p):
        os.path.exists(p)
        return os.path.join(os.fspath(p), "x")
    """
    assert codes(run(tmp_path, source)) == [("JWP005", 6)]


def test_pure_references_effectful_functions(tmp_path: Path) -> None:
    source = """
    import logging

    @side_effect
    def roll(): ...

    @pure
    def f(xs):
        handlers = [roll]
        logging.log(logging.INFO, "x")
        return map(print, xs), handlers
    """
    issues = run(tmp_path, source)
    # `logging.INFO` is a constant, not an effect.
    assert codes(issues) == [("JWP002", 9), ("JWP005", 10), ("JWP005", 11)]
    assert "references @side_effect function 'roll'" in issues[0].message
    assert "references effectful 'builtins.print'" in issues[2].message


def test_pure_global_and_nonlocal(tmp_path: Path) -> None:
    source = """
    def outer():
        n = 0

        @pure
        def f():
            global g
            nonlocal n

            def counter():
                nonlocal m  # only rebinds f's own local
            m = 0
    """
    issues = run(tmp_path, source)
    assert codes(issues) == [("JWP006", 7), ("JWP006", 8)]
    assert (issues[0].start.character, issues[0].end.character) == (8, 14)


def test_pure_mutates_parameter(tmp_path: Path) -> None:
    source = """
    @pure
    def f(xs, d, self, *, opts):
        xs.append(1)
        d["k"] = 1
        del d["j"]
        self.cache.items[0] += 1
        opts.a = 1
        local = []
        local.append(1)
        return [*xs, local, len(d)]
    """
    issues = run(tmp_path, source)
    assert codes(issues) == [
        ("JWP007", 4),
        ("JWP007", 5),
        ("JWP007", 6),
        ("JWP007", 7),
        ("JWP007", 8),
    ]
    assert "parameter 'self'" in issues[3].message


def test_pure_rebound_parameter_may_be_mutated(tmp_path: Path) -> None:
    source = """
    @pure
    def f(xs):
        xs = list(xs)
        xs.append(1)
        return xs
    """
    assert run(tmp_path, source) == []


def test_pure_calls_shell(tmp_path: Path) -> None:
    source = """
    @shell
    def main(): ...

    @pure
    def f():
        main()
    """
    [issue] = run(tmp_path, source)
    assert issue.code == "JWP008"


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("# jwp: ignore", []),
        ("# jwp: ignore[JWP005]", []),
        ("#jwp:ignore[JWP002, JWP005]", []),
        ("# jwp: ignore[JWP007]", [("JWP005", 3)]),
        ("# noqa", [("JWP005", 3)]),
    ],
)
def test_suppression_comments(
    tmp_path: Path, comment: str, expected: list[tuple[str, int]]
) -> None:
    source = f"@pure\ndef f():\n    print('x')  {comment}\n"
    assert codes(run(tmp_path, source)) == expected


def test_project_side_effects_from_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyjustwannabepure]\nside_effects = ["vendor.g*", "*.save", "os.path.join"]\n'
    )
    (tmp_path / "vendor.py").write_text(
        "def get(): ...\nclass Model:\n    def save(self): ...\n    def copy(self): ...\n"
    )
    source = HEADER + dedent("""
    import os
    import vendor

    @pure
    def f(m: vendor.Model):
        vendor.get()
        m.save()
        m.copy()
        return os.path.join("a", "b")
    """)
    path = tmp_path / "main.py"
    path.write_text(source)
    module = parse(source, path)
    assert module is not None
    effects = load_effects(tmp_path)
    issues = check(module, source, path, Resolver(jedi.Project(tmp_path)), effects)
    assert codes(issues) == [("JWP005", 7), ("JWP005", 8), ("JWP005", 10)]
    assert "'vendor.get'" in issues[0].message


@pytest.mark.parametrize(
    "pyproject",
    [
        None,
        "[project]\nname = 'x'\n",
        "[tool.pyjustwannabepure]\nside_effects = 'vendor.*'\n",
        "not toml [",
    ],
)
def test_missing_or_invalid_config_uses_defaults(
    tmp_path: Path, pyproject: str | None
) -> None:
    if pyproject is not None:
        (tmp_path / "pyproject.toml").write_text(pyproject)
    assert load_effects(tmp_path) == Effects()


def test_open_buffer_edits_invalidate_cached_markers(tmp_path: Path) -> None:
    effects = tmp_path / "effects.py"
    effects.write_text(HEADER + "def roll(): ...\n")
    buffers: dict[Path, str] = {}
    resolver = Resolver(jedi.Project(tmp_path), buffers.get)
    source = HEADER + "from effects import roll\n@pure\ndef f():\n    return roll()\n"
    path = tmp_path / "main.py"
    path.write_text(source)
    module = parse(source, path)
    assert module is not None

    def issues() -> list[tuple[str, int]]:
        return codes(check(module, source, path, resolver))

    assert issues() == []
    buffers[effects] = HEADER + "@side_effect\ndef roll(): ...\n"
    assert issues() == [("JWP002", 4)]
    buffers[effects] = HEADER + "def roll(): ...\n"
    assert issues() == []


@pytest.mark.parametrize(
    ("on_disk", "in_buffer", "expected"),
    [
        # Lines added above in the unsaved buffer: Jedi still reports the disk lines.
        (
            "def roll(): ...\nclass Dice:\n    def roll(self): ...\n",
            "# new\n\n@side_effect\ndef roll(): ...\nclass Dice:\n    @side_effect\n    def roll(self): ...\n",
            [("JWP002", 6), ("JWP002", 7)],
        ),
        # Jedi's line for `roll` now holds a different, marked function.
        (
            "def safe(): ...\ndef roll(): ...\nclass Dice:\n    def roll(self): ...\n",
            "def safe(): ...\n@side_effect\ndef other(): ...\ndef roll(): ...\nclass Dice:\n    def roll(self): ...\n",
            [],
        ),
    ],
)
def test_open_buffer_line_drift(
    tmp_path: Path, on_disk: str, in_buffer: str, expected: list[tuple[str, int]]
) -> None:
    effects = tmp_path / "effects.py"
    effects.write_text(HEADER + on_disk)
    resolver = Resolver(jedi.Project(tmp_path), {effects: HEADER + in_buffer}.get)
    source = HEADER + dedent("""
    from effects import roll, Dice

    @pure
    def f(d: Dice):
        roll()
        d.roll()
    """)
    path = tmp_path / "main.py"
    path.write_text(source)
    module = parse(source, path)
    assert module is not None
    assert codes(check(module, source, path, resolver)) == expected


def test_unmarked_module_skips_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files without `@pure` or `@side_effect` references must not pay for a Jedi script or the marker table."""

    def fail(*args: object) -> None:
        raise AssertionError("should not be called")

    monkeypatch.setattr("justwannabepure.lsp.checks.marker_table", fail)
    monkeypatch.setattr(Resolver, "script", fail)
    source = (
        HEADER
        + "import os\n\ndef f():\n    return os.getcwd()\n\n@shell\ndef g(x):\n    if x: pass\n"
    )
    module = parse(source, tmp_path / "m.py")
    assert module is not None
    resolver = Resolver(jedi.Project(tmp_path))
    issues = check(module, source, tmp_path / "m.py", resolver)
    assert [i.code for i in issues] == ["JWP003"]
