"""Masking the VALUES carried inside findings, before grounding sees them.

The narrative path does not send sample rows - it sends the findings object.
That object carries real values lifted out of real columns:

    exploration  CategoricalSummaryPayload.mode and .top_frequencies[].value
                 - the most frequent values of a categorical column
    analytics    ConcentrationBandPayload.top_entities
                 NonContributingEntitiesPayload.examples
                 AssociationRulePayload.antecedent / .consequent
                 SegmentProfilePayload.segment (only when the segment name IS
                 an entity value, which it is not for RFM or k-means, so it
                 is left alone)

A column of email addresses whose top three values reach the model is the
same leak as a sample row, arriving by a different route. This masks those
value slots using the SAME token scheme the sample redactor uses, so a
reader of the prompt sees <EMAIL_1> in both places.

Structure over check: this walks the SERIALISED findings dict rather than the
model objects, because the serialised form is exactly what gets sent. Masking
the objects and then serialising would leave open the possibility of a field
that serialises differently from how it was masked.
"""

from __future__ import annotations

from typing import Any

from app.privacy.classification import PrivacyClassification
from app.privacy.redaction import redact_text_values


def redact_findings_payload(
    payload: dict, classification: PrivacyClassification | None
) -> tuple[dict, dict[str, int]]:
    """A findings dict with column values masked. Returns (payload, counts).

    `counts` is {column: number of value slots masked} for the egress record -
    never the values.
    """
    redactable = classification.redactable_columns() if classification else {}
    if not redactable:
        return payload, {}

    masked_counts: dict[str, int] = {}

    def mask_for(column: str, values: list[Any]) -> list[Any]:
        if column not in redactable:
            return values
        result, was = redact_text_values(values, column, classification)
        if was:
            masked_counts[column] = masked_counts.get(column, 0) + len(values)
        return result

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node

        node = {key: walk(value) for key, value in node.items()}

        # The finding's own `columns` list says which column its payload
        # describes. That is the join between a value slot and a
        # classification, and it is why this walks findings rather than
        # guessing from field names.
        columns = [c for c in (node.get("columns") or []) if isinstance(c, str)]
        target = next((c for c in columns if c in redactable), None)

        payload_node = node.get("payload")
        if target and isinstance(payload_node, dict):
            if isinstance(payload_node.get("mode"), str):
                payload_node["mode"] = mask_for(target, [payload_node["mode"]])[0]
            for slot in ("top_entities", "examples", "antecedent", "consequent"):
                if isinstance(payload_node.get(slot), list):
                    payload_node[slot] = mask_for(target, payload_node[slot])
            frequencies = payload_node.get("top_frequencies")
            if isinstance(frequencies, list):
                values = [entry.get("value") for entry in frequencies if isinstance(entry, dict)]
                masked = mask_for(target, values)
                index = 0
                for entry in frequencies:
                    if isinstance(entry, dict):
                        entry["value"] = masked[index]
                        index += 1
        return node

    return walk(payload), masked_counts
