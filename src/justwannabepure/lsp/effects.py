"""Well-known effectful builtins and stdlib functions, by Jedi's fully qualified name."""

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


def is_effectful(full_name: str) -> bool:
    if full_name in _EFFECTFUL:
        return True
    if full_name in _PURE or full_name.startswith(_PURE_PREFIXES):
        return False
    return full_name.startswith(_EFFECTFUL_PREFIXES)
