"""Command line: `pyjustwannabepure check [PATH ...]` and `pyjustwannabepure server`.

Both commands need the `lsp` extra (jedi, pygls); the decorators themselves do not.
"""

import argparse
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

SKIP_DIRS = {"__pycache__", "node_modules", "site-packages", "build", "dist"}
# Top-level modules installed by the `tools` extra, including transitive ones.
TOOLS_MODULES = {"jedi", "parso", "pygls", "lsprotocol", "cattrs", "attrs"}


def iter_python_files(paths: Sequence[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_file():
            yield path
            continue
        for candidate in sorted(path.rglob("*.py")):
            relative = candidate.relative_to(path).parts[:-1]
            if not any(part.startswith(".") or part in SKIP_DIRS for part in relative):
                yield candidate


def check_paths(paths: Sequence[Path], root: Path) -> int:
    import jedi

    from .lsp.checks import check, parse
    from .lsp.resolve import Resolver

    resolver = Resolver(jedi.Project(root))
    found = errors = 0
    for path in iter_python_files(paths):
        shown = path.relative_to(root) if path.is_relative_to(root) else path
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"{shown}: error: {e}", file=sys.stderr)
            errors += 1
            continue
        if (module := parse(source, path)) is None:
            print(f"{shown}: error: syntax error, skipped", file=sys.stderr)
            errors += 1
            continue
        for issue in check(module, source, path, resolver):
            print(
                f"{shown}:{issue.start.line + 1}:{issue.start.character + 1}: {issue.code} {issue.message}"
            )
            found += 1

    if found:
        print(f"Found {found} issue{'s' * (found != 1)}.", file=sys.stderr)
    return 1 if found or errors else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pyjustwannabepure")
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
    args = parser.parse_args(argv)

    try:
        if args.command == "server":
            from .lsp.server import main as serve

            serve()
            return 0

        root = args.root.resolve()
        missing = [p for p in args.paths if not p.exists()]
        if missing:
            parser.error(f"no such file or directory: {', '.join(map(str, missing))}")
        return check_paths([p.resolve() for p in args.paths], root)
    except ModuleNotFoundError as e:
        package = (e.name or "").partition(".")[0]
        if package not in TOOLS_MODULES:
            raise
        parser.exit(
            2,
            f"pyjustwannabepure {args.command}: missing dependency '{package}'.\n"
            f"Install the tools extra: pip install 'justwannabepure[lsp]'\n",
        )


if __name__ == "__main__":
    sys.exit(main())
