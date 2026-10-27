# pyjustwannabepure

Marker decorators for functional-core / imperative-shell code, and a language
server that checks them:

| Code     | Rule                                                                                                                                                 |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `JWP001` | `@pure` functions must not be `async`                                                                                                                |
| `JWP002` | `@pure` functions must not call `@side_effect` functions                                                                                             |
| `JWP003` | `@shell` functions must not contain `if`/`match`/`try`/`for`/`while`/`raise`/`assert` (a `match` that only narrows types is allowed)                 |
| `JWP004` | a function cannot be both `@pure` and `@side_effect`                                                                                                 |
| `JWP005` | `@pure` functions must not call or pass around known-effectful builtins and stdlib functions (`print`, `open`, `time.time`, `random.*`, `os.*`, ...) |
| `JWP006` | `@pure` functions must not use `global`, or `nonlocal` outside a nested function                                                                     |
| `JWP007` | `@pure` functions must not mutate their parameters (`x.a = ...`, `x[k] = ...`, `del x[k]`, `x.append(...)`, ...)                                     |
| `JWP008` | `@pure` functions must not call `@shell` functions                                                                                                   |

`JWP002` also fires when a `@pure` function passes a `@side_effect` function
around without calling it, like `map(roll, xs)`.

Silence a line with `# jwp: ignore`, or only some codes with
`# jwp: ignore[JWP005, JWP007]`.

Declare your project's own effectful functions for `JWP005` in `pyproject.toml`
at the project root, as glob patterns over fully qualified names:

```toml
[tool.pyjustwannabepure]
side_effects = ["requests.*", "myapp.db.*", "*.write_csv"]
```

```python
from justwannabepure import pure, shell, side_effect
```

The decorators have no dependencies. The `check` and `server` commands need the
`lsp` extra:

```console
$ pip install 'justwannabepure[lsp]'
```

Run the language server (stdio) with `uv run pyjustwannabepure server`. A Zed
extension lives in [`editors/zed`](editors/zed).

Or run it as a one-shot static analysis (exits 1 if anything is found):

```console
$ uv run pyjustwannabepure check [PATH ...] [--root PROJECT_ROOT]
examples/demo.py:20:1: JWP001 @pure function 'fetch' must not be async
```
