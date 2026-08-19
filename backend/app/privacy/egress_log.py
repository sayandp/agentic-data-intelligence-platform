"""What left this machine, recorded WITHOUT recording what left.

Every outbound model call is a disclosure. The audit trail's job is to answer
"what did this run send to a third party, and what was masked before it went"
- for an operator, an auditor, or the person who has to say what happened
after a provider incident.

NEVER THE PAYLOAD. Logging the rows to prove they were protected would defeat
the point: the log becomes a second copy of exactly the data the redaction
exists to withhold, in a store nobody classified. So a record carries SHAPE
and never CONTENT - column names, a row count, which columns were masked and
how many distinct values in each. Column names are already sent to the model
(it needs the schema) and are not personal data; the values never appear here
in any form, masked or otherwise.

STRUCTURE OVER CHECK. A record is built FROM a `RedactedSample`, and the only
way to obtain one of those is to call `redact_records`. `redacted_columns` and
`masked_value_counts` are copied off the sample rather than re-derived or
passed in by the caller, so a record cannot claim a column was masked unless
the redactor actually masked it. The log reports what happened; it cannot
describe an intention.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import EgressEvent
from app.privacy.redaction import RedactedSample, RedactionPolicy


@dataclass(frozen=True)
class EgressRecord:
    """One outbound model call, as shape only."""

    agent: str
    provider: str
    model: str | None
    policy: str
    #: Columns whose data was included in the payload. Names only.
    columns: list[str]
    #: Rows sent. For the narrative path this is the finding count instead,
    #: named by `unit`, because that path sends findings rather than rows.
    row_count: int
    unit: str = "rows"
    #: {column: kind} the redactor actually masked.
    redacted_columns: dict[str, str] = field(default_factory=dict)
    #: {column: distinct values masked}. A count, never a value.
    masked_value_counts: dict[str, int] = field(default_factory=dict)

    @property
    def was_redacted(self) -> bool:
        return bool(self.redacted_columns)

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "provider": self.provider,
            "model": self.model,
            "policy": self.policy,
            "columns": self.columns,
            "row_count": self.row_count,
            "unit": self.unit,
            "redacted_columns": self.redacted_columns,
            "masked_value_counts": self.masked_value_counts,
        }


def record_for_sample(
    sample: RedactedSample,
    *,
    agent: str,
    provider: str,
    model: str | None,
    policy: RedactionPolicy,
    columns: list[str],
) -> EgressRecord:
    """The record for a path that sends sample ROWS.

    Takes the `RedactedSample` rather than the raw rows on purpose: the
    masking figures are read off the object the redactor produced, so they
    describe what was actually done.
    """
    return EgressRecord(
        agent=agent,
        provider=provider,
        model=model,
        policy=policy.value,
        columns=[str(c) for c in columns],
        row_count=len(sample),
        unit="rows",
        redacted_columns=dict(sample.redacted_columns),
        masked_value_counts=dict(sample.masked_value_counts),
    )


def record_for_findings(
    masked_counts: dict[str, int],
    *,
    agent: str,
    provider: str,
    model: str | None,
    policy: RedactionPolicy,
    columns: list[str],
    finding_count: int,
    redacted_columns: dict[str, str],
    unit: str = "findings",
) -> EgressRecord:
    """The record for the narrative path, which sends findings rather than
    rows. The values it carries are lifted out of real columns, so the same
    accounting applies - only the unit differs.

    `redacted_columns` is the set of columns the policy WOULD mask, and it is
    intersected here with `masked_counts`, which is what the redactor actually
    masked. A column that is redactable but appears in no finding was never
    masked here, and saying it was would be the same overstatement the sample
    path is structurally incapable of making. The two paths hold the same
    invariant: a record names only what the redactor did.

    `unit` is a parameter because this path makes two calls with different
    contents: stage 1 sends findings, stage 2 sends the claims stage 1
    produced. Recording both as "findings" would be a number that does not
    mean what it says."""
    return EgressRecord(
        agent=agent,
        provider=provider,
        model=model,
        policy=policy.value,
        columns=[str(c) for c in columns],
        row_count=finding_count,
        unit=unit,
        redacted_columns={column: kind for column, kind in redacted_columns.items() if column in masked_counts},
        masked_value_counts=dict(masked_counts),
    )


def persist_egress(db: Session, run_id: str, records: list[EgressRecord]) -> list[EgressEvent]:
    """Write records for one run. Callers hold the session; the agents that
    produce records do not, which is why the record is returned rather than
    written where it is made."""
    rows = [
        EgressEvent(
            run_id=run_id,
            agent=record.agent,
            provider=record.provider,
            model=record.model,
            policy=record.policy,
            columns_json=record.columns,
            row_count=record.row_count,
            unit=record.unit,
            redacted_columns_json=record.redacted_columns,
            masked_value_counts_json=record.masked_value_counts,
        )
        for record in records
    ]
    for row in rows:
        db.add(row)
    return rows
