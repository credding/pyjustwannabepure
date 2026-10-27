import ast

import pytest

from justwannabepure.lsp.markers import Marker, collect_aliases, marked_functions


def markers(source: str) -> dict[str, set[Marker]]:
    module = ast.parse(source)
    return {fn.name: m for fn, m in marked_functions(module, collect_aliases(module))}


@pytest.mark.parametrize(
    ("imports", "decorator"),
    [
        ("from justwannabepure import pure", "pure"),
        ("from justwannabepure import pure as p", "p"),
        ("import justwannabepure", "justwannabepure.pure"),
        ("import justwannabepure as j", "j.pure"),
    ],
)
def test_import_forms(imports: str, decorator: str) -> None:
    assert markers(f"{imports}\n@{decorator}\ndef f(): ...") == {"f": {Marker.PURE}}


def test_all_markers_and_async() -> None:
    source = """
from justwannabepure import pure, side_effect, shell
@side_effect
def a(): ...
@shell
async def b(): ...
class C:
    @pure
    def c(self): ...
"""
    assert markers(source) == {"a": {Marker.SIDE_EFFECT}, "b": {Marker.SHELL}, "c": {Marker.PURE}}


@pytest.mark.parametrize(
    "source",
    [
        "from functools import cache as pure\n@pure\ndef f(): ...",
        "@pure\ndef f(): ...",
        "import other as justwannabepure_not\n@justwannabepure_not.pure\ndef f(): ...",
        "from justwannabepure import pure as p\n@pure\ndef f(): ...",  # only the alias counts
        "from .justwannabepure import pure\n@pure\ndef f(): ...",  # relative import is a different module
    ],
)
def test_unrelated_decorators_ignored(source: str) -> None:
    assert markers(source) == {}
