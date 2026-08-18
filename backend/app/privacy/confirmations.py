"""Human confirmations that a column holds personal data.

Reuses ConfirmedColumnRole, the table the semantic-role confirm flow already
writes to, rather than adding a second confirmation table. Same reasoning as
everywhere else in this codebase: two stores of "what a person said about a
column" would be two things that must agree.

The role string is namespaced `pii:<kind>` so a privacy confirmation and a
semantic-role confirmation cannot collide on the same (source, role) unique
constraint, and so a reader can tell them apart at a glance.

Scoped to the SOURCE, not the run: that a column holds email addresses is a
property of the file's shape and survives re-ingest, which is what makes the
question get asked once.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ConfirmedColumnRole

#: Prefix marking a ConfirmedColumnRole row as a privacy confirmation.
PII_ROLE_PREFIX = "pii:"


def confirmed_pii_for_source(db: Session, source_id: str) -> dict[str, str]:
    """{column: kind} a person confirmed as personal data for this source."""
    rows = (
        db.query(ConfirmedColumnRole)
        .filter(ConfirmedColumnRole.source_id == source_id, ConfirmedColumnRole.role.startswith(PII_ROLE_PREFIX))
        .all()
    )
    return {row.column_name: row.role[len(PII_ROLE_PREFIX) :] for row in rows}


def pii_role_key(kind: str) -> str:
    return f"{PII_ROLE_PREFIX}{kind}"
