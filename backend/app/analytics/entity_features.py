"""Per-entity recency/frequency/monetary features - computed ONCE.

RFM, behavioural segmentation and historical CLV all rest on the same three
numbers per entity. Computing them in three places is how three modules end
up quietly disagreeing about the same customer, which is exactly the drift
app/column_kind.py exists to prevent one layer down. So they are computed
here and passed around.

NO LLM. NO CAUSAL VOCABULARY.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class EntityFeatures:
    """One row per entity, plus the observation window it was measured in."""

    frame: pd.DataFrame          # index=entity, columns: recency_days, frequency, monetary
    observation_end: pd.Timestamp
    entity_column: str
    date_column: str
    value_column: str | None

    @property
    def entity_count(self) -> int:
        return int(len(self.frame))


def build_entity_features(
    df: pd.DataFrame,
    entity_column: str,
    date_column: str,
    value_column: str | None,
) -> EntityFeatures | None:
    """Returns None when there is nothing to measure.

    `recency_days` is measured from the LATEST EVENT IN THE DATA, never from
    today: a dataset ingested six months after it was exported would
    otherwise report every customer as long-lapsed, which is an artefact of
    when the file was read rather than anything about the customers.
    """
    columns = [entity_column, date_column] + ([value_column] if value_column else [])
    frame = df[columns].dropna(subset=[entity_column, date_column])
    if frame.empty:
        return None

    dates = pd.to_datetime(frame[date_column], errors="coerce")
    frame = frame.assign(**{date_column: dates}).dropna(subset=[date_column])
    if frame.empty:
        return None

    observation_end = frame[date_column].max()
    grouped = frame.groupby(entity_column, observed=True)

    features = pd.DataFrame(
        {
            "recency_days": (observation_end - grouped[date_column].max()).dt.days.astype(float),
            "frequency": grouped[date_column].count().astype(float),
            "first_seen": grouped[date_column].min(),
            "last_seen": grouped[date_column].max(),
        }
    )
    if value_column:
        features["monetary"] = grouped[value_column].sum().astype(float)
    else:
        features["monetary"] = 0.0

    features["lifespan_days"] = (features["last_seen"] - features["first_seen"]).dt.days.astype(float)

    return EntityFeatures(
        frame=features,
        observation_end=observation_end,
        entity_column=entity_column,
        date_column=date_column,
        value_column=value_column,
    )
