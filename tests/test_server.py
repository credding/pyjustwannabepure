import sys
from pathlib import Path

import pytest_lsp
from lsprotocol import types
from pygls.uris import from_fs_path
from pytest_lsp import ClientServerConfig, LanguageClient

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
