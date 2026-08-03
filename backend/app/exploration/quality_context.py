"""Part 4: builds DataQualityContext from a run's ValidationEvent rows.
Carries the resolution history forward; never re-derives or re-interprets
it - that boundary belongs to the ValidationEngine, not here."""

from __future__ import annotations

from app.exploration.findings import DataQualityContext, ResolutionKind
from app.models import ValidationEvent


def build_data_quality_context(events: list[ValidationEvent], baseline_is_provisional: bool) -> DataQualityContext:
    resolution_counts: dict[ResolutionKind, int] = {}
    for event in events:
        if not event.action_taken:
            continue
        try:
            kind = ResolutionKind(event.action_taken)
        except ValueError:
            continue  # not a resolution outcome this context tracks (e.g. None handled above)
        resolution_counts[kind] = resolution_counts.get(kind, 0) + 1

    return DataQualityContext(
        total_events=len(events),
        resolution_counts=resolution_counts,
        active_baseline_provisional=baseline_is_provisional,
    )
