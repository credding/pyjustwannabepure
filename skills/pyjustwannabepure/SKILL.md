---
name: pyjustwannabepure
description: Writes and refactors Python as a functional core and imperative shell using the justwannabepure decorators (@pure, @shell, @side_effect), verified with `pyjustwannabepure check`. Use when writing new Python code in a project that uses pyjustwannabepure, when the user asks to refactor code toward functional core / imperative shell, or when fixing JWP001-JWP011 diagnostics.
---

# pyjustwannabepure

Apply this pattern to code you write and to code the user asks you to
refactor. Don't decorate or restructure other existing code. Calls into
undecorated code are allowed, so it can stay as it is.

## Setup

Skip if `pyjustwannabepure` is already in `pyproject.toml`. Otherwise:

```console
uv add pyjustwannabepure                  # decorators, zero dependencies
uv add --dev 'pyjustwannabepure[lsp]'     # the checker
```

(pip: `pip install pyjustwannabepure` and `pip install 'pyjustwannabepure[lsp]'`.)
The import name differs from the package name:
`from justwannabepure import pure, shell, side_effect`.

## The three roles

- **`@side_effect`**: touches the outside world. I/O, files, network, DB,
  env vars, clock, randomness. Keep these small and dumb: a side effect never
  calls another side effect or a shell.
- **`@shell`**: glue. Calls side effects, hands their results to pure code,
  and passes the output on to more side effects. It makes no decisions.
- **`@pure`**: everything else. Values in, values out. This is where the
  logic, branching and error handling belong.

## New code

Design core-first: write the `@pure` logic and its tests, then thin
`@side_effect` functions for the I/O, then a `@shell` that wires them
together. For outcomes that branch, return a value or a result type and unpack
it in the shell with a type-narrowing `match`, which is the only control flow
a shell may contain.

```python
@side_effect
def read_orders(path: str) -> list[Order]: ...


@pure
def invoice(orders: list[Order], today: date) -> Invoice | Rejected: ...


@shell
def main() -> None:
    match invoice(read_orders(ORDERS), current_date()):
        case Invoice() as inv:
            send(inv)
        case Rejected() as r:
            log_rejection(r)
```

## Refactoring on request

Stay within the code the user named. Where feasible, pin its current behavior
with tests first, then split it:

1. Hoist reads (input, files, `now()`, random draws) up into the shell and pass
   the values in as parameters.
2. Return data that describes what to write or do, and let the shell perform it.
3. Move every `if`/`for`/`try` into a pure function.

Preserve behavior, and don't let the refactor spread into neighboring code.

## Constraints to design around

- `@shell` must not contain `if`, `match` (except type narrowing), `try`,
  `for`, `while`, `raise` or `assert`. Comprehensions and ternaries are fine.
- `@pure` must not mutate its parameters. Return new values instead.
- `@side_effect` must not call other `@side_effect` or `@shell` functions.
  Compose effects in a shell.
- `@side_effect` may contain at most one control-flow statement, counted
  through nesting: the unavoidable `try` around the I/O, or a single `if` on its
  outcome (e.g. `rowcount`). A `raise` directly in an `except` body is free.
  Return raw values and let pure code make further decisions.
- Third-party or project effects you can't decorate go in `pyproject.toml`:

  ```toml
  [tool.pyjustwannabepure]
  side_effects = ["requests.*", "myapp.db.*"]
  ```

## Test the core thoroughly (required)

Every `@pure` function you write or extract needs unit tests. They need no
mocks.

- Cover normal cases, edge cases (empty, zero, boundaries, duplicates, unicode)
  and every branch and error/result variant.
- Prefer `pytest.mark.parametrize` tables of input → expected output.
- If `hypothesis` is already a dependency, add property tests for invariants.
- If a test needs a mock, the function isn't pure: refactor it.

Shells are straight-line and need no unit tests. Test side effects with
integration tests where practical.

## Verify

Copy this checklist and tick it off before calling the work done:

```
- [ ] pyjustwannabepure and its [lsp] extra installed
- [ ] @pure core written or extracted, with tests
- [ ] @side_effect and @shell functions written
- [ ] `uv run pyjustwannabepure check` exits 0
- [ ] project tests and linters pass
```

`check` scans the whole project, or the paths you pass, and prints
`file:line:col: JWPxxx message` for each diagnostic. If it fails with "missing
dependency", the `[lsp]` extra isn't installed.

Run `check` and the tests, fix the diagnostics and failures, and repeat until
both pass:

- Fix diagnostics in code you wrote or were asked to change. Mention any others
  to the user instead of fixing them.
- For the fix for each code, read [references/rules.md](references/rules.md).
- Fix the structure. Don't remove decorators and don't suppress. Use
  `# jwp: ignore[JWPxxx]` only as a last resort, with a comment saying why.
