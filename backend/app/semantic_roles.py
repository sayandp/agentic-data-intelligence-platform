"""ONE semantic role detection per run, shared by every downstream agent.

app/analytics/roles.py has always known that `Customer ID` is an entity
identifier and `InvoiceDate` is the event date. Only the analytics agent
asked. Exploration, Query, Modeling and Narrative saw dtypes and nothing
else, which is why exploration correlated an invoice number against a price
and reported a trend on a customer id - a customer id plotted over time is
not a finding, it is an artefact of the id being an integer.

This module is the shared surface, in the same shape as column_kind and
value_basis before it: the detector is NOT reimplemented anywhere: it is run
once, persisted on the run, and read.

WHAT IS AND IS NOT A DECISION HERE
----------------------------------
Role detection stays deterministic. `prompt_context` exists so an LLM can be
TOLD what a column is; no agent asks a model to decide a role, and nothing
downstream branches on a model's opinion of one. A role that is confirmed by
a human, merely detected, or detected weakly reads differently in that
context, because presenting an unconfirmed guess as fact is how a model ends
up reasoning confidently about the wrong column.
"""

from __future__ import annotations

import pandas as pd

from app.analytics.roles import (
    ColumnRole,
    RoleDetection,
    SemanticColumnDetector,
    apply_confirmed_roles,
)

#: Roles whose columns are KEYS, not measurements. Correlating one against a
#: price, fitting a trend through one, or reporting its distribution
#: describes the numbering scheme rather than the business.
IDENTIFIER_ROLES = (ColumnRole.ENTITY_ID, ColumnRole.TRANSACTION_ID, ColumnRole.ITEM_ID)

#: How each identifier role is named in a skip reason a person will read.
_ROLE_LABELS = {
    ColumnRole.ENTITY_ID: "entity identifier",
    ColumnRole.TRANSACTION_ID: "transaction identifier",
    ColumnRole.ITEM_ID: "item identifier",
    ColumnRole.EVENT_DATE: "event date",
    ColumnRole.MONETARY: "monetary value",
    ColumnRole.QUANTITY: "quantity",
}


def detect_for_run(df: pd.DataFrame, confirmed_roles: dict[str, str] | None = None) -> RoleDetection:
    """The one detection pass. Identical inputs give identical output, which
    is what lets the analytics agent and this share a result."""
    return apply_confirmed_roles(SemanticColumnDetector().detect(df), confirmed_roles or {}, df.columns)


def roles_document(detection: RoleDetection) -> dict:
    """The persisted form, stored on Run.semantic_roles.

    `by_column` is the lookup every consumer actually wants and is derived
    here rather than by each of them, so "is this column an identifier?"
    cannot be answered two slightly different ways in two places.
    """
    document = detection.to_dict()
    by_column: dict[str, dict] = {}
    for role, candidate in detection.assigned().items():
        by_column[candidate.column] = {
            "role": role.value,
            "label": _ROLE_LABELS.get(role, role.value),
            "confidence": candidate.confidence.value,
            "score": candidate.score,
            # A human's answer outranks an inference, and downstream copy
            # says so rather than presenting both as equally settled.
            "confirmed": detection.confirmed_roles.get(role.value) == candidate.column,
        }
    document["by_column"] = by_column
    return document


def identifier_exclusions(roles: dict | None) -> dict[str, str]:
    """{column: reason} for every column that is a KEY rather than a measure.

    The reason is written for the `skipped` list a user reads, because an
    analysis that is silently absent is indistinguishable from one that
    found nothing.
    """
    identifier_values = {role.value for role in IDENTIFIER_ROLES}
    exclusions: dict[str, str] = {}
    for column, entry in ((roles or {}).get("by_column") or {}).items():
        if entry.get("role") not in identifier_values:
            continue
        label = entry.get("label") or entry.get("role")
        how = "confirmed" if entry.get("confirmed") else "detected"
        exclusions[column] = f"excluded: {how} as {label}"
    return exclusions


def prompt_context(roles: dict | None) -> list[dict]:
    """The role block handed to Query, Modeling and Narrative.

    CONTEXT ONLY. The model is told what the detector found so it stops
    treating an identifier as a plain number; it is never asked to decide a
    role, and nothing downstream reads a role back out of a model response.

    A weakly-detected or unconfirmed role is labelled as such. Presenting a
    guess as settled fact is precisely what makes a model reason confidently
    about the wrong column.
    """
    context = []
    for column, entry in sorted(((roles or {}).get("by_column") or {}).items()):
        confidence = entry.get("confidence")
        if entry.get("confirmed"):
            basis = "confirmed by a person"
        elif confidence == "high":
            basis = "detected"
        else:
            basis = f"detected with {confidence or 'unknown'} confidence - treat as unconfirmed"
        context.append(
            {
                "column": column,
                "role": entry.get("role"),
                "means": entry.get("label"),
                "basis": basis,
            }
        )
    return context
