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
