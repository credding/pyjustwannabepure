"""Spike checks for the mypy plugin: `uv run pytest spikes/mypy`."""

import re
import shutil
import textwrap
from pathlib import Path

import pytest
from mypy import api

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = Path(__file__).with_name("plugin.py")
ERROR = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+): error: (?P<msg>.*)  \[(?P<code>[\w-]+)\]$"
)


@pytest.fixture(scope="session")
def cache_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # Shared so typeshed is only checked once per session.
    return tmp_path_factory.mktemp("mypy_cache")


@pytest.fixture
def mypy(tmp_path: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mypy.ini").write_text(f"[mypy]\nplugins = {PLUGIN}\n")

    def run(
        *files: str, flags: tuple[str, ...] = ()
    ) -> list[tuple[str, int, str, str]]:
        out, err, _ = api.run([*files, "--cache-dir", str(cache_dir), *flags])
        assert "INTERNAL ERROR" not in out + err, out + err
        return [
            (m["file"], int(m["line"]), m["code"], m["msg"])
            for line in out.splitlines()
            if (m := ERROR.match(line))
        ]

    return run


def write(path: Path, source: str) -> None:
    path.write_text(textwrap.dedent(source).lstrip())


def codes(errors: list[tuple[str, int, str, str]]) -> list[tuple[int, str]]:
    """Only the plugin's own errors."""
    return sorted((line, code) for _, line, code, _ in errors if code.startswith("JWP"))


def test_examples_match_jedi_checker(tmp_path: Path, mypy) -> None:
    for f in (ROOT / "examples").glob("*.py"):
        shutil.copy(f, tmp_path)
    expected = sorted(
        (int(line.split(":")[1]), line.split()[1])
        for line in (ROOT / "tests/examples.expected").read_text().splitlines()
    )
    errors = mypy("demo.py", "effects.py")
    assert codes(errors) == expected
    assert {msg for *_, msg in errors} == {
        line.split(" ", 2)[2]
        for line in (ROOT / "tests/examples.expected").read_text().splitlines()
    }


def test_marker_change_rechecks_dependents(tmp_path: Path, mypy) -> None:
    """Q1 (incremental mode): flipping a callee's marker updates its callers."""
    write(
        tmp_path / "b.py",
        """
        from justwannabepure import pure, side_effect

        @side_effect
        def roll() -> int:
            return 4
        """,
    )
    write(
        tmp_path / "a.py",
        """
        from justwannabepure import pure
        from b import roll

        @pure
        def score() -> int:
            return roll()
        """,
    )
    assert codes(mypy("a.py", "b.py")) == [(6, "JWP002")]
    b = tmp_path / "b.py"
    b.write_text(b.read_text().replace("@side_effect", "@pure"))
    assert codes(mypy("a.py", "b.py")) == []
    b.write_text(b.read_text().replace("@pure", "@side_effect"))
    assert codes(mypy("a.py", "b.py")) == [(6, "JWP002")]
    # Q5: an unchanged rerun is served from the cache and still reports it.
    assert codes(mypy("a.py", "b.py")) == [(6, "JWP002")]


UNANNOTATED = """
from justwannabepure import pure, shell

@pure
def f():
    print(1)

@shell
def g():
    for x in []:
        pass
"""


def test_unannotated_body_rules_still_run(tmp_path: Path, mypy) -> None:
    """Q2/Q3: the decorator hook fires for an unannotated def, call hooks don't."""
    write(tmp_path / "m.py", UNANNOTATED)
    assert codes(mypy("m.py")) == [(9, "JWP003")]


def test_unannotated_call_rules_need_check_untyped_defs(tmp_path: Path, mypy) -> None:
    write(tmp_path / "m.py", UNANNOTATED)
    assert codes(mypy("m.py", flags=("--check-untyped-defs",))) == [
        (5, "JWP005"),
        (9, "JWP003"),
    ]


def test_caller_scope(tmp_path: Path, mypy) -> None:
    """Q4: methods, nested defs and lambdas are attributed to the marked function."""
    write(
        tmp_path / "m.py",
        """
        from justwannabepure import pure, side_effect

        @side_effect
        def roll() -> int:
            return 4

        class C:
            @pure
            def method(self) -> int:
                return roll()

        @pure
        def outer() -> int:
            def inner() -> int:
                return roll()
            f = lambda: roll()
            return inner() + f()

        def unmarked() -> int:
            return roll()
        """,
    )
    errors = mypy("m.py")
    assert codes(errors) == [(10, "JWP002"), (15, "JWP002"), (16, "JWP002")]
    assert "'method'" in errors[0][3] and "'outer'" in errors[1][3]


def test_effect_fullnames(tmp_path: Path, mypy) -> None:
    """Q7: mypy's fullnames match the Jedi-based effects table."""
    write(
        tmp_path / "m.py",
        """
        import os
        from pathlib import Path

        from justwannabepure import pure

        @pure
        def f(p: Path) -> str:
            print(1)
            os.remove("x")
            p.with_suffix(".txt")
            os.path.join("a", "b")
            return p.read_text()
        """,
    )
    errors = mypy("m.py")
    assert codes(errors) == [(8, "JWP005"), (9, "JWP005"), (12, "JWP005")]
    assert [msg.split("'")[3] for *_, msg in sorted(errors)] == [
        "builtins.print",
        "os.remove",
        "pathlib.Path.read_text",
    ]


def test_inherited_method(tmp_path: Path, mypy) -> None:
    write(
        tmp_path / "m.py",
        """
        from justwannabepure import pure, side_effect

        class Base:
            @side_effect
            def roll(self) -> int:
                return 4

        class Sub(Base): ...

        @pure
        def f(s: Sub) -> int:
            return s.roll()
        """,
    )
    assert codes(mypy("m.py")) == [(12, "JWP002")]


def test_value_references(tmp_path: Path, mypy) -> None:
    write(
        tmp_path / "m.py",
        """
        import random

        from justwannabepure import pure, side_effect

        @side_effect
        def roll(n: int) -> int:
            return n

        @pure
        def f(xs: list[int]) -> object:
            ys = list(map(roll, xs))
            return random.choice, map(print, ys)  # the tuple isn't a call: not seen
        """,
    )
    assert codes(mypy("m.py")) == [(11, "JWP002"), (12, "JWP005")]


@pytest.mark.xfail(strict=True, reason="container elements aren't visited")
def test_value_reference_in_container(tmp_path: Path, mypy) -> None:
    write(
        tmp_path / "m.py",
        """
        from justwannabepure import pure, side_effect

        @side_effect
        def roll() -> int:
            return 4

        @pure
        def f() -> object:
            return [roll]
        """,
    )
    assert codes(mypy("m.py")) == [(9, "JWP002")]


def test_suppression(tmp_path: Path, mypy) -> None:
    write(
        tmp_path / "m.py",
        """
        from justwannabepure import pure, shell, side_effect

        @side_effect
        def roll() -> int:
            return 4

        @pure
        def f() -> int:
            return roll()  # type: ignore[JWP002]

        @shell
        def g() -> None:
            assert roll()  # jwp: ignore[JWP003]
        """,
    )
    assert codes(mypy("m.py")) == []
