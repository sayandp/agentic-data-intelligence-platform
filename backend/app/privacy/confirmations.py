"""Human decisions about whether a column holds personal data.

Reuses ConfirmedColumnRole, the table the semantic-role confirm flow already
writes to, rather than adding a second confirmation table. Same reasoning as
everywhere else in this codebase: two stores of "what a person said about a
column" would be two things that must agree.

The role string is namespaced `pii:<column>` so a privacy decision and a
semantic-role confirmation cannot collide on the same (source, role) unique
constraint, and so a reader can tell them apart at a glance. Keying on the
COLUMN (not the kind) is what makes the unique constraint say the right
thing: one decision per column, and deciding again replaces the previous
answer. Keying on the kind would have silently allowed only one email column
per source.

Two decisions are possible, and they are mirrors of each other:

    "this IS personal"      -> decision is a PIIKind value; the column is
                               masked on every path, including permissive
    "this is NOT personal"  -> decision is NOT_PERSONAL; the column is masked
                               on no path, which is the only way to switch
                               default-deny off for a candidate

Scoped to the SOURCE, not the run: that a column holds email addresses is a
property of the file's shape and survives re-ingest, which is what makes the
question get asked once.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import ConfirmedColumnRole

#: Prefix marking a ConfirmedColumnRole row as a privacy decision.
PII_ROLE_PREFIX = "pii:"

#: The decision meaning "a person looked at this column and it is not
#: personal data". Distinct from having no row at all, which means nobody has
#: looked - and under default-deny those two states behave differently.
NOT_PERSONAL = "not_personal"


@dataclass(frozen=True)
class PrivacyDecision:
    """One person's answer about one column."""

    column: str
    decision: str  # a PIIKind value, or NOT_PERSONAL
    confirmed_by: str | None = None
    confirmed_at: str | None = None

    @property
    def is_personal(self) -> bool:
        return self.decision != NOT_PERSONAL


def pii_role_key(column: str) -> str:
    return f"{PII_ROLE_PREFIX}{column}"


def privacy_decisions_for_source(db: Session, source_id: str) -> dict[str, PrivacyDecision]:
    """{column: PrivacyDecision} for this source.

    A row whose column no longer exists in the frame is returned anyway and
    simply never matches - the same tolerance the semantic-role flow has for
    a source that changed shape. A stored answer about a vanished column is
    not applicable, not an error.
    """
    rows = (
        db.query(ConfirmedColumnRole)
        .filter(
            ConfirmedColumnRole.source_id == source_id,
            ConfirmedColumnRole.role.startswith(PII_ROLE_PREFIX),
        )
        .all()
    )
    decisions: dict[str, PrivacyDecision] = {}
    for row in rows:
        # `decision` is nullable for rows written before the column existed.
        # Such a row can only have come from the confirm-as-personal flow,
        # which is the safe reading: treat it as personal rather than as
        # permission to stop masking.
        decisions[row.column_name] = PrivacyDecision(
            column=row.column_name,
            decision=row.decision or "free_text",
            confirmed_by=row.confirmed_by,
            confirmed_at=row.confirmed_at.isoformat() if row.confirmed_at else None,
        )
    return decisions


def record_privacy_decision(
    db: Session,
    source_id: str,
    column: str,
    decision: str,
    confirmed_by: str | None = None,
) -> ConfirmedColumnRole:
    """Record (or replace) the decision for one column of one source."""
    role = pii_role_key(column)
    existing = (
        db.query(ConfirmedColumnRole)
        .filter(ConfirmedColumnRole.source_id == source_id, ConfirmedColumnRole.role == role)
        .one_or_none()
    )
    if existing is not None:
        existing.column_name = column
        existing.decision = decision
        existing.confirmed_by = confirmed_by
        return existing

    row = ConfirmedColumnRole(
        source_id=source_id,
        role=role,
        column_name=column,
        decision=decision,
        confirmed_by=confirmed_by,
    )
    db.add(row)
    return row


def clear_privacy_decision(db: Session, source_id: str, column: str) -> bool:
    """Withdraw a decision, returning it to whatever detection says.

    Present because deciding must not be a one-way door - and specifically
    because marking a column not-personal is the one action here that REMOVES
    protection. An action that cannot be undone is a bad place for that.
    """
    existing = (
        db.query(ConfirmedColumnRole)
        .filter(ConfirmedColumnRole.source_id == source_id, ConfirmedColumnRole.role == pii_role_key(column))
        .one_or_none()
    )
    if existing is None:
        return False
    db.delete(existing)
    return True


def confirmed_pii_for_source(db: Session, source_id: str) -> dict[str, PrivacyDecision]:
    """Alias kept for the call site in the graph, which wants every decision -
    both directions - not only the confirmations."""
    return privacy_decisions_for_source(db, source_id)
