"""Marker decorators for functional-core / imperative-shell code.

They are identity functions at runtime; the ``pyjustwannabepure server`` language
server reads them statically and reports violations.
"""

from collections.abc import Callable

__all__ = ["pure", "shell", "side_effect"]


def pure[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Mark ``fn`` as pure: not async, and never calls a ``@side_effect`` function."""
    return fn


def side_effect[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Mark ``fn`` as having non-obvious side effects (I/O, randomness, clocks...)."""
    return fn


def shell[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Mark ``fn`` as an imperative shell: straight-line code with no control flow."""
    return fn
