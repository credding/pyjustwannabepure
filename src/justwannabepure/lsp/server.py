"""pygls wiring: re-run the inspections on open/change/save and publish diagnostics."""

import ast
import asyncio
import logging
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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
from .interface import interface_digest
from .resolve import Resolver, read_text

DEBOUNCE_SECONDS = 0.3

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Job:
    """A document that is worth checking."""

    module: ast.Module
    source: str
    path: Path


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
        # Interface digests (see `interface.py`), by URI:
        # - `saved`: each open document's file on disk, as Jedi reads it;
        # - `buffers`: each open document's editor text, by version, which the markers come from;
        # - `seen`: what each document's last check saw of the others, as (buffer, saved).
        self.saved: dict[str, str] = {}
        self.buffers: dict[str, tuple[int | None, str]] = {}
        self.seen: dict[str, dict[str, View]] = {}

    @side_effect
    def open_source(self, path: Path) -> str | None:
        doc = self.workspace.text_documents.get(from_fs_path(str(path)) or "")
        return doc.source if doc else None

    @side_effect
    def workspace_root(self) -> Path:
        return Path(self.workspace.root_path or Path.cwd())

    @shell
    def buffer_digests(self) -> dict[str, str]:
        """The interface digest of each open Python document's text, recomputed when its version moves."""
        docs = self.workspace.text_documents
        self.buffers = {
            uri: buffer_digest(self.buffers.get(uri), doc.version, doc.source)
            for uri, doc in docs.items()
            if is_python_path(uri)
        }
        return {uri: digest for uri, (_, digest) in self.buffers.items()}

    @side_effect
    def schedule(self, checks: dict[str, Coroutine[Any, Any, None]]) -> None:
        """Run each URI's check, cancelling the one already pending for it."""
        for uri in checks.keys() & self.pending.keys():
            self.pending.pop(uri).cancel()
        loop = asyncio.get_running_loop()
        self.pending.update({uri: loop.create_task(run) for uri, run in checks.items()})

    @side_effect
    async def on_worker[**P, T](
        self, label: str, fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs
    ) -> T | None:
        """Run `fn` on the check thread; `None`, logged, if it raises."""
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self.executor, lambda: fn(*args, **kwargs)
            )
        except Exception:
            log.exception("%s failed", label)
            return None

    @shell
    async def validate(self, uri: str, delay: float = 0.0) -> None:
        await asyncio.sleep(delay)
        doc = self.workspace.get_text_document(uri)
        match prepare(doc.source, to_fs_path(uri)):
            case None:
                return  # not Python, or mid-edit: keep the last good diagnostics
            case Job(module=module, source=source, path=path):
                self.seen[uri] = views(uri, self.buffer_digests(), self.saved)
                issues = await self.on_worker(
                    f"check of {uri}",
                    check,
                    module,
                    source,
                    path,
                    self.resolver,
                    self.effects,
                )
                latest = self.workspace.get_text_document(uri).version
                match publication(uri, doc, latest, issues):
                    case types.PublishDiagnosticsParams() as params:
                        self.text_document_publish_diagnostics(params)
                    case None:
                        return  # the check failed, or a newer one is scheduled


@pure
def is_python_path(fs_path: str) -> bool:
    return fs_path.endswith((".py", ".pyi"))


# Another document's interface as one check saw it: (editor buffer, file on disk).
type View = tuple[str, str | None]


@pure
def buffer_digest(
    entry: tuple[int | None, str] | None, version: int | None, source: str
) -> tuple[int | None, str]:
    """`entry` if it is for `version`, else a fresh (version, digest)."""
    if entry is not None and entry[0] == version:
        return entry
    return version, interface_digest(source)


@pure
def views(uri: str, buffers: dict[str, str], saved: dict[str, str]) -> dict[str, View]:
    """What a check of `uri` sees of every other open document."""
    return {
        other: (digest, saved.get(other))
        for other, digest in buffers.items()
        if other != uri
    }


@pure
def stale_after_save(
    uri: str, digest: str, open_uris: list[str], seen: dict[str, dict[str, View]]
) -> list[str]:
    """The documents to recheck once `uri` is saved with interface `digest`.

    `uri` itself, and every document whose last check saw a different
    interface for it, in the buffer or on disk, or never saw it at all.
    """
    return [
        other
        for other in open_uris
        if other == uri or seen.get(other, {}).get(uri) != (digest, digest)
    ]


@pure
def prepare(source: str, fs_path: str | None) -> Job | None:
    """The job for a Python file that parses, else `None`."""
    if fs_path is None or not is_python_path(fs_path):
        return None
    path = Path(fs_path)
    module = parse(source, path)
    return Job(module, source, path) if module is not None else None


@pure
def publication(
    uri: str, doc: TextDocument, latest: int | None, issues: list[Issue] | None
) -> types.PublishDiagnosticsParams | None:
    """What to publish for `doc`, unless the check failed or the document has moved on."""
    if issues is None or doc.version != latest:
        return None
    return types.PublishDiagnosticsParams(
        uri=uri,
        version=doc.version,
        diagnostics=[to_diagnostic(doc, issue) for issue in issues],
    )


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
    # From disk: a restored editor buffer may not match the file Jedi reads.
    ls.saved[uri] = interface_digest(read_text(Path(to_fs_path(uri) or "")))
    ls.schedule({uri: ls.validate(uri)})


@server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
@shell
def did_change(ls: PurityServer, params: types.DidChangeTextDocumentParams) -> None:
    uri = params.text_document.uri
    ls.schedule({uri: ls.validate(uri, DEBOUNCE_SECONDS)})


@server.feature(types.TEXT_DOCUMENT_DID_SAVE)
@shell
def did_save(ls: PurityServer, params: types.DidSaveTextDocumentParams) -> None:
    # A marker or a signature changed in one module can change diagnostics in
    # any other, but a body-only edit can't.
    uri = params.text_document.uri
    digest = interface_digest(ls.workspace.get_text_document(uri).source)
    ls.saved[uri] = digest
    stale = stale_after_save(uri, digest, list(ls.workspace.text_documents), ls.seen)
    ls.schedule({other: ls.validate(other) for other in stale})


@server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
@side_effect
def did_close(ls: PurityServer, params: types.DidCloseTextDocumentParams) -> None:
    uri = params.text_document.uri
    ls.saved.pop(uri, None)
    ls.seen.pop(uri, None)
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
