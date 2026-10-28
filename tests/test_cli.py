import sys
from importlib.metadata import version
from pathlib import Path

import pytest

from justwannabepure.cli import (
    CheckCommand,
    FileReport,
    ServeCommand,
    build_parser,
    display_path,
    error_report,
    exit_code,
    is_skipped,
    issues_report,
    main,
    missing_dependency,
    python_files,
    summary,
    to_command,
)
from justwannabepure.lsp.checks import Issue, Position

HEADER = "from justwannabepure import pure, shell, side_effect\n"


def test_reports_issues_across_modules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "effects.py").write_text(HEADER + "@side_effect\ndef roll(): ...\n")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "app.py").write_text(
        HEADER + "from effects import roll\n@pure\ndef f():\n    return roll()\n"
    )
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text(
        HEADER + "@shell\ndef g(x):\n    if x: pass\n"
    )

    assert main(["check", str(tmp_path), "--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"{Path('pkg', 'app.py')}:5:12: JWP002 @pure function 'f' calls @side_effect function 'roll'"
    ]


def test_clean_exit_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "ok.py").write_text(HEADER + "@pure\ndef f(x):\n    return x + 1\n")
    assert main(["check", str(tmp_path / "ok.py"), "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == ""


def test_syntax_error_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "bad.py").write_text("def (:\n")
    assert main(["check", str(tmp_path), "--root", str(tmp_path)]) == 1
    assert "syntax error" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("command", "blocked"), [("check", "jedi"), ("server", "pygls")]
)
def test_missing_lsp_extra(
    command: str,
    blocked: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in list(sys.modules):
        if name.partition(".")[0] == blocked or name.startswith("justwannabepure.lsp"):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(
        sys.modules, blocked, None
    )  # makes `import <blocked>` raise ModuleNotFoundError

    with pytest.raises(SystemExit) as exit_info:
        main([command, str(tmp_path)] if command == "check" else [command])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert f"missing dependency '{blocked}'" in err
    assert "pyjustwannabepure[lsp]" in err


def test_reads_side_effects_from_pyproject(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyjustwannabepure]\nside_effects = ["vendor.*"]\n'
    )
    (tmp_path / "vendor.py").write_text("def get(): ...\n")
    (tmp_path / "app.py").write_text(
        HEADER + "import vendor\n@pure\ndef f():\n    return vendor.get()\n"
    )
    assert main(["check", str(tmp_path / "app.py"), "--root", str(tmp_path)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "app.py:5:12: JWP005 @pure function 'f' calls effectful 'vendor.get'"
    ]


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert (
        capsys.readouterr().out == f"pyjustwannabepure {version('pyjustwannabepure')}\n"
    )


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        ((), False),
        (("src", "pkg"), False),
        ((".venv",), True),
        (("src", ".git", "x"), True),
        (("__pycache__",), True),
        (("a", "node_modules"), True),
        (("builder",), False),
        (("dist",), True),
    ],
)
def test_is_skipped(parts: tuple[str, ...], expected: bool) -> None:
    assert is_skipped(parts) is expected


@pytest.mark.parametrize(
    ("root", "candidates", "expected"),
    [
        (
            Path("/r/a.py"),
            [Path("/r/a.py")],
            [Path("/r/a.py")],
        ),  # a file given directly
        (Path("/r"), [], []),
        (
            Path("/r"),
            [
                Path("/r/a.py"),
                Path("/r/.venv/b.py"),
                Path("/r/p/build/c.py"),
                Path("/r/p/d.py"),
            ],
            [Path("/r/a.py"), Path("/r/p/d.py")],
        ),
        # only directories below the root are skipped
        (
            Path("/r/.hidden/build"),
            [Path("/r/.hidden/build/a.py")],
            [Path("/r/.hidden/build/a.py")],
        ),
        (
            Path("/r"),
            [Path("/r/.a.py")],
            [Path("/r/.a.py")],
        ),  # a dotted file name is not a directory
    ],
)
def test_python_files(root: Path, candidates: list[Path], expected: list[Path]) -> None:
    assert python_files(root, candidates) == expected


@pytest.mark.parametrize(
    ("path", "root", "expected"),
    [
        (Path("/r/a/b.py"), Path("/r"), Path("a/b.py")),
        (Path("/elsewhere/b.py"), Path("/r"), Path("/elsewhere/b.py")),
        (Path("/r"), Path("/r"), Path(".")),
    ],
)
def test_display_path(path: Path, root: Path, expected: Path) -> None:
    assert display_path(path, root) == expected


def test_issues_report_is_one_based() -> None:
    issue = Issue("JWP002", "msg", Position(0, 0), Position(0, 3))
    shown = Path("a.py")
    assert issues_report(shown, [issue]) == FileReport(("a.py:1:1: JWP002 msg",), ())
    assert issues_report(shown, []) == FileReport((), ())


def test_error_report() -> None:
    assert error_report(Path("a.py"), "boom") == FileReport((), ("a.py: error: boom",))


CLEAN = FileReport((), ())
ONE = FileReport(("x",), ())
TWO = FileReport(("x", "y"), ())
BROKEN = FileReport((), ("e",))


@pytest.mark.parametrize(
    ("reports", "message", "code"),
    [
        ([], None, 0),
        ([CLEAN, CLEAN], None, 0),
        ([ONE], "Found 1 issue.", 1),
        ([ONE, CLEAN, TWO], "Found 3 issues.", 1),
        ([BROKEN], None, 1),  # errors fail the run but are not counted as issues
        ([BROKEN, ONE], "Found 1 issue.", 1),
    ],
)
def test_summary_and_exit_code(
    reports: list[FileReport], message: str | None, code: int
) -> None:
    assert summary(reports) == message
    assert exit_code(reports) == code


@pytest.mark.parametrize(
    ("module", "package"),
    [
        ("jedi", "jedi"),
        ("pygls.lsp.server", "pygls"),
        ("attrs", "attrs"),
        ("requests", None),
        (None, None),
        ("", None),
    ],
)
def test_missing_dependency(module: str | None, package: str | None) -> None:
    message = missing_dependency("check", module)
    if package is None:
        assert message is None
    else:
        assert message is not None
        assert message.startswith(
            f"pyjustwannabepure check: missing dependency '{package}'."
        )


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["server"], ServeCommand()),
        (["server", "--stdio"], ServeCommand()),
        (["check"], CheckCommand([Path(".")], Path("."))),
        (
            ["check", "a", "b", "--root", "r"],
            CheckCommand([Path("a"), Path("b")], Path("r")),
        ),
    ],
)
def test_to_command(argv: list[str], expected: CheckCommand | ServeCommand) -> None:
    assert to_command(build_parser("0").parse_args(argv)) == expected
