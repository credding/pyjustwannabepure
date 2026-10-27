"""pygls wiring: re-run the inspections on open/change/save and publish diagnostics."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jedi
from lsprotocol import types
from pygls.lsp.server import LanguageServer
from pygls.uris import from_fs_path, to_fs_path
from pygls.workspace import TextDocument
from pygls.workspace.position_codec import ServerTextPosition, ServerTextRange

from .checks import Issue, check, parse
from .resolve import Resolver

DEBOUNCE_SECONDS = 0.3

log = logging.getLogger(__name__)


class PurityServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__("justwannabepure", "0.1.0", text_document_sync_kind=types.TextDocumentSyncKind.Full)
        self.resolver: Resolver | None = None
        # Jedi is not thread-safe, so every check runs on this one worker thread.
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jwp-check")
        self.pending: dict[str, asyncio.Task[None]] = {}

    def open_source(self, path: Path) -> str | None:
        doc = self.workspace.text_documents.get(from_fs_path(str(path)) or "")
        return doc.source if doc else None

    def get_resolver(self) -> Resolver:
        if self.resolver is None:
            root = self.workspace.root_path or str(Path.cwd())
            self.resolver = Resolver(jedi.Project(root), self.open_source)
        return self.resolver

    def schedule(self, uri: str, delay: float = 0.0) -> None:
        if task := self.pending.pop(uri, None):
            task.cancel()
        self.pending[uri] = asyncio.get_running_loop().create_task(self.validate(uri, delay))

    async def validate(self, uri: str, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        doc = self.workspace.get_text_document(uri)
        source, fs_path = doc.source, to_fs_path(uri)
        if fs_path is None or not fs_path.endswith((".py", ".pyi")):
            return
        path = Path(fs_path)
        if (module := parse(source, path)) is None:
            return  # keep the last good diagnostics while the user is mid-edit
        resolver = self.get_resolver()
        loop = asyncio.get_running_loop()
        try:
            issues = await loop.run_in_executor(self.executor, check, module, source, path, resolver)
        except Exception:
            log.exception("check failed for %s", uri)
            return
        if doc.version != self.workspace.get_text_document(uri).version:
            return  # stale: a newer check is already scheduled
        self.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(
                uri=uri, version=doc.version, diagnostics=[to_diagnostic(doc, issue) for issue in issues]
            )
        )


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


@server.feature(types.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: PurityServer, params: types.DidOpenTextDocumentParams) -> None:
    ls.schedule(params.text_document.uri)


@server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: PurityServer, params: types.DidChangeTextDocumentParams) -> None:
    ls.schedule(params.text_document.uri, DEBOUNCE_SECONDS)


@server.feature(types.TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: PurityServer, params: types.DidSaveTextDocumentParams) -> None:
    # A marker added or removed in one module can change diagnostics in any other.
    for uri in list(ls.workspace.text_documents):
        ls.schedule(uri)


@server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls: PurityServer, params: types.DidCloseTextDocumentParams) -> None:
    uri = params.text_document.uri
    if task := ls.pending.pop(uri, None):
        task.cancel()
    ls.text_document_publish_diagnostics(types.PublishDiagnosticsParams(uri=uri, diagnostics=[]))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    server.start_io()


if __name__ == "__main__":
    main()
