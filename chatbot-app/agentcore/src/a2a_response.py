"""Helpers for assembling streamed A2A responses."""

from typing import Optional


class A2ATextAccumulator:
    """Collect unique text chunks from cumulative A2A task snapshots."""

    def __init__(self):
        self._chunks = []
        self._seen = set()

    def add(self, text: Optional[str]) -> None:
        normalized = text.strip() if text else ""
        if not normalized or normalized in self._seen:
            return
        self._seen.add(normalized)
        self._chunks.append(normalized)

    @property
    def text(self) -> str:
        return "\n\n".join(self._chunks)
