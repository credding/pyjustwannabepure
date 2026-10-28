"""Well-known effectful builtins and stdlib functions, by Jedi's fully qualified name."""

import logging
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from justwannabepure import pure, shell, side_effect

log = logging.getLogger(__name__)

_EFFECTFUL = frozenset(
    {
        "builtins.print",
        "builtins.input",
        "builtins.open",
        "builtins.exec",
        "builtins.eval",
        "builtins.breakpoint",
        "builtins.__import__",
        "time.time",
        "time.time_ns",
        "time.monotonic",
        "time.monotonic_ns",
        "time.perf_counter",
        "time.perf_counter_ns",
        "time.process_time",
        "time.process_time_ns",
        "time.thread_time",
        "time.sleep",
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.datetime.today",
        "datetime.date.today",
        "uuid.uuid1",
        "uuid.uuid4",
        "sys.exit",
        "glob.glob",
        "glob.iglob",
        # `os.path` is mostly string manipulation, except these.
        "os.path.exists",
        "os.path.lexists",
        "os.path.isfile",
        "os.path.isdir",
        "os.path.islink",
        "os.path.ismount",
        "os.path.getsize",
        "os.path.getmtime",
        "os.path.getatime",
        "os.path.getctime",
        "os.path.realpath",
        "os.path.abspath",
        "os.path.expanduser",
        "os.path.expandvars",
        "os.path.samefile",
    }
)

_EFFECTFUL_PREFIXES = (
    "random.",
    "secrets.",
    "os.",
    "subprocess.",
    "socket.",
    "shutil.",
    "tempfile.",
    "logging.",
    "urllib.request.",
    "http.client.",
    "pathlib.Path.",  # pure path methods resolve to `pathlib.PurePath.*`
)

_PURE = frozenset({"os.fspath", "os.fsencode", "os.fsdecode"})

_PURE_PREFIXES = ("os.path.",)


@pure
def is_effectful(full_name: str) -> bool:
    if full_name in _EFFECTFUL:
        return True
    if full_name in _PURE or full_name.startswith(_PURE_PREFIXES):
        return False
    return full_name.startswith(_EFFECTFUL_PREFIXES)


@dataclass(frozen=True)
class Effects:
    """The builtin table plus project patterns, like `requests.*` or `*.write_text`."""

    patterns: tuple[str, ...] = ()

    @pure
    def __call__(self, full_name: str) -> bool:
        # Project patterns win over the builtin pure exceptions.
        return any(fnmatchcase(full_name, p) for p in self.patterns) or is_effectful(
            full_name
        )


BUILTIN_EFFECTS = Effects()


@dataclass(frozen=True)
class InvalidConfig:
    message: str


@side_effect
def read_pyproject(root: Path) -> dict[str, Any] | None:
    """`root/pyproject.toml` parsed, or `None` if it is missing or unreadable."""
    try:
        with (root / "pyproject.toml").open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return None
    except (OSError, tomllib.TOMLDecodeError):
        log.exception("could not read %s", root / "pyproject.toml")
        return None


@pure
def effects_from_config(config: dict[str, Any] | None) -> Effects | InvalidConfig:
    """Validate `[tool.pyjustwannabepure] side_effects = [...]`."""
    patterns = (
        (config or {}).get("tool", {}).get("pyjustwannabepure", {}).get("side_effects")
    )
    if patterns is None:
        return Effects()
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        return InvalidConfig(
            "tool.pyjustwannabepure.side_effects must be a list of strings"
        )
    return Effects(tuple(patterns))


@side_effect
def warn(message: str) -> None:
    log.warning(message)


@shell
def load_effects(root: Path) -> Effects:
    """Read `[tool.pyjustwannabepure] side_effects = [...]` from `root/pyproject.toml`."""
    match effects_from_config(read_pyproject(root)):
        case Effects() as effects:
            return effects
        case InvalidConfig(message=message):
            warn(message)
            return Effects()
