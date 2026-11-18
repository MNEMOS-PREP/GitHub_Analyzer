"""Numbered evidence for the PR agent: every tool result the model can see is stored here as E1, E2, ...

A finding must cite an id from this log, and the verifier checks the finding against the stored result.
Ids are never reused, and callers only ever get copies, so stored evidence cannot be edited afterwards.
"""

from __future__ import annotations

import copy
import json


def _key(tool: str, args: dict) -> str:
    return tool + "\0" + json.dumps(args, sort_keys=True, default=str)


class EvidenceLog:
    def __init__(self) -> None:
        self._items: dict[str, dict] = {}
        self._calls: dict[str, str] = {}

    def add(self, tool: str, args: dict, result: str) -> str:
        eid = f"E{len(self._items) + 1}"
        self._items[eid] = {"tool": tool, "args": copy.deepcopy(args), "result": result}
        self._calls.setdefault(_key(tool, args), eid)
        return eid

    def get(self, eid: str) -> dict | None:
        item = self._items.get(eid)
        return copy.deepcopy(item) if item is not None else None

    def lookup_call(self, tool: str, args: dict) -> str | None:
        return self._calls.get(_key(tool, args))

    def items(self) -> dict[str, dict]:
        return copy.deepcopy(self._items)

    def __len__(self) -> int:
        return len(self._items)
