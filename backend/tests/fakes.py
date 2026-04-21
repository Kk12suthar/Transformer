from __future__ import annotations

from collections.abc import Callable
from typing import Any


def normalize_sql(statement: Any) -> str:
    return " ".join(str(statement).split()).lower()


class FakeResult:
    def __init__(self, rows: list[Any] | None = None):
        self._rows = rows or []

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)

    def mappings(self) -> "FakeResult":
        return self


class RecordingSession:
    def __init__(self, handler: Callable[[str, dict[str, Any]], Any]):
        self._handler = handler
        self.executed: list[dict[str, Any]] = []
        self.commits = 0
        self.closed = False

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> FakeResult:
        bound_params = dict(params or {})
        normalized_sql = normalize_sql(statement)
        self.executed.append({"sql": normalized_sql, "params": bound_params})

        result = self._handler(normalized_sql, bound_params)
        if isinstance(result, FakeResult):
            return result
        if result is None:
            return FakeResult()
        if isinstance(result, list):
            return FakeResult(result)
        return FakeResult([result])

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True
