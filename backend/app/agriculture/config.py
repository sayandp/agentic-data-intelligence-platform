"""Every threshold and cap the Agriculture Agent uses, in one place.

Mirrors app/marketing/config.py exactly: constructor-configurable, reported
with every run's output, and no magic number at any rule's call site.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.analytics.roles import ColumnRole

#: What a source must have before this pack will say anything. Deliberately
#: small: a district, a crop and a production figure is the least that makes
#: a file agricultural. Area is NOT required - a table carrying production and
#: yield without area is still analysable, and demanding it would refuse real
#: government crop statistics.
DEFAULT_REQUIRED_ROLES: tuple[ColumnRole, ...] = (
    ColumnRole.DISTRICT,
    ColumnRole.CROP,
    ColumnRole.PRODUCTION_QUANTITY,
)

#: At least one of these must also be present. Production alone, grouped by a
#: district and a crop, could be a logistics table; a season, a crop year or an
#: area is what makes it a growing record.
DEFAULT_REQUIRED_ANY_OF: tuple[ColumnRole, ...] = (
    ColumnRole.SEASON,
    ColumnRole.CROP_YEAR,
    ColumnRole.AREA_CULTIVATED,
)

#: The grain rows are aggregated to before any rule runs. Configurable, and
#: STATED in the output - a yield averaged over the wrong grain is a different
#: number that looks equally reasonable.
DEFAULT_GRAIN: tuple[ColumnRole, ...] = (
    ColumnRole.DISTRICT,
    ColumnRole.CROP,
    ColumnRole.SEASON,
)


@dataclass(frozen=True)
class AgricultureConfig:
    required_roles: tuple[ColumnRole, ...] = DEFAULT_REQUIRED_ROLES
    required_any_of: tuple[ColumnRole, ...] = DEFAULT_REQUIRED_ANY_OF
    grain: tuple[ColumnRole, ...] = DEFAULT_GRAIN

    #: WARNING: a district-crop whose yield falls to below this fraction of
    #: its own historical mean. Its OWN history, never another district's -
    #: soil, rainfall and crop mix differ, and a cross-district comparison
    #: dressed as a collapse would be a false alarm every season.
    yield_collapse_fraction: float = 0.6

    #: WARNING: production falling by more than this fraction against the
    #: same district-crop's prior period.
    production_drop_fraction: float = 0.3

    #: WARNING: rainfall outside this many standard deviations of the same
    #: season's own historical range.
    rainfall_sigma: float = 2.0

    #: WARNING: area cultivated falling by more than this fraction.
    area_fall_fraction: float = 0.25

    #: IMPROVEMENT: a district-crop below this fraction of the state median
    #: yield for that crop.
    below_median_fraction: float = 0.8

    #: IMPROVEMENT: coefficient of variation above which a district-crop's
    #: yield is called unstable.
    high_variance_cv: float = 0.5

    #: The minimum number of prior observations before a history-based rule
    #: will fire. Two points is a line, not a history, and a "collapse"
    #: measured against one prior season is noise with a label.
    min_history_points: int = 3

    #: KEY VALUE: how many crops the top-crops summary names.
    top_crops: int = 5

    #: Season labels arrive padded and cased inconsistently in real crop
    #: statistics ("Kharif     ", "kharif"). Normalisation is recorded, never
    #: silent.
    normalise_labels: bool = True

    def as_reported(self) -> dict:
        return {
            "required_roles": [r.value for r in self.required_roles],
            "required_any_of": [r.value for r in self.required_any_of],
            "grain": [r.value for r in self.grain],
            "yield_collapse_fraction": self.yield_collapse_fraction,
            "production_drop_fraction": self.production_drop_fraction,
            "rainfall_sigma": self.rainfall_sigma,
            "area_fall_fraction": self.area_fall_fraction,
            "below_median_fraction": self.below_median_fraction,
            "high_variance_cv": self.high_variance_cv,
            "min_history_points": self.min_history_points,
            "top_crops": self.top_crops,
            "normalise_labels": self.normalise_labels,
        }
