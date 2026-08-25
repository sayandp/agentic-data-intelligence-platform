"""The registry of domain packs.

A domain pack is a capability-gated analysis for one kind of data: the
Marketing Agent for ad-platform exports, the Agriculture Agent for crop
statistics. They share a shape - a persisted findings row per run, written
even when the run does not qualify, carrying `applicable`, a refusal reason,
`findings` with severities, and `missing_roles`.

THIS EXISTS BECAUSE THE SECOND PACK NEEDED IT. The run comparison and the
Session Summary both read the marketing table by name, which was correct when
there was one pack and became a special case the moment there were two. Adding
`if agriculture: ...` beside `if marketing: ...` in every consumer is how a
domain pack stops being configuration and becomes a fork - so the consumers
now iterate this list instead.

Adding a third pack means appending one entry here, not editing any consumer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models import AgricultureAnalysis, MarketingAnalysis


@dataclass(frozen=True)
class DomainPack:
    """One capability-gated domain analysis."""

    #: Stable key used in comparison section names and fact ids.
    name: str
    #: Human label for output a person reads.
    label: str
    #: The SQLAlchemy model holding this pack's per-run findings row.
    model: Any

    def record_for(self, db, run_id: str):
        return db.query(self.model).filter(self.model.run_id == run_id).one_or_none()

    def payload_for(self, db, run_id: str) -> dict:
        record = self.record_for(db, run_id)
        return (record.findings_json if record is not None else None) or {}


DOMAIN_PACKS: tuple[DomainPack, ...] = (
    DomainPack(name="marketing", label="marketing pack", model=MarketingAnalysis),
    DomainPack(name="agriculture", label="agriculture pack", model=AgricultureAnalysis),
)
