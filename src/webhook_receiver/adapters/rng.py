"""Injectable randomness (SPEC §6.4).

Randomness is an input, exactly as time is. A retry schedule that uses `random`
directly cannot be tested -- you can assert that the delay lands in a range, but
not that the *schedule* is right, and a bug that only shows up as "the delays are
slightly wrong" is a bug nobody finds.

So the RNG is passed in, and `JITTER_SEED` makes it reproducible. The seed is a
test and debugging tool: setting it in production would give every worker in the
fleet the *same* jitter, which defeats the entire point of jitter (see
`domain/backoff.py`) -- so the setting's docstring says so, and nothing sets it
by default.
"""

from __future__ import annotations

import random
from typing import Protocol


class Rng(Protocol):
    """The only source of randomness in the application."""

    def uniform(self, low: float, high: float) -> float: ...


class SystemRng:
    """Real entropy. Used everywhere except tests.

    Not `random.uniform` at module level: a module-level RNG is process-global
    shared state, so a test that seeds it changes the behaviour of every other
    test in the same process, in an order-dependent way.
    """

    def __init__(self) -> None:
        self._random = random.Random()  # noqa: S311 (jitter, not cryptography)

    def uniform(self, low: float, high: float) -> float:
        return self._random.uniform(low, high)


class SeededRng:
    """Deterministic randomness, for tests and for reproducing a bad schedule."""

    def __init__(self, seed: int) -> None:
        self._random = random.Random(seed)  # noqa: S311 (jitter, not cryptography)

    def uniform(self, low: float, high: float) -> float:
        return self._random.uniform(low, high)


def create_rng(seed: int | None) -> Rng:
    """`SeededRng` when a seed is configured, `SystemRng` otherwise."""
    return SystemRng() if seed is None else SeededRng(seed)
