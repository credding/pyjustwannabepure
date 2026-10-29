import asyncio
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
import pytest_lsp
from lsprotocol import types
from pygls.uris import from_fs_path
from pygls.workspace import TextDocument
from pytest_lsp import ClientServerConfig, LanguageClient

from justwannabepure.lsp.checks import Issue, Position
from justwannabepure.lsp.interface import interface_digest
from justwannabepure.lsp.server import (
    Job,
    View,
    buffer_digest,
    is_python_path,
    prepare,
    publication,
    stale_after_save,
    to_diagnostic,
    views,
)

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


CALLEE = """\
from justwannabepure import pure, side_effect

@side_effect
def roll() -> int:
    return 4
"""

CALLER = """\
from justwannabepure import pure
from b import roll

@pure
def score() -> int:
    return roll()
"""


@pytest_lsp.fixture(
    config=ClientServerConfig(
        server_command=[sys.executable, "-m", "justwannabepure.cli", "server"]
    ),
)
async def project(lsp_client: LanguageClient, tmp_path: Path):
    (tmp_path / "a.py").write_text(CALLER)
    (tmp_path / "b.py").write_text(CALLEE)
    await lsp_client.initialize_session(
        types.InitializeParams(
            capabilities=types.ClientCapabilities(),
            root_uri=from_fs_path(str(tmp_path)),
        )
    )
    yield
    await lsp_client.shutdown_session()


async def wait_until(client: LanguageClient, done: Callable[[], bool]) -> None:
    async with asyncio.timeout(30):
        while not done():
            await client.wait_for_notification(types.TEXT_DOCUMENT_PUBLISH_DIAGNOSTICS)


def open_document(client: LanguageClient, path: Path) -> str:
    uri = from_fs_path(str(path))
    assert uri is not None
    client.text_document_did_open(
        types.DidOpenTextDocumentParams(
            text_document=types.TextDocumentItem(
                uri=uri, language_id="python", version=1, text=path.read_text()
            )
        )
    )
    return uri


async def test_saving_a_marker_change_rechecks_callers(
    project: LanguageClient, tmp_path: Path
) -> None:
    client = project
    caller = open_document(client, tmp_path / "a.py")
    callee = open_document(client, tmp_path / "b.py")

    def caller_codes() -> list[str | int | None] | None:
        diagnostics = client.diagnostics.get(caller)
        return None if diagnostics is None else [d.code for d in diagnostics]

    await wait_until(client, lambda: caller_codes() == ["JWP002"])

    fixed = CALLEE.replace("@side_effect", "@pure")
    (tmp_path / "b.py").write_text(fixed)
    client.text_document_did_change(
        types.DidChangeTextDocumentParams(
            text_document=types.VersionedTextDocumentIdentifier(uri=callee, version=2),
            content_changes=[types.TextDocumentContentChangeWholeDocument(text=fixed)],
        )
    )
    client.text_document_did_save(
        types.DidSaveTextDocumentParams(
            text_document=types.TextDocumentIdentifier(uri=callee)
        )
    )
    await wait_until(client, lambda: caller_codes() == [])


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


@pytest.mark.parametrize(
    ("source", "fs_path"),
    [
        ("x = 1\n", None),
        ("x = 1\n", "/a/b.txt"),
        ("def (:\n", "/a/b.py"),
    ],
)
def test_prepare_skips(source: str, fs_path: str | None) -> None:
    assert prepare(source, fs_path) is None


def test_prepare_parses() -> None:
    job = prepare("x = 1\n", "/a/b.pyi")
    assert isinstance(job, Job)
    assert (job.source, job.path) == ("x = 1\n", Path("/a/b.pyi"))
    assert len(job.module.body) == 1


ISSUE = Issue("JWP002", "msg", Position(0, 0), Position(0, 1))


@pytest.mark.parametrize(
    ("latest", "issues"),
    [
        (3, None),  # the check failed
        (4, [ISSUE]),  # stale
        (None, []),
    ],
)
def test_publication_skips(latest: int | None, issues: list[Issue] | None) -> None:
    doc = TextDocument("file:///a.py", "x\n", version=3)
    assert publication("file:///a.py", doc, latest, issues) is None


@pytest.mark.parametrize("issues", [[], [ISSUE, ISSUE]])
def test_publication(issues: list[Issue]) -> None:
    doc = TextDocument("file:///a.py", "x\n", version=3)
    params = publication("file:///a.py", doc, 3, issues)
    assert params is not None
    assert (params.uri, params.version) == ("file:///a.py", 3)
    assert params.diagnostics == [to_diagnostic(doc, issue) for issue in issues]


def test_buffer_digest() -> None:
    entry = (3, "cached")
    assert buffer_digest(entry, 3, "x = 1\n") is entry
    assert buffer_digest(entry, 4, "x = 1\n") == (4, interface_digest("x = 1\n"))
    assert buffer_digest(None, None, "x = 1\n") == (None, interface_digest("x = 1\n"))


def test_views() -> None:
    buffers = {"a": "A", "b": "B2", "c": "C"}
    assert views("a", buffers, {"b": "B1", "a": "A"}) == {
        "b": ("B2", "B1"),
        "c": ("C", None),
    }


SEEN: dict[str, dict[str, View]] = {
    "a": {"b": ("B", "B")},  # saw b as saved
    "c": {"b": ("B2", "B")},  # saw b's unsaved edit
    "d": {"e": ("E", "E")},  # never saw b
}


@pytest.mark.parametrize(
    ("digest", "expected"),
    [
        ("B", ["b", "c", "d", "x"]),  # unchanged interface: only stale views
        ("B2", ["a", "b", "c", "d", "x"]),  # c saw B2 in the buffer, but B on disk
    ],
)
def test_stale_after_save(digest: str, expected: list[str]) -> None:
    open_uris = ["a", "b", "c", "d", "x"]  # x was never checked
    assert stale_after_save("b", digest, open_uris, SEEN) == expected
