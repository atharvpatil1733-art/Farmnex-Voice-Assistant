"""Per-turn latency (SPEC §6 budget). Stages are stored in `voice.messages.latency_ms` and sent in
`turn.end`, so a slow turn can be traced to its stage before anything is changed."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager


class TurnTimer:
    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._start = clock()
        self._values: dict[str, int] = {}

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        """Record how long the block took, in ms, under `name`."""
        started = self._clock()
        try:
            yield
        finally:
            self._values[name] = int((self._clock() - started) * 1000)

    def mark(self, name: str) -> None:
        """Record ms elapsed since the turn started (first time only)."""
        self._values.setdefault(name, int((self._clock() - self._start) * 1000))

    def as_dict(self) -> dict[str, int]:
        return dict(self._values)
