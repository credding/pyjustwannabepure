# justwannabepure

Marker decorators for functional-core / imperative-shell code, and a language
server that checks them:

| Code | Rule |
|---|---|
| `JWP001` | `@pure` functions must not be `async` |
| `JWP002` | `@pure` functions must not call `@side_effect` functions |
| `JWP003` | `@shell` functions must not contain `if`/`match`/`try`/`for`/`while` |
| `JWP004` | a function cannot be both `@pure` and `@side_effect` |

```python
from justwannabepure import pure, shell, side_effect
```

Run the server with `uv run justwannabepure-lsp` (stdio).

Or run it as a one-shot static analysis (exits 1 if anything is found):

```console
$ uv run pyjustwannabepure check [PATH ...] [--root PROJECT_ROOT]
examples/demo.py:21:1: JWP001 @pure function 'fetch' must not be async
```
