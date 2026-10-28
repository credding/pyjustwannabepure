"""Command line: `pyjustwannabepure check [PATH ...]` and `pyjustwannabepure server`.

Both commands need the `lsp` extra (jedi, pygls); the decorators themselves do not.
"""

import argparse
import importlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, TextIO

from justwannabepure import pure, shell, side_effect

if TYPE_CHECKING:
    from .lsp.checks import Issue
    from .lsp.effects import Effects
    from .lsp.resolve import Resolver

SKIP_DIRS = {"__pycache__", "node_modules", "site-packages", "build", "dist"}
# Top-level modules installed by the `lsp` extra, including transitive ones.
LSP_MODULES = {"jedi", "parso", "pygls", "lsprotocol", "cattrs", "attrs"}


@dataclass(frozen=True)
class CheckCommand:
    name: ClassVar[str] = "check"
    # Imported up front, so a missing `lsp` extra is reported before any work starts.
    modules: ClassVar[tuple[str, ...]] = (
        "justwannabepure.lsp.checks",
        "justwannabepure.lsp.effects",
        "justwannabepure.lsp.resolve",
    )
    paths: list[Path]
    root: Path


@dataclass(frozen=True)
class ServeCommand:
    name: ClassVar[str] = "server"
    modules: ClassVar[tuple[str, ...]] = ("justwannabepure.lsp.server",)


@dataclass(frozen=True)
class ReadError:
    message: str


@dataclass(frozen=True)
class FileReport:
    issues: tuple[str, ...]  # for stdout
    errors: tuple[str, ...]  # for stderr


@pure
def is_skipped(relative_dirs: Sequence[str]) -> bool:
    return any(part.startswith(".") or part in SKIP_DIRS for part in relative_dirs)


@pure
def python_files(root: Path, candidates: Sequence[Path]) -> list[Path]:
    """The candidates found under `root`, minus those in skipped directories."""
    return [c for c in candidates if not is_skipped(c.relative_to(root).parts[:-1])]


@pure
def display_path(path: Path, root: Path) -> Path:
    return path.relative_to(root) if path.is_relative_to(root) else path


@pure
def issues_report(shown: Path, issues: "list[Issue]") -> FileReport:
    return FileReport(
        tuple(
            f"{shown}:{issue.start.line + 1}:{issue.start.character + 1}: {issue.code} {issue.message}"
            for issue in issues
        ),
        (),
    )


@pure
def error_report(shown: Path, message: str) -> FileReport:
    return FileReport((), (f"{shown}: error: {message}",))


@pure
def summary(reports: Sequence[FileReport]) -> str | None:
    found = sum(len(report.issues) for report in reports)
    return f"Found {found} issue{'s' * (found != 1)}." if found else None


@pure
def exit_code(reports: Sequence[FileReport]) -> int:
    return 1 if any(report.issues or report.errors for report in reports) else 0


@pure
def missing_dependency(command: str, module: str | None) -> str | None:
    """The message for a `ModuleNotFoundError` from the `lsp` extra, else `None`."""
    package = (module or "").partition(".")[0]
    if package not in LSP_MODULES:
        return None
    return (
        f"pyjustwannabepure {command}: missing dependency '{package}'.\n"
        f"Install the lsp extra: pip install 'pyjustwannabepure[lsp]'\n"
    )


@pure
def build_parser(prog_version: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pyjustwannabepure")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {prog_version}"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check_cmd = commands.add_parser("check", help="report @pure / @shell violations")
    check_cmd.add_argument(
        "paths", nargs="*", type=Path, default=[Path(".")], help="files or directories"
    )
    check_cmd.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="project root used to resolve imports (default: .)",
    )
    server_cmd = commands.add_parser("server", help="start the language server")
    # Many editor clients pass --stdio unconditionally; it is the only transport, so accept it as a no-op.
    server_cmd.add_argument(
        "--stdio", action="store_true", help="communicate over stdin/stdout (default)"
    )
    return parser


@pure
def to_command(args: argparse.Namespace) -> CheckCommand | ServeCommand:
    if args.command == "server":
        return ServeCommand()
    return CheckCommand(args.paths, args.root)


@side_effect
def candidate_files(path: Path) -> list[Path]:
    return [path] if path.is_file() else sorted(path.rglob("*.py"))


@side_effect
def read_source(path: Path) -> str | ReadError:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return ReadError(str(e))


@side_effect
def print_lines(lines: Sequence[str], file: TextIO) -> None:
    for line in lines:
        print(line, file=file)


@side_effect
def emit_summary(message: str | None) -> None:
    if message:
        print(message, file=sys.stderr)


@side_effect
def missing_module(modules: Sequence[str]) -> str | None:
    """The name of the module whose import failed, if any."""
    try:
        list(map(importlib.import_module, modules))
    except ModuleNotFoundError as e:
        return e.name
    return None


@side_effect
def require_paths(parser: argparse.ArgumentParser, paths: Sequence[Path]) -> None:
    missing = [p for p in paths if not p.exists()]
    if missing:
        parser.error(f"no such file or directory: {', '.join(map(str, missing))}")


@shell
def emit(report: FileReport) -> None:
    print_lines(report.issues, sys.stdout)
    print_lines(report.errors, sys.stderr)


@shell
def check_source(
    source: str, path: Path, shown: Path, resolver: "Resolver", effects: "Effects"
) -> FileReport:
    from .lsp.checks import check, parse

    match parse(source, path):
        case None:
            return error_report(shown, "syntax error, skipped")
        case module:
            return issues_report(shown, check(module, source, path, resolver, effects))


@shell
def check_file(
    path: Path, root: Path, resolver: "Resolver", effects: "Effects"
) -> FileReport:
    shown = display_path(path, root)
    match read_source(path):
        case ReadError(message=message):
            report = error_report(shown, message)
        case source:
            report = check_source(source, path, shown, resolver, effects)
    emit(report)
    return report


@shell
def check_paths(paths: Sequence[Path], root: Path) -> int:
    import jedi

    from .lsp.effects import load_effects
    from .lsp.resolve import Resolver

    resolver = Resolver(jedi.Project(root))
    effects = load_effects(root)
    reports = [
        check_file(file, root, resolver, effects)
        for path in paths
        for file in python_files(path, candidate_files(path))
    ]
    emit_summary(summary(reports))
    return exit_code(reports)


@side_effect
def serve() -> int:
    from .lsp.server import main as serve

    serve()
    return 0


@shell
def run(parser: argparse.ArgumentParser, command: CheckCommand | ServeCommand) -> int:
    match command:
        case ServeCommand():
            return serve()
        case CheckCommand(paths=paths, root=root):
            require_paths(parser, paths)
            return check_paths([p.resolve() for p in paths], root.resolve())


@shell
def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser(version("pyjustwannabepure"))
    command = to_command(parser.parse_args(argv))
    # Any other failed import is left to raise again from `run`.
    match missing_dependency(command.name, missing_module(command.modules)):
        case str() as message:
            parser.exit(2, message)
        case None:
            return run(parser, command)


if __name__ == "__main__":
    sys.exit(main())
