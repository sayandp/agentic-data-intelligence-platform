"""The egress boundary. ONE redaction function, and a type that proves it ran.

THE STORED FRAME IS NEVER MUTATED. Redaction applies only to the rows leaving
for a third-party model - the same detect-before-transform separation the
value basis and the semantic roles already follow. What is on disk and what
every deterministic agent computes on is the original data; only the outbound
copy is masked.

STRUCTURE OVER CHECK. `RedactedSample` is the only type the agents accept for
sample rows, and the only way to build one is `redact_records()`. A call site
cannot pass a raw `list[dict]` to an agent - that is a type error, not a code
review note. This is the design principle applied to the sharpest edge in the
system: forgetting to redact is not a mistake anyone can make here, because
the shape of the argument will not allow it.

CONSISTENT TOKENS. A redacted value becomes a stable token per column -
<EMAIL_1>, <EMAIL_2> - so the model can still see that two rows share a value,
which is exactly what it needs to spot a duplicate or a join key, without
seeing the value. Tokens are scoped to one redaction call: the same email in
two different outbound payloads need not, and should not, carry the same
token.

COLUMN NAMES ARE NOT REDACTED. The model needs the schema to do its job, and a
column name is not personal data. `email` staying `email` is the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from enum import Enum

from app.privacy.classification import PIIKind, PrivacyClassification


class RedactionPolicy(str, Enum):
    """How much to mask on a given outbound path.

    STRICT is DEFAULT-DENY: verified PII *and* unconfirmed candidates
    (person names, addresses, free text) are masked until a human marks the
    column not personal. Justified by measurement - diagnosis accuracy was
    identical with redaction on and off (3/3 same cause, same confidence), so
    over-redacting costs that path essentially nothing.

    PERMISSIVE masks only what is VERIFIED personal - pattern-matched or
    human-confirmed - and lets candidates through. Justified by the opposite
    measurement on the narrative path: with a free-text column redacted, the
    report went from 5 claims to 4 and from 574 to 275 characters, losing the
    value-concentration finding outright. A report that is accurate and
    useless is its own kind of failure.

    The split is per PATH, never global. Relaxing everywhere to protect
    narrative quality would have traded a real leak for a readability gain,
    which is the wrong trade and was explicitly ruled out.
    """

    STRICT = "strict"
    PERMISSIVE = "permissive"


#: Which policy each outbound path uses. Declared as data so a path cannot
#: quietly pick a different one, and so the UI can state the split from the
#: same source the code reads.
POLICY_BY_PATH = {
    "diagnosis": RedactionPolicy.STRICT,
    "query": RedactionPolicy.STRICT,
    "modeling": RedactionPolicy.STRICT,
    "narrative": RedactionPolicy.PERMISSIVE,
}


@dataclass(frozen=True)
class RedactedSample:
    """Sample rows that have been through `redact_records`.

    Constructing this directly is possible in Python and pointless: every
    agent's type hint asks for it, and the only function that produces one
    with `redacted_columns` populated is the redactor. The guarantee is that
    a raw list cannot be passed where this is expected, so the redaction step
    cannot be silently skipped at a call site.
    """

    rows: list[dict[str, Any]]
    #: {column: kind} that were masked, for the egress record. Never the
    #: values themselves.
    redacted_columns: dict[str, str] = field(default_factory=dict)
    #: How many distinct values were masked per column. Useful in the audit
    #: trail ("14 distinct emails were masked") and carries no value.
    masked_value_counts: dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def was_redacted(self) -> bool:
        return bool(self.redacted_columns)


#: Token shape per kind. Deliberately readable: a model that sees <EMAIL_1>
#: understands the slot better than it would understand a hash, and a human
#: reading the prompt log can tell what was removed.
_TOKEN_PREFIX = {
    PIIKind.EMAIL: "EMAIL",
    PIIKind.PHONE_NUMBER: "PHONE",
    PIIKind.PAYMENT_CARD: "CARD",
    PIIKind.IP_ADDRESS: "IP",
    PIIKind.GOVERNMENT_ID: "GOV_ID",
    PIIKind.PERSON_NAME: "NAME",
    PIIKind.POSTAL_ADDRESS: "ADDRESS",
    PIIKind.FREE_TEXT: "TEXT",
}


def redact_records(
    records: list[dict[str, Any]],
    classification: PrivacyClassification | None,
    policy: RedactionPolicy = RedactionPolicy.STRICT,
) -> RedactedSample:
    """The one redaction function. Every outbound path calls this.

    Null stays null: masking an absent value would tell the model a value
    exists where none does, and null-ness is frequently the thing being
    diagnosed.
    """
    if not records:
        return RedactedSample(rows=[])

    redactable = classification.redactable_columns(policy) if classification else {}
    if not redactable:
        # Nothing classified. The rows pass through unchanged, but they are
        # still wrapped - so the call site is identical whether or not this
        # source has personal data in it.
        return RedactedSample(rows=[dict(record) for record in records])

    # One counter per column, so tokens are stable WITHIN a column and a
    # repeated value gets the same token every time it appears.
    assigned: dict[str, dict[str, str]] = {column: {} for column in redactable}

    redacted_rows: list[dict[str, Any]] = []
    for record in records:
        row = dict(record)
        for column, kind in redactable.items():
            if column not in row:
                continue
            value = row[column]
            if value is None or (isinstance(value, float) and value != value):  # NaN
                continue
            key = str(value)
            seen = assigned[column]
            if key not in seen:
                seen[key] = f"<{_TOKEN_PREFIX.get(kind, 'REDACTED')}_{len(seen) + 1}>"
            row[column] = seen[key]
        redacted_rows.append(row)

    return RedactedSample(
        rows=redacted_rows,
        redacted_columns={column: kind.value for column, kind in redactable.items()},
        masked_value_counts={column: len(values) for column, values in assigned.items() if values},
    )


def redact_text_values(
    values: list[Any],
    column: str,
    classification: PrivacyClassification | None,
    policy: RedactionPolicy = RedactionPolicy.STRICT,
) -> tuple[list[Any], bool]:
    """Mask a bare list of values drawn from ONE column.

    Exists for the narrative path, where what leaves is not rows but values
    lifted out of findings - a categorical column's `mode` and its top
    frequencies, an analytics band's `top_entities`. Those are real values
    from a real column and leak exactly as a sample row would.

    Returns (values, was_redacted) so the caller can record the egress.
    """
    redactable = classification.redactable_columns(policy) if classification else {}
    kind = redactable.get(column)
    if kind is None:
        return values, False

    prefix = _TOKEN_PREFIX.get(kind, "REDACTED")
    seen: dict[str, str] = {}
    masked: list[Any] = []
    for value in values:
        if value is None:
            masked.append(None)
            continue
        key = str(value)
        if key not in seen:
            seen[key] = f"<{prefix}_{len(seen) + 1}>"
        masked.append(seen[key])
    return masked, True
