# justwannabepure

Marker decorators for functional-core / imperative-shell code, and a language
server that checks them:

| Code     | Rule                                                                                                                |
| -------- | ------------------------------------------------------------------------------------------------------------------- |
| `JWP001` | `@pure` functions must not be `async`                                                                               |
| `JWP002` | `@pure` functions must not call `@side_effect` functions                                                            |
| `JWP003` | `@shell` functions must not contain `if`/`match`/`try`/`for`/`while` (a `match` that only narrows types is allowed) |
| `JWP004` | a function cannot be both `@pure` and `@side_effect`                                                                |

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
examples/demo.py:21:1: JWP001 @pure function 'fetch' must not be async
```
