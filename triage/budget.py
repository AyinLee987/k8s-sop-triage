"""Run budget: per-call timeout + total deadline + call caps + no-progress detection.

Every blocking call (kubectl subprocess, HTTP to the model) is given
min(per_call_timeout, remaining time), so the deadline can be overshot by at
most one call's timeout and blocked calls are killed, not waited on.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    """Raised when the run must stop; `reason` is recorded in the trace."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Budget:
    total_seconds: float = 180.0
    per_call_timeout: float = 30.0
    max_tool_calls: int = 25
    max_model_calls: int = 12
    max_duplicate_calls: int = 3
    started: float = field(default_factory=time.monotonic)
    tool_calls: int = 0
    model_calls: int = 0
    duplicate_calls: int = 0

    def remaining(self) -> float:
        return self.total_seconds - (time.monotonic() - self.started)

    def call_timeout(self) -> float:
        remaining = self.remaining()
        if remaining <= 0:
            raise BudgetExceeded("deadline")
        return max(1.0, min(self.per_call_timeout, remaining))

    def charge_tool(self) -> float:
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetExceeded("max_tool_calls")
        timeout = self.call_timeout()
        self.tool_calls += 1
        return timeout

    def charge_model(self) -> float:
        if self.model_calls >= self.max_model_calls:
            raise BudgetExceeded("max_model_calls")
        timeout = self.call_timeout()
        self.model_calls += 1
        return timeout

    def note_duplicate(self) -> None:
        self.duplicate_calls += 1
        if self.duplicate_calls > self.max_duplicate_calls:
            raise BudgetExceeded("no_progress_repeated_calls")

    def child(self, max_tool_calls: int, max_model_calls: int) -> "Budget":
        """A tighter sub-budget sharing the same deadline (used for bounded free investigation)."""
        return Budget(
            total_seconds=self.remaining(),
            per_call_timeout=self.per_call_timeout,
            max_tool_calls=max_tool_calls,
            max_model_calls=max_model_calls,
            max_duplicate_calls=self.max_duplicate_calls,
        )

    def absorb(self, child: "Budget") -> None:
        self.tool_calls += child.tool_calls
        self.model_calls += child.model_calls
        self.duplicate_calls += child.duplicate_calls

    def snapshot(self) -> dict:
        return {
            "wall_seconds": round(time.monotonic() - self.started, 3),
            "tool_calls": self.tool_calls,
            "model_calls": self.model_calls,
            "duplicate_calls": self.duplicate_calls,
        }
