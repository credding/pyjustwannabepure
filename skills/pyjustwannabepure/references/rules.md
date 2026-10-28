# JWP rules and fixes

| Code   | Violation                                                         | Fix                                                                                                                                                       |
| ------ | ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| JWP001 | `@pure` function is `async`                                       | Awaiting is I/O. Do the awaiting in a `@shell`/`@side_effect` and pass the results into a sync `@pure` function.                                          |
| JWP002 | `@pure` calls or passes around a `@side_effect` function          | Call the effect in the shell and pass its result in as a parameter. For repeated effects (e.g. many random draws), pass in the pre-computed values.        |
| JWP003 | `@shell` contains `if`/`match`/`try`/`for`/`while`/`raise`/`assert` | Move the decision into a `@pure` function that returns the value to use, or a result type the shell unpacks with a type-narrowing `match`. Replace loops with comprehensions or move them into pure code. Turn `try` around an effect into a `@side_effect` that returns a result value. |
| JWP004 | function is both `@pure` and `@side_effect`                       | Pick one. If it does both, split it into an effect and a pure computation.                                                                                |
| JWP005 | `@pure` calls an effectful builtin/stdlib/project function        | Same as JWP002. For `print`/logging, return the message and let the shell output it. For `datetime.now()`/`random`, take the value as a parameter.          |
| JWP006 | `@pure` uses `global`, or `nonlocal` outside a nested function    | Pass the state in and return the new state.                                                                                                               |
| JWP007 | `@pure` mutates a parameter                                       | Build and return a new value (`[*xs, x]`, `{**d, k: v}`, `dataclasses.replace(obj, ...)`). Copying first (`xs = list(xs)`) and mutating the copy is fine. |
| JWP008 | `@pure` calls a `@shell` function                                 | The dependency is backwards: shells call pure code, never the reverse. Extract the logic the pure function needs into another `@pure` function.             |

If a diagnostic is a false positive (the call really is pure), suppress it on
that line with `# jwp: ignore[JWPxxx]` and add a comment saying why.
