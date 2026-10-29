import pytest

from justwannabepure.lsp.interface import UNPARSABLE, interface_digest

BASE = """\
from justwannabepure import pure, side_effect

@side_effect
def roll(sides: int) -> int:
    return 4

def helper(x):
    return x + 1

class Dice:
    sides = 6

    def __init__(self, n: int) -> None:
        self.n = n
        total = n

    @side_effect
    def roll(self) -> int:
        return self.n

    @pure
    def outer(self) -> int:
        @side_effect
        def inner() -> int: ...
        return 1
"""


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("    return 4\n", "    print(1)\n    return 5\n"),  # annotated body
        ("        total = n\n", "        total = n * 2\n"),  # not a self attribute
        ("        return self.n\n", "        return 7\n"),  # annotated method body
        # A marker inside an annotated body: nothing else can reach `inner`.
        ("        @side_effect\n        def inner", "        @pure\n        def inner"),
        ("from justwannabepure", "# a comment\n\nfrom justwannabepure"),  # line shift
    ],
)
def test_same_interface(old: str, new: str) -> None:
    assert old in BASE
    assert interface_digest(BASE.replace(old, new)) == interface_digest(BASE)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("@side_effect\ndef roll", "@pure\ndef roll"),  # marker
        ("@side_effect\ndef roll", "def roll"),  # marker removed
        ("    @side_effect\n    def roll", "    @pure\n    def roll"),  # method marker
        ("def roll(sides: int)", "def roll(sides: str)"),  # parameter type
        ("def roll(sides: int) -> int", "def roll(sides: int) -> str"),  # return type
        ("def roll(sides", "async def roll(sides"),
        ("    return x + 1\n", "    return str(x)\n"),  # unannotated body
        ("        self.n = n\n", "        self.n = str(n)\n"),  # self attribute
        ("        self.n = n\n", "        self.n: int = n\n"),
        ("    sides = 6\n", "    sides = 8\n"),  # class attribute
        ("class Dice:", "class Dice(int):"),  # bases
        ("from justwannabepure import pure, side_effect", "import os"),
    ],
)
def test_changed_interface(old: str, new: str) -> None:
    assert old in BASE
    assert interface_digest(BASE.replace(old, new)) != interface_digest(BASE)


def test_unparsable() -> None:
    assert interface_digest("def (:\n") == UNPARSABLE
    assert interface_digest("") != UNPARSABLE
