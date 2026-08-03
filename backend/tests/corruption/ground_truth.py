"""Ground truth produced alongside every corruption injection.

This is what turns the corruption harness into an evaluation strategy rather
than just a source of broken data: each injector states, up front, what a
correct pipeline should do with the mess it just made.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CorruptionGroundTruth:
    corruption_type: str
    target_columns: list[str]
    parameters: dict
    expected_detection: str
    expected_risk_level: str  # "low" (auto-fixable) | "high" (must escalate)
    seed: int
    notes: str = field(default="")

    def __post_init__(self) -> None:
        if self.expected_risk_level not in ("low", "high"):
            raise ValueError(f"expected_risk_level must be 'low' or 'high', got {self.expected_risk_level!r}")
