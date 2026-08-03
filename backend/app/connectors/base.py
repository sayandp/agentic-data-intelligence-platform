"""Connector ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.contract import DataContract


class BaseConnector(ABC):
    @property
    @abstractmethod
    def source_immutable(self) -> bool:
        """True if re-fetching this source is guaranteed to return the exact
        same data every time (a local file). False if the underlying data can
        change between fetches (a database table, a live API endpoint).

        Anything that needs to act later on "what a human saw when a decision
        was made" - e.g. verifying an approved fix - must not re-fetch a
        source where this is False; it must work from a snapshot taken at
        ingest time instead (see app/run_snapshots.py). Required on every
        connector, with no default, so a future connector has to make this
        call explicitly rather than silently inherit the wrong answer.
        """

    @abstractmethod
    def fetch(self) -> DataContract:
        ...
