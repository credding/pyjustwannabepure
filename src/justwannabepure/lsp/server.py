"""pygls wiring: re-run the inspections on open/change/save and publish diagnostics."""

import asyncio
import logging
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
from pathlib import Path
from typing import Any

import jedi
from lsprotocol import types
from pygls.lsp.server import LanguageServer
from pygls.uris import from_fs_path, to_fs_path
from pygls.workspace import TextDocument
from pygls.workspace.position_codec import ServerTextPosition, ServerTextRange

from justwannabepure import pure, shell, side_effect

from .checks import Issue, check, parse
from .effects import Effects, load_effects
from .resolve import Resolver

DEBOUNCE_SECONDS = 0.3

log = logging.getLogger(__name__)


class PurityServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__(
            "pyjustwannabepure",
            version("pyjustwannabepure"),
            text_document_sync_kind=types.TextDocumentSyncKind.Full,
        )
        # Replaced with the workspace root's once the client is initialized.
        self.resolver = Resolver(jedi.Project(Path.cwd()), self.open_source)
        self.effects = Effects()
        # Jedi is not thread-safe, so every check runs on this one worker thread.
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jwp-check"
        )
        self.pending: dict[str, asyncio.Task[None]] = {}

    @side_effect
    def open_source(self, path: Path) -> str | None:
        doc = self.workspace.text_documents.get(from_fs_path(str(path)) or "")
        return doc.source if doc else None

    @side_effect
    def workspace_root(self) -> Path:
        return Path(self.workspace.root_path or Path.cwd())

    @side_effect
    def schedule(self, checks: dict[str, Coroutine[Any, Any, None]]) -> None:
        """Run each URI's check, cancelling the one already pending for it."""
        loop = asyncio.get_running_loop()
        for uri, run in checks.items():
            if task := self.pending.pop(uri, None):
                task.cancel()
            self.pending[uri] = loop.create_task(run)

    @side_effect
    async def validate(self, uri: str, delay: float = 0.0) -> None:
        if delay:
            await asyncio.sleep(delay)
        doc = self.workspace.get_text_document(uri)
        source, fs_path = doc.source, to_fs_path(uri)
        if fs_path is None or not is_python_path(fs_path):
            return
        path = Path(fs_path)
        if (module := parse(source, path)) is None:
            return  # keep the last good diagnostics while the user is mid-edit
        loop = asyncio.get_running_loop()
        try:
            issues = await loop.run_in_executor(
                self.executor, check, module, source, path, self.resolver, self.effects
            )
        except Exception:
            log.exception("check failed for %s", uri)
            return
        if doc.version != self.workspace.get_text_document(uri).version:
            return  # stale: a newer check is already scheduled
        self.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(
                uri=uri,
                version=doc.version,
                diagnostics=[to_diagnostic(doc, issue) for issue in issues],
            )
        )


@pure
def is_python_path(fs_path: str) -> bool:
    return fs_path.endswith((".py", ".pyi"))


@pure
def to_diagnostic(doc: TextDocument, issue: Issue) -> types.Diagnostic:
    server_range = ServerTextRange(
        start=ServerTextPosition(issue.start.line, issue.start.character),
        end=ServerTextPosition(issue.end.line, issue.end.character),
    )
    return types.Diagnostic(
        range=doc.position_codec.range_to_client_units(doc.lines, server_range),
        message=issue.message,
        severity=types.DiagnosticSeverity.Error,
        code=issue.code,
        source="justwannabepure",
    )


server = PurityServer()


@server.feature(types.INITIALIZED)
@shell
def initialized(ls: PurityServer, params: types.InitializedParams) -> None:
    root = ls.workspace_root()
    ls.resolver = Resolver(jedi.Project(root), ls.open_source)
    ls.effects = load_effects(root)


@server.feature(types.TEXT_DOCUMENT_DID_OPEN)
@shell
def did_open(ls: PurityServer, params: types.DidOpenTextDocumentParams) -> None:
    uri = params.text_document.uri
    ls.schedule({uri: ls.validate(uri)})


@server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
@shell
def did_change(ls: PurityServer, params: types.DidChangeTextDocumentParams) -> None:
    uri = params.text_document.uri
    ls.schedule({uri: ls.validate(uri, DEBOUNCE_SECONDS)})


@server.feature(types.TEXT_DOCUMENT_DID_SAVE)
@shell
def did_save(ls: PurityServer, params: types.DidSaveTextDocumentParams) -> None:
    # A marker added or removed in one module can change diagnostics in any other.
    ls.schedule({uri: ls.validate(uri) for uri in ls.workspace.text_documents})


@server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
@side_effect
def did_close(ls: PurityServer, params: types.DidCloseTextDocumentParams) -> None:
    uri = params.text_document.uri
    if task := ls.pending.pop(uri, None):
        task.cancel()
    ls.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(uri=uri, diagnostics=[])
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    server.start_io()


if __name__ == "__main__":
    main()
