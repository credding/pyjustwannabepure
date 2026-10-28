from typing import Any

import pytest

from justwannabepure.lsp.effects import (
    Effects,
    InvalidConfig,
    effects_from_config,
    is_effectful,
)


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        ("builtins.print", True),
        ("builtins.len", False),
        ("time.time", True),
        ("time.strftime", False),
        ("random.randint", True),
        ("os.getcwd", True),
        ("os.fspath", False),
        ("os.path.join", False),
        ("os.path.exists", True),
        ("pathlib.Path.read_text", True),
        ("pathlib.PurePath.with_suffix", False),
        ("logging.Logger.info", True),
        ("randomize.thing", False),
        ("", False),
    ],
)
def test_is_effectful(full_name: str, expected: bool) -> None:
    assert is_effectful(full_name) is expected


@pytest.mark.parametrize(
    ("patterns", "full_name", "expected"),
    [
        ((), "requests.get", False),
        (("requests.*",), "requests.get", True),
        (("*.write_text",), "pkg.File.write_text", True),
        (
            ("os.path.join",),
            "os.path.join",
            True,
        ),  # project patterns beat pure exceptions
        (("requests.*",), "builtins.print", True),
        (("requests.get",), "requests.post", False),
    ],
)
def test_project_patterns(
    patterns: tuple[str, ...], full_name: str, expected: bool
) -> None:
    assert Effects(patterns)(full_name) is expected


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (None, Effects()),
        ({}, Effects()),
        ({"tool": {}}, Effects()),
        ({"tool": {"pyjustwannabepure": {}}}, Effects()),
        ({"tool": {"pyjustwannabepure": {"side_effects": []}}}, Effects()),
        (
            {"tool": {"pyjustwannabepure": {"side_effects": ["a.*", "b"]}}},
            Effects(("a.*", "b")),
        ),
        (
            {"tool": {"pyjustwannabepure": {"side_effects": "a.*"}}},
            InvalidConfig(
                "tool.pyjustwannabepure.side_effects must be a list of strings"
            ),
        ),
        (
            {"tool": {"pyjustwannabepure": {"side_effects": ["a", 1]}}},
            InvalidConfig(
                "tool.pyjustwannabepure.side_effects must be a list of strings"
            ),
        ),
    ],
)
def test_effects_from_config(
    config: dict[str, Any] | None, expected: Effects | InvalidConfig
) -> None:
    assert effects_from_config(config) == expected
