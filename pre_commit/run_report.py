from __future__ import annotations

import dataclasses
import json
from typing import Any


@dataclasses.dataclass
class HookResult:
    hook_id: str
    hook_name: str
    status: str  # "passed" | "failed" | "skipped"
    files: tuple[str, ...]
    duration_s: float | None
    return_code: int
    files_modified: bool
    diff: str | None  # working-tree diff after this hook (when modified)

    def to_dict(self) -> dict[str, Any]:
        return {
            'hook_id': self.hook_id,
            'hook_name': self.hook_name,
            'status': self.status,
            'files': list(self.files),
            'duration_s': self.duration_s,
            'return_code': self.return_code,
            'files_modified': self.files_modified,
            'diff': self.diff,
        }


@dataclasses.dataclass
class RunReport:
    results: list[HookResult]
    retval: int

    def to_dict(self) -> dict[str, Any]:
        return {
            'retval': self.retval,
            'results': [r.to_dict() for r in self.results],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def write_json(self, path: str) -> None:
        with open(path, 'w') as f:
            f.write(self.to_json())
            f.write('\n')


class RunResult(int):
    """int subclass that carries an optional RunReport.

    All existing call-sites (``return run(...)``, ``retv | run(...)``)
    keep working because RunResult *is* an int.  Library callers can
    access ``result.report`` for the structured data.
    """

    report: RunReport | None

    def __new__(
            cls,
            retval: int,
            report: RunReport | None = None,
    ) -> RunResult:
        obj = super().__new__(cls, retval)
        obj.report = report
        return obj
