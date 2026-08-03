from __future__ import annotations

from typing import Callable

import pandas as pd

from tests.corruption.ground_truth import CorruptionGroundTruth
from tests.corruption.injectors import (
    change_dtype,
    drop_column,
    inject_nulls,
    inject_whitespace_case,
    rename_column,
    shift_distribution,
    truncate_rows,
)

Injector = Callable[..., tuple[pd.DataFrame, CorruptionGroundTruth]]
CorruptionSpec = tuple[str, dict]


class CorruptionSuite:
    """Applies named, labelled, seeded corruptions to a clean dataset.

    `apply`/`apply_all` corrupt each name in isolation against its own copy of
    the input frame - what evaluates single-fault detection. `compose` chains
    multiple named corruptions onto the *same* frame in sequence, producing
    one corrupted dataset and a list of ground truths - real pipelines break
    in multiples, so this is what evaluates that case.
    """

    REGISTRY: dict[str, Injector] = {
        "rename_column": rename_column,
        "change_dtype": change_dtype,
        "inject_nulls": inject_nulls,
        "drop_column": drop_column,
        "shift_distribution": shift_distribution,
        "inject_whitespace_case": inject_whitespace_case,
        "truncate_rows": truncate_rows,
    }

    def names(self) -> list[str]:
        return list(self.REGISTRY)

    def _injector(self, name: str) -> Injector:
        if name not in self.REGISTRY:
            raise ValueError(f"unknown corruption '{name}'; choices: {sorted(self.REGISTRY)}")
        return self.REGISTRY[name]

    def apply(self, df: pd.DataFrame, name: str, seed: int, **kwargs) -> tuple[pd.DataFrame, CorruptionGroundTruth]:
        return self._injector(name)(df.copy(deep=True), seed, **kwargs)

    def apply_all(
        self,
        df: pd.DataFrame,
        names: list[str] | None = None,
        base_seed: int = 0,
        kwargs_by_name: dict[str, dict] | None = None,
    ) -> dict[str, tuple[pd.DataFrame, CorruptionGroundTruth]]:
        """Applies each corruption independently, in isolation, to its own copy.

        Seeds are derived deterministically from base_seed (base_seed + index
        in `names`), so the whole batch is reproducible from one number while
        each corruption still gets a distinct seed.
        """
        names = names or self.names()
        kwargs_by_name = kwargs_by_name or {}
        return {
            name: self.apply(df, name, seed=base_seed + i, **kwargs_by_name.get(name, {}))
            for i, name in enumerate(names)
        }

    def compose(
        self, df: pd.DataFrame, specs: list[CorruptionSpec], seed: int
    ) -> tuple[pd.DataFrame, list[CorruptionGroundTruth]]:
        """Applies multiple named corruptions in sequence to the same frame.

        Each step in `specs` is (name, kwargs) and gets a derived seed
        (seed, seed + 1, seed + 2, ...), so the composed result as a whole is
        reproducible from the single `seed` passed in. Order matters: a later
        step operates on the output of the previous one (e.g. a column
        renamed in step 1 no longer exists under its old name for step 2).
        """
        working = df.copy(deep=True)
        truths: list[CorruptionGroundTruth] = []
        for i, (name, kwargs) in enumerate(specs):
            working, truth = self._injector(name)(working, seed + i, **kwargs)
            truths.append(truth)
        return working, truths
