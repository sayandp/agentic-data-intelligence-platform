"""Part 4: the agriculture domain pack.

THE TRAPS ARE THE POINT OF THIS FILE. Four detector bugs in this project came
from a plausible-but-wrong role assignment producing confident nonsense, and
this domain has two obvious ones: a crop YEAR taken as a measure, and area /
production / yield - three non-negative numerics on the same rows - taken for
each other. Each is tested directly, both that the wrong assignment does not
happen and that the right one still does.
"""

import numpy as np
import pandas as pd
import pytest

from app.agriculture.config import AgricultureConfig
from app.agriculture.engine import run_agriculture
from app.agriculture.findings import AgricultureFindingType, AgricultureSeverity, SEVERITY_OF
from app.agriculture.preprocess import DERIVED_YIELD, prepare
from app.analytics.roles import ColumnRole
from app.semantic_roles import detect_for_run
from tests.golden_scenarios import ingest_and_wait

CAUSAL_WORDS = ("because", "caused", "due to", "drove", "led to", "resulted in", "therefore", "thus")

MEASURE_ROLES = (
    ColumnRole.AREA_CULTIVATED,
    ColumnRole.PRODUCTION_QUANTITY,
    ColumnRole.CROP_YIELD,
    ColumnRole.RAINFALL,
    ColumnRole.MARKET_PRICE,
)


def crop_frame(
    years=range(2015, 2023),
    districts=("Palakkad", "Thrissur", "Wayanad", "Idukki"),
    crops=("Rice", "Coconut", "Banana"),
    collapse: tuple[str, str, int] | None = ("Wayanad", "Rice", 2022),
    include_rainfall: bool = True,
    messy_labels: bool = True,
) -> pd.DataFrame:
    """A crop-statistics export shaped like the real thing."""
    rng = np.random.default_rng(11)
    seasons = ["Kharif     ", "Rabi", "Whole Year "] if messy_labels else ["Kharif", "Rabi", "Whole Year"]
    rows = []
    for year in years:
        for district in districts:
            for crop in crops:
                base_area = {"Rice": 1200.0, "Coconut": 800.0, "Banana": 300.0}[crop]
                base_yield = {"Rice": 3.0, "Coconut": 6.0, "Banana": 12.0}[crop]
                collapsing = collapse is not None and (district, crop, year) == collapse
                area = base_area * (0.5 if collapsing else 1.0) * float(rng.uniform(0.97, 1.03))
                per_ha = base_yield * (0.35 if collapsing else 1.0) * float(rng.uniform(0.97, 1.03))
                row = {
                    "District_Name": district.upper() if (messy_labels and year % 2) else district,
                    "Crop_Year": year,
                    "Season": seasons[year % len(seasons)],
                    "Crop": crop,
                    "Area": round(area, 2),
                    "Production": round(area * per_ha, 2),
                }
                if include_rainfall:
                    row["Rainfall_mm"] = round(float(rng.normal(2400, 150)), 1)
                rows.append(row)
    return pd.DataFrame(rows)


def prepared_frame(df: pd.DataFrame, config: AgricultureConfig | None = None):
    detection = detect_for_run(df)
    return prepare(df, detection, config or AgricultureConfig())


# ---- TRAP 1: a crop year is a label, never a measure ----


def test_a_crop_year_never_fills_a_measure_role():
    """`Crop_Year` holds 2015..2022: non-negative, whole, low cardinality. It
    satisfies every shape test area, production, rainfall and price apply, and
    summed it produces a plausible-looking total in the millions."""
    detection = detect_for_run(crop_frame())

    for role in MEASURE_ROLES:
        candidate = detection.best(role)
        assert candidate is None or candidate.column != "Crop_Year", (
            f"Crop_Year was assigned to {role.value} - a year is a label, not a quantity"
        )


def test_a_bare_year_column_is_still_recognised_as_a_year():
    frame = crop_frame().rename(columns={"Crop_Year": "Year"})

    detection = detect_for_run(frame)

    assert detection.best(ColumnRole.CROP_YEAR) is not None
    for role in MEASURE_ROLES:
        candidate = detection.best(role)
        assert candidate is None or candidate.column != "Year"


def test_a_column_named_like_a_measure_but_holding_years_is_refused():
    """The case that makes the year guard load-bearing rather than redundant.

    `Production_Year` matches the PRODUCTION name hint, so the
    name-is-mandatory guard would happily assign it. Only the year check
    separates them - and a year column summed as production yields a total in
    the millions that looks entirely plausible.
    """
    from app.analytics.roles import _score_agri_measure

    frame = crop_frame().rename(columns={"Crop_Year": "Production_Year"})

    detection = detect_for_run(frame)
    production = detection.best(ColumnRole.PRODUCTION_QUANTITY)
    assert production is None or production.column != "Production_Year", (
        "a year column named like a measure was taken as production"
    )
    assert detection.best(ColumnRole.CROP_YEAR).column == "Production_Year"

    # Asserted at the scorer too. End to end, the genuine `Production` column
    # outranks `Production_Year` on the alphabetical tie-break, so the frame
    # alone cannot show the year guard doing any work - falsification caught
    # exactly that. A source whose ONLY production-named column is a year
    # would have no such protection.
    score, reasons = _score_agri_measure(
        frame["Production_Year"], "Production_Year", ColumnRole.PRODUCTION_QUANTITY
    )
    assert score == 0.0
    assert "crop year" in reasons[0]


def test_an_unnamed_integer_column_in_year_range_is_not_taken_as_a_measure():
    """The value-range half of the guard, with no name to help."""
    from app.analytics.roles import _reads_as_year

    years = pd.Series([2015, 2016, 2017, 2018, 2019])
    tonnes = pd.Series([1200.5, 3400.0, 880.25, 5100.75, 2000.0])

    assert _reads_as_year(years, "some_column")
    assert not _reads_as_year(tonnes, "some_column")


# ---- TRAP 2: area, production and yield are mutually confusable ----


def test_area_production_and_yield_are_assigned_to_the_right_columns():
    detection = detect_for_run(crop_frame())

    assert detection.best(ColumnRole.AREA_CULTIVATED).column == "Area"
    assert detection.best(ColumnRole.PRODUCTION_QUANTITY).column == "Production"


def test_a_production_column_is_never_taken_for_area():
    frame = crop_frame()[["District_Name", "Crop_Year", "Season", "Crop", "Production"]]

    detection = detect_for_run(frame)

    area = detection.best(ColumnRole.AREA_CULTIVATED)
    assert area is None, f"Production was assigned to area_cultivated as {area.column if area else None}"


def test_a_yield_column_is_never_taken_for_production():
    frame = crop_frame().rename(columns={"Production": "Yield_per_hectare"})

    detection = detect_for_run(frame)

    production = detection.best(ColumnRole.PRODUCTION_QUANTITY)
    assert production is None or production.column != "Yield_per_hectare"
    assert detection.best(ColumnRole.CROP_YIELD).column == "Yield_per_hectare"


def test_an_unnamed_numeric_column_fills_none_of_the_three():
    """Shape cannot separate a 2.5-hectare area from a 2.5 t/ha yield, so a
    column that does not name itself is assigned to neither.

    TWO mechanisms enforce this and the test asserts the outcome rather than
    either one: the mandatory-name guard scores it zero, and the pre-existing
    confidence floor would reject a shape-only 0.3 as LOW even without that
    guard. Falsification showed the floor alone is sufficient today - the name
    guard is defence in depth, and stating the reason is what keeps it from
    being deleted as dead code.
    """
    from app.analytics.roles import _score_agri_measure

    frame = crop_frame()[["District_Name", "Crop_Year", "Season", "Crop"]].copy()
    frame["measurement"] = [1234.5 + i for i in range(len(frame))]

    detection = detect_for_run(frame)
    for role in (ColumnRole.AREA_CULTIVATED, ColumnRole.PRODUCTION_QUANTITY, ColumnRole.CROP_YIELD):
        candidate = detection.best(role)
        assert candidate is None or candidate.column != "measurement", (
            f"an unnamed numeric column was assigned to {role.value} on shape alone"
        )

    # And the guard itself scores it zero with the reason, independently of
    # where the confidence floor happens to sit.
    score, reasons = _score_agri_measure(frame["measurement"], "measurement", ColumnRole.AREA_CULTIVATED)
    assert score == 0.0
    assert "no name evidence" in reasons[0]


def test_a_sibling_stands_down_and_says_which_measure_the_name_reads_as():
    """Tested directly rather than through a frame: real crop-statistics
    column names do not match two of these hints at once, so an end-to-end
    fixture never reaches this branch - and a guard no test can reach is a
    guard nobody has shown to work.

    What this branch adds is the REASON, not the refusal. A column named
    `Area` asked to fill the production slot is refused either way - the
    mandatory-name guard would reject it for having no production evidence.
    The stand-down refuses it for the more useful reason: the name reads as
    a different measure. Falsification showed the outcomes are identical, so
    the reason is what this test pins.
    """
    from app.analytics.roles import _score_agri_measure

    values = pd.Series([100.0, 200.0, 300.0, 400.0])

    score, reasons = _score_agri_measure(
        values,
        "Area",
        ColumnRole.PRODUCTION_QUANTITY,
        conflicting=(ColumnRole.AREA_CULTIVATED,),
    )

    assert score == 0.0
    assert "area_cultivated" in reasons[0], (
        "the refusal must name the measure the column reads as, not merely report an absence"
    )


# ---- preprocessing ----


def test_yield_is_derived_and_the_derivation_is_reported():
    work, report, _roles = prepared_frame(crop_frame())

    assert DERIVED_YIELD in work.columns
    derived = [d for d in report.derived if d.metric == DERIVED_YIELD]
    assert derived, "the derivation must be reported with every derived number"
    assert "Production" in derived[0].formula and "Area" in derived[0].formula


def test_zero_area_never_produces_an_infinite_or_silent_yield():
    """A crop listed but not sown is common in real crop statistics. Dividing
    by zero produces inf, which propagates through a mean and emerges as a
    plausible-looking average."""
    frame = crop_frame()
    frame.loc[len(frame)] = {
        "District_Name": "Idukki", "Crop_Year": 2022, "Season": "Rabi", "Crop": "Banana",
        "Area": 0.0, "Production": 140.0, "Rainfall_mm": 2350.0,
    }

    work, report, _roles = prepared_frame(frame)

    values = work[DERIVED_YIELD]
    assert not bool(np.isinf(values.dropna()).any()), "a zero-area row produced an infinite yield"
    assert int(values.isna().sum()) >= 1, "the zero-area row must have no yield at all"

    derived = [d for d in report.derived if d.metric == DERIVED_YIELD][0]
    assert derived.undefined_row_count >= 1
    assert "zero area" in (derived.undefined_reason or "")


def test_an_existing_yield_column_is_used_as_supplied_not_recomputed():
    frame = crop_frame()
    frame["Yield"] = 9.99

    _work, report, _roles = prepared_frame(frame)

    derived = [d for d in report.derived if d.metric == DERIVED_YIELD][0]
    assert "as supplied" in derived.formula


def test_every_label_normalisation_is_recorded():
    """Collapsing PALAKKAD and Palakkad is necessary to group at all. Doing it
    silently would leave a reader unable to tell whether two districts merged
    because they are the same place or because the normaliser was too
    aggressive."""
    _work, report, _roles = prepared_frame(crop_frame(messy_labels=True))

    assert report.normalisations, "label collapses must be listed"
    collapsed = {n["normalised_to"] for n in report.normalisations}
    assert "Palakkad" in collapsed or "Kharif" in collapsed
    for entry in report.normalisations:
        assert entry["from"], "every normalisation names what it came from"


def test_the_grain_is_stated():
    _work, report, _roles = prepared_frame(crop_frame())

    assert report.grain, "a yield averaged over an unstated grain is not actionable"
    assert "Crop_Year" in report.grain, "the crop year must be part of the grain or history collapses"


def test_the_grain_is_configurable():
    config = AgricultureConfig(grain=(ColumnRole.DISTRICT, ColumnRole.CROP))

    _work, report, _roles = prepared_frame(crop_frame(), config)

    assert "Season" not in report.grain


# ---- applicability ----


def test_a_non_agricultural_source_is_refused_with_the_missing_role_named():
    frame = pd.DataFrame({"order_id": range(50), "amount": [10.0] * 50, "city": ["Paris"] * 50})

    result = run_agriculture("r", frame, detect_for_run(frame))

    assert result.applicable is False
    assert result.not_applicable_reason
    assert result.missing_roles


def test_a_district_crop_production_table_with_no_season_or_year_is_refused():
    """A district, a crop and a production figure could be a logistics table."""
    frame = crop_frame()[["District_Name", "Crop", "Production"]]

    result = run_agriculture("r", frame, detect_for_run(frame))

    assert result.applicable is False
    assert "grown" in (result.not_applicable_reason or "")


def test_found_and_missing_roles_are_both_reported():
    result = run_agriculture("r", crop_frame(include_rainfall=False), detect_for_run(crop_frame(include_rainfall=False)))

    assert result.applicable
    assert result.resolved_roles
    assert "rainfall" in result.missing_roles, "an absent role must be named even when it did not block the pack"


# ---- rules ----


def test_yield_collapse_fires_on_the_district_that_collapsed_and_no_other():
    frame = crop_frame(collapse=("Wayanad", "Rice", 2022))

    result = run_agriculture("r", frame, detect_for_run(frame))

    scopes = {
        f.payload.scope for f in result.findings if f.finding_type is AgricultureFindingType.YIELD_COLLAPSE
    }
    assert scopes == {"Wayanad / Rice"}, f"expected only Wayanad / Rice, got {scopes}"


def test_yield_collapse_is_silent_when_nothing_collapses():
    frame = crop_frame(collapse=None)

    result = run_agriculture("r", frame, detect_for_run(frame))

    assert not [f for f in result.findings if f.finding_type is AgricultureFindingType.YIELD_COLLAPSE]


def test_yield_collapse_compares_a_district_with_its_own_history():
    """Soil, rainfall and crop mix differ between districts, so a
    cross-district comparison dressed as a collapse would be a false alarm
    every season."""
    frame = crop_frame()

    result = run_agriculture("r", frame, detect_for_run(frame))

    collapses = [f for f in result.findings if f.finding_type is AgricultureFindingType.YIELD_COLLAPSE]
    assert collapses
    assert "OWN mean yield" in collapses[0].payload.compared_against


def test_a_history_rule_refuses_below_the_configured_minimum():
    """Two points is a line, not a history, and a "collapse" measured against
    one prior season is noise with a label.

    The fixture puts the collapse IN the short window, so the rule would fire
    if the minimum were lowered. A previous version used a window that did not
    contain the collapse at all, and passed whatever the minimum was.
    """
    frame = crop_frame(years=range(2021, 2023), collapse=("Wayanad", "Rice", 2022))

    strict = run_agriculture("r", frame, detect_for_run(frame), AgricultureConfig(min_history_points=3))
    permissive = run_agriculture("r", frame, detect_for_run(frame), AgricultureConfig(min_history_points=1))

    def collapses(result):
        return [f for f in result.findings if f.finding_type is AgricultureFindingType.YIELD_COLLAPSE]

    permissive_fires = collapses(permissive)
    assert permissive_fires, (
        "the fixture must be able to fire, or this test proves nothing about the minimum"
    )
    assert not collapses(strict), "a two-point history must not support a collapse claim"


def test_below_median_yield_compares_per_crop():
    """Rice and coconut yields differ by orders of magnitude; one pooled
    median would rank every low-yield crop as underperforming."""
    frame = crop_frame()

    result = run_agriculture("r", frame, detect_for_run(frame))

    findings = [f for f in result.findings if f.finding_type is AgricultureFindingType.BELOW_MEDIAN_YIELD]
    for finding in findings:
        assert "for" in finding.payload.compared_against
        assert "per crop" in finding.payload.compared_against


def test_key_values_report_absence_rather_than_zero():
    frame = crop_frame(include_rainfall=False)

    result = run_agriculture("r", frame, detect_for_run(frame))

    totals = [f for f in result.findings if f.finding_type is AgricultureFindingType.SEASON_TOTALS][0]
    payload = totals.payload
    assert payload.rainfall_mean is None
    assert "rainfall_mean" in payload.undefined, "a missing measure must say so, not render as zero"


def test_key_values_state_the_yield_basis():
    result = run_agriculture("r", crop_frame(), detect_for_run(crop_frame()))

    totals = [f for f in result.findings if f.finding_type is AgricultureFindingType.SEASON_TOTALS][0]
    assert totals.payload.average_yield is not None
    assert totals.payload.yield_basis, "an average yield with no stated basis is a number nobody can trace"


def test_every_warning_states_rule_observed_threshold_and_basis():
    result = run_agriculture("r", crop_frame(), detect_for_run(crop_frame()))

    for finding in result.findings:
        if finding.severity is AgricultureSeverity.KEY_VALUE:
            continue
        payload = finding.payload
        assert payload.finding_type
        assert payload.observed is not None
        assert payload.threshold is not None
        assert payload.compared_against, f"{payload.finding_type} has no comparison basis"


def test_one_rule_raising_never_sinks_the_rest(monkeypatch):
    def _explode(df, roles, config, baseline_profile=None):
        raise RuntimeError("boom")

    from app.agriculture.rules import season_totals

    monkeypatch.setattr("app.agriculture.engine.RULES", (season_totals, _explode))

    result = run_agriculture("r", crop_frame(), detect_for_run(crop_frame()))

    assert result.applicable
    assert any("boom" in s.get("reason", "") for s in result.skipped_rules)
    assert result.findings


def test_nothing_agricultural_is_on_the_auto_apply_allowlist():
    for finding_type in AgricultureFindingType:
        assert SEVERITY_OF[finding_type] in {
            AgricultureSeverity.WARNING,
            AgricultureSeverity.IMPROVEMENT,
            AgricultureSeverity.KEY_VALUE,
        }


def test_no_agriculture_output_uses_causal_language():
    result = run_agriculture("r", crop_frame(), detect_for_run(crop_frame()))

    blob = str(result.model_dump(mode="json")).lower()
    for word in CAUSAL_WORDS:
        assert word not in blob, f"agriculture output used causal language: {word}"


# ---- charts ----


def test_the_charts_follow_the_shape_rules():
    result = run_agriculture("r", crop_frame(), detect_for_run(crop_frame()))

    by_kind = {c["kind"]: c for c in result.charts}
    assert by_kind["yield_over_seasons"]["figure_json"]["data"][0]["type"] == "scatter"
    assert by_kind["yield_by_district"]["figure_json"]["data"][0]["type"] == "bar"
    assert by_kind["crop_share"]["figure_json"]["data"][0]["type"] == "bar"
    assert by_kind["yield_distribution"]["figure_json"]["data"][0]["type"] == "histogram"


def test_ordered_data_uses_the_accent_and_categories_the_palette():
    result = run_agriculture("r", crop_frame(), detect_for_run(crop_frame()))

    by_kind = {c["kind"]: c for c in result.charts}
    assert by_kind["yield_over_seasons"]["colour_roles"] == ["accent"]
    assert by_kind["yield_distribution"]["colour_roles"] == ["accent"]
    assert by_kind["yield_by_district"]["colour_roles"] == ["categorical"]
    assert by_kind["crop_share"]["colour_roles"] == ["categorical"]


def test_no_agriculture_chart_colour_resolves_to_a_status_token():
    result = run_agriculture("r", crop_frame(), detect_for_run(crop_frame()))

    for chart in result.charts:
        for role in chart["colour_roles"]:
            assert role in {"accent", "categorical", "sequential"}, f"{chart['chart_id']} uses {role!r}"


# ---- the cross-cutting requirement ----
#
# Report, deck export, comparison and session summary must all work on an
# agriculture run with NO special-casing. These drive the real pipeline rather
# than asserting on the pack in isolation, because "works everywhere else" is
# not a property of this package - it is a property of the seams.


def _ingest_crop_source(client, tmp_path, name="crops.csv", frame=None):
    csv = tmp_path / name
    (frame if frame is not None else crop_frame()).to_csv(csv, index=False)
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv)}}
    ).json()["id"]
    return source_id, ingest_and_wait(client, source_id)


def test_an_agriculture_run_produces_a_persisted_analysis(client, tmp_path):
    _source_id, result = _ingest_crop_source(client, tmp_path)

    body = client.get(f"/agriculture/{result['run_id']}").json()

    assert body["applicable"] is True, body.get("not_applicable_reason")
    assert body["findings"]
    assert body["charts"]


def test_a_non_agricultural_run_still_gets_a_row_stating_why(client, tmp_path):
    """A missing row would leave a user unable to tell a refusal from a
    failure."""
    csv = tmp_path / "orders.csv"
    csv.write_text("order_id,amount,city\n" + "\n".join(f"{i},10.0,Paris" for i in range(60)) + "\n")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv)}}
    ).json()["id"]
    result = ingest_and_wait(client, source_id)

    body = client.get(f"/agriculture/{result['run_id']}").json()

    assert body["applicable"] is False
    assert body["not_applicable_reason"]


def test_the_comparison_covers_agriculture_without_special_casing(client, tmp_path):
    """The comparison iterates the domain-pack registry. A second pack must
    appear as its own section with no consumer edit."""
    csv = tmp_path / "crops.csv"
    crop_frame().to_csv(csv, index=False)
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv)}}
    ).json()["id"]
    first = ingest_and_wait(client, source_id)
    second = ingest_and_wait(client, source_id)

    body = client.get(f"/compare?run_a={first['run_id']}&run_b={second['run_id']}").json()

    sections = {s["name"] for s in body["sections"]}
    assert "agriculture" in sections, f"agriculture is not a comparison section: {sections}"
    assert "marketing" in sections, "the first pack must still be compared"


def test_the_session_summary_can_cite_an_agriculture_finding(client, tmp_path, db_session):
    from app.models import Run
    from app.summary.facts import build_summary_facts

    _source_id, result = _ingest_crop_source(client, tmp_path)
    run = db_session.query(Run).filter(Run.id == result["run_id"]).one()

    facts = build_summary_facts(db_session, run)

    origins = {f.origin for f in facts}
    assert any(o.startswith("agriculture") for o in origins), (
        f"no agriculture fact reached the summary: {sorted(origins)}"
    )


def test_the_deck_exports_for_an_agriculture_run(client, tmp_path):
    """The deck never knew about domain packs, so a second one must need no
    change there. Asserted rather than assumed."""
    _source_id, result = _ingest_crop_source(client, tmp_path)

    from app.export.pipeline import collect_sources

    from app.db import SessionLocal
    from app.models import Run

    db = SessionLocal()
    try:
        run = db.query(Run).filter(Run.id == result["run_id"]).one()
        sources = collect_sources(db, run)
    finally:
        db.close()

    assert sources.run_id == result["run_id"]
