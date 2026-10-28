import sys
from pathlib import Path

import pytest
import pytest_lsp
from lsprotocol import types
from pygls.uris import from_fs_path
from pygls.workspace import TextDocument
from pytest_lsp import ClientServerConfig, LanguageClient

from justwannabepure.lsp.checks import Issue, Position
from justwannabepure.lsp.server import is_python_path, to_diagnostic

ROOT = Path(__file__).parent

SOURCE = """\
from justwannabepure import pure, shell, side_effect

@side_effect
def roll(): ...

@pure
async def f():
    return roll()

@shell
def g(x):
    if x:
        pass
"""


@pytest_lsp.fixture(
    config=ClientServerConfig(
        server_command=[sys.executable, "-m", "justwannabepure.cli", "server"]
    ),
)
async def client(lsp_client: LanguageClient):
    await lsp_client.initialize_session(
        types.InitializeParams(
            capabilities=types.ClientCapabilities(), root_uri=from_fs_path(str(ROOT))
        )
    )
    yield
    await lsp_client.shutdown_session()


async def test_publishes_diagnostics(client: LanguageClient) -> None:
    uri = from_fs_path(str(ROOT / "demo.py"))
    assert uri is not None
    client.text_document_did_open(
        types.DidOpenTextDocumentParams(
            text_document=types.TextDocumentItem(
                uri=uri, language_id="python", version=1, text=SOURCE
            )
        )
    )
    await client.wait_for_notification(types.TEXT_DOCUMENT_PUBLISH_DIAGNOSTICS)

    diagnostics = client.diagnostics[uri]
    assert [(d.code, d.range.start.line) for d in diagnostics] == [
        ("JWP001", 6),
        ("JWP002", 7),
        ("JWP003", 11),
    ]
    assert all(d.source == "justwannabepure" for d in diagnostics)

    # Fixing the code clears the diagnostics.
    client.text_document_did_change(
        types.DidChangeTextDocumentParams(
            text_document=types.VersionedTextDocumentIdentifier(uri=uri, version=2),
            content_changes=[
                types.TextDocumentContentChangeWholeDocument(text="x = 1\n")
            ],
        )
    )
    await client.wait_for_notification(types.TEXT_DOCUMENT_PUBLISH_DIAGNOSTICS)
    assert not client.diagnostics[uri]


@pytest.mark.parametrize(
    ("fs_path", "expected"),
    [
        ("/a/b.py", True),
        ("/a/b.pyi", True),
        ("/a/b.pyc", False),
        ("/a/b.txt", False),
        ("/a/py", False),
    ],
)
def test_is_python_path(fs_path: str, expected: bool) -> None:
    assert is_python_path(fs_path) is expected


def test_to_diagnostic_converts_to_utf16() -> None:
    doc = TextDocument("file:///a.py", "s = '😀'; roll()\n")
    # Code points 10..16 (`roll()`) are UTF-16 units 11..17: the emoji is a surrogate pair.
    issue = Issue("JWP002", "msg", Position(0, 10), Position(0, 16))
    diagnostic = to_diagnostic(doc, issue)
    assert diagnostic.range == types.Range(
        start=types.Position(line=0, character=11),
        end=types.Position(line=0, character=17),
    )
    assert (diagnostic.code, diagnostic.message, diagnostic.source) == (
        "JWP002",
        "msg",
        "justwannabepure",
    )
    assert diagnostic.severity == types.DiagnosticSeverity.Error
