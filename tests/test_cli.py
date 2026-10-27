import sys
from importlib.metadata import version
from pathlib import Path

import pytest

from justwannabepure.cli import main

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
