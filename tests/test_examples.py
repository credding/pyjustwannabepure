"""End-to-end snapshot of `check examples`, pinning every rule's output."""

from pathlib import Path

import pytest

from justwannabepure.cli import main

ROOT = Path(__file__).parent.parent


def test_examples_snapshot(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(ROOT / "examples"), "--root", str(ROOT)]) == 1
    expected = (ROOT / "tests" / "examples.expected").read_text()
    assert capsys.readouterr().out == expected
