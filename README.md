# pyjustwannabepure

**Functional core, imperative shell — checked by your editor.**

`pyjustwannabepure` gives you three tiny decorators to label what your functions
are allowed to do, and a language server (plus a CLI) that tells you when the
code doesn't match the label.

- **`@pure`** — the functional core. Takes values, returns values. No I/O, no
  clocks, no randomness, no mutating its arguments.
- **`@side_effect`** — the edges. Anything that touches the outside world.
- **`@shell`** — the glue. Straight-line code that wires side effects to pure
  logic, with no branching of its own.

The decorators are identity functions with zero dependencies, so they cost
nothing at runtime. All the checking happens statically.

## Installation

Add the decorators to your project:

```console
$ uv add pyjustwannabepure
```

The checker (the `check` and `server` commands) needs the `lsp` extra. Add it
as a dev dependency so it lives in your project's venv:

```console
$ uv add --dev 'pyjustwannabepure[lsp]'
```

Or install it globally as a tool:

```console
$ uv tool install 'pyjustwannabepure[lsp]'
```

## Agent skill

[`skills/pyjustwannabepure`](skills/pyjustwannabepure) is an
[Agent Skill](https://agentskills.io) that teaches coding agents to write
functional core / imperative shell code, test the core, and run
`pyjustwannabepure check`. To apply it to all new code, mention it in your
`AGENTS.md` or `CLAUDE.md`.

Install it for any agent that supports skills:

```console
$ npx skills add credding/pyjustwannabepure
```

Or copy the folder into your agent's skills directory (for example
`.claude/skills/` or `.agents/skills/`).

## A quick example

```python
import random

from justwannabepure import pure, shell, side_effect


@side_effect
def roll_die(sides: int) -> int:
    return random.randint(1, sides)


@pure
def summarize(rolls: list[int]) -> str:
    return f"rolled {rolls}: total {sum(rolls)}, best {max(rolls)}"


@shell
def main() -> None:
    rolls = [roll_die(6) for _ in range(3)]
    print(summarize(rolls))
```

- `roll_die` is random, so it's marked `@side_effect`.
- `summarize` only computes a string from its input, so it's `@pure` — easy to
  test, no mocks needed.
- `main` is the `@shell`: it fetches the effectful data, hands it to the pure
  core, and prints the result. Comprehensions and ternaries are fine; real
  control flow is not.

Now suppose `summarize` starts cutting corners:

```python
@pure
def summarize(rolls: list[int]) -> str:
    rolls.append(roll_die(6))  # sneak in one more roll
    print("debug:", rolls)
    return f"rolled {rolls}: total {sum(rolls)}, best {max(rolls)}"
```

```console
$ uv run pyjustwannabepure check
dice.py:13:5: JWP007 @pure function 'summarize' mutates its parameter 'rolls'
dice.py:13:18: JWP002 @pure function 'summarize' calls @side_effect function 'roll_die'
dice.py:14:5: JWP005 @pure function 'summarize' calls effectful 'builtins.print'
Found 3 issues.
```

## The decorators

### `@pure`

A pure function's output depends only on its input. A `@pure` function must
not:

- be `async`
- call (or pass around, like `map(roll_die, xs)`) a `@side_effect` function
- call a `@shell` function
- call known-effectful builtins and stdlib functions — `print`, `open`,
  `input`, `time.time`, `datetime.now`, `random.*`, `os.*`, `subprocess.*`,
  `pathlib.Path` I/O methods, and so on
- use `global`, or `nonlocal` outside a nested function
- mutate its parameters (`x.a = ...`, `x[k] = ...`, `del x[k]`,
  `x.append(...)`, ...)

Calling unannotated functions is allowed, so you can adopt the decorators
gradually.

### `@side_effect`

Marks a function that does something non-obvious: I/O, randomness, reading
the clock, talking to the network. It works on methods too — `d.roll()` is
caught when `roll` is a `@side_effect` method.

A side effect is a leaf: it must not call (or pass around) another
`@side_effect` function or a `@shell` function. Composing effects is the
shell's job, so a side effect does exactly one thing.

A function can't be both `@pure` and `@side_effect`.

### `@shell`

The imperative shell coordinates effects and pure logic, and should be so
boring it barely needs tests. A `@shell` function must not contain `if`,
`match`, `try`, `for`, `while`, `raise`, or `assert`. Push decisions into the
pure core instead.

One exception: a `match` that only narrows types is allowed, so you can unpack
a result returned by the core:

```python
@shell
def main() -> None:
    match parse(read_input()):
        case Ok(value):
            save(value)
        case Err(error):
            report(error)
```

## Rules

| Code     | Rule                                                                                                  |
| -------- | ----------------------------------------------------------------------------------------------------- |
| `JWP001` | `@pure` functions must not be `async`                                                                 |
| `JWP002` | `@pure` functions must not call or pass around `@side_effect` functions                               |
| `JWP003` | `@shell` functions must not contain `if`/`match`/`try`/`for`/`while`/`raise`/`assert`                 |
| `JWP004` | a function cannot be both `@pure` and `@side_effect`                                                  |
| `JWP005` | `@pure` functions must not call or pass around known-effectful builtins, stdlib and project functions |
| `JWP006` | `@pure` functions must not use `global`, or `nonlocal` outside a nested function                      |
| `JWP007` | `@pure` functions must not mutate their parameters                                                    |
| `JWP008` | `@pure` functions must not call `@shell` functions                                                    |
| `JWP009` | `@side_effect` functions must not call or pass around other `@side_effect` functions                  |
| `JWP010` | `@side_effect` functions must not call or pass around `@shell` functions                              |

### Ignoring a line

Silence every rule on a line with `# jwp: ignore`, or only some of them:

```python
print(rolls)  # jwp: ignore[JWP005]
```

## Configuration

You can't decorate code you don't own, so declare your project's other
effectful functions in `pyproject.toml` at the project root. They're glob
patterns over fully qualified names, and `@pure` functions calling them are
reported as `JWP005`:

```toml
[tool.pyjustwannabepure]
side_effects = ["requests.*", "myapp.db.*", "*.write_csv"]
```

## Usage

### In your editor

Run the language server over stdio:

```console
$ uv run pyjustwannabepure server
```

Point any LSP-capable editor at that command. For Zed, there's an extension in
[`editors/zed`](editors/zed).

### On the command line

`check` scans files or directories (default: the current directory) and exits
with status 1 if it finds anything, which makes it easy to drop into CI or a
pre-commit hook:

```console
$ uv run pyjustwannabepure check [PATH ...] [--root PROJECT_ROOT]
```

`--root` is the project root used to resolve imports (default: `.`).

Try it on the bundled examples:

```console
$ uv run pyjustwannabepure check examples
```
