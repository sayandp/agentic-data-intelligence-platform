"""Post-condition verification for fixes, and fix_chain replay for
reconstructing a run's repaired frame from source.

Post-condition verification is what turns "self-healing" into something
demonstrated rather than claimed: apply -> re-run the failed expectation(s)
against the repaired frame -> commit only if they now pass, otherwise roll
back via the reversal record and escalate. A fix that silently fails to fix
is worse than no fix, because a resolved-looking state would hide an
unresolved problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.actions import apply_action, revert_action
from app.connectors.base import BaseConnector
from app.contract import DataContract, SourceType
from app.correlation import CorrelatedGroup
from app.diagnosis.models import FixAction
from app.models import DataSource, Run
from app.run_snapshots import load_snapshot
from app.validation.engine import RuleOutcome, RuleStatus, ValidationEngine


@dataclass
class RepairResult:
    verified: bool
    new_df: pd.DataFrame
    reversal_record: dict | None
    error: str | None = None
    # Rules that were evaluated and PASSING on the pre-fix frame but are
    # FAILING on the post-fix frame - a genuine regression, and the only
    # thing that triggers a do-no-harm revert. Populated only on that
    # revert path; empty for every other outcome, including the
    # pre-existing "group's own rule didn't clear" revert.
    new_failures: list[str] = field(default_factory=list)
    # Rules that were not_applicable (or never evaluated at all) on the
    # pre-fix frame and are FAILING on the post-fix frame - REVEALED by the
    # fix, not caused by it (e.g. a drift check that couldn't run while the
    # column was the wrong dtype). Populated only when verified=True; the
    # fix is kept, and the caller is responsible for raising each of these
    # as its own fresh validation event to be diagnosed and gated normally.
    revealed_failures: list[RuleOutcome] = field(default_factory=list)


def attempt_fix(
    contract: DataContract,
    group: CorrelatedGroup,
    action: FixAction,
    spec: dict,
    baseline_profile: dict,
    engine: ValidationEngine | None = None,
) -> RepairResult:
    """Applies `action`, re-evaluates the full current state against the
    baseline (three-state: passed/failed/not_applicable - see
    app/validation/engine.py), and confirms both that none of `group`'s
    original failures still fire AND that no rule which was EVALUATED AND
    PASSING before the fix is now failing.

    A fix may only REMOVE failures, never regress a previously-passing rule.
    `strip_whitespace` resolving its own categorical_drift while a
    distribution_drift that was PASSING before now fails is exactly the
    class of bug this guards against - the group's own check passing is
    necessary but not sufficient evidence the fix was safe. Reverts
    (verified=False) on a regression like that, or if the group's own
    targeted rule(s) never cleared, or if the fix couldn't even be applied
    (e.g. a rejected type cast).

    A rule that was not_applicable before the fix and fails after was
    REVEALED, not caused - e.g. distribution_drift can't be scored while a
    column is the wrong dtype, so a safe_type_cast that fixes the dtype can
    be the first thing that ever makes an existing drift observable. That is
    not grounds to revert: the fix is kept, and every such reveal comes back
    on `RepairResult.revealed_failures` for the caller to raise as its own
    validation event. Newly-applicable rules are re-evaluated exactly ONCE,
    against the post-fix frame - this never loops to convergence."""
    engine = engine or ValidationEngine()

    outcome = apply_action(contract.data, action, spec, baseline_profile=baseline_profile)
    if not outcome.success:
        return RepairResult(verified=False, new_df=contract.data, reversal_record=None, error=outcome.error)

    # Computed against the frame exactly as attempt_fix received it - the
    # "before" state for THIS fix, regardless of how many earlier fixes in
    # the same run already touched it.
    pre_fix_status: dict[str, RuleStatus] = {o.rule_id: o.status for o in engine.evaluate(contract, baseline_profile)}

    repaired_contract = DataContract(
        data=outcome.new_df,
        source_type=contract.source_type,
        source_id=contract.source_id,
        connector_metadata=contract.connector_metadata,
        detected_encoding=contract.detected_encoding,
        encoding_confidence=contract.encoding_confidence,
    )
    post_fix_outcomes = engine.evaluate(repaired_contract, baseline_profile)
    post_fix_status: dict[str, RuleStatus] = {o.rule_id: o.status for o in post_fix_outcomes}

    original_rules = {m.rule_failed for m in group.members}
    unresolved = sorted(rule_id for rule_id in original_rules if post_fix_status.get(rule_id) == RuleStatus.FAILED)

    regressed: list[str] = []
    revealed: list[RuleOutcome] = []
    for outcome_row in post_fix_outcomes:
        if outcome_row.status != RuleStatus.FAILED or outcome_row.rule_id in original_rules:
            continue
        pre_status = pre_fix_status.get(outcome_row.rule_id, RuleStatus.NOT_APPLICABLE)
        if pre_status == RuleStatus.PASSED:
            regressed.append(outcome_row.rule_id)
        elif pre_status == RuleStatus.NOT_APPLICABLE:
            # pre_status == FAILED (a pre-existing, unrelated failure this
            # fix never touched) is deliberately excluded from both buckets:
            # it was already known and already has its own validation_event
            # from the run's initial full validate() pass.
            revealed.append(outcome_row)
    regressed.sort()

    if unresolved or regressed:
        reverted_df = revert_action(outcome.new_df, outcome.reversal_record)
        error_parts = []
        if unresolved:
            error_parts.append(f"still firing after fix: {unresolved}")
        if regressed:
            resolved = sorted(rule_id for rule_id in original_rules if post_fix_status.get(rule_id) != RuleStatus.FAILED)
            error_parts.append(
                f"fix regressed previously-passing rule(s): {regressed} "
                f"(originally-targeted rule(s) it did resolve: {resolved})"
            )
        return RepairResult(
            verified=False,
            new_df=reverted_df,
            reversal_record=outcome.reversal_record,
            error="post-condition failed: " + "; ".join(error_parts),
            new_failures=regressed,
        )

    return RepairResult(verified=True, new_df=outcome.new_df, reversal_record=outcome.reversal_record, revealed_failures=revealed)


def apply_fix_chain(df: pd.DataFrame, fix_chain: list[dict] | None, baseline_profile: dict | None = None) -> pd.DataFrame:
    """Replays a run's committed fixes, in order, against a freshly-fetched
    frame. This IS what "the repaired frame" means for any later consumer -
    derived on demand from source + recipe, never stored as a duplicate copy."""
    working = df
    for entry in fix_chain or []:
        action = FixAction(entry["action"])
        outcome = apply_action(working, action, entry["spec"], baseline_profile=baseline_profile)
        if not outcome.success:
            raise ValueError(f"fix_chain replay failed at {entry!r}: {outcome.error}")
        working = outcome.new_df
    return working


def base_contract_for_run(run: Run, source: DataSource, connector: BaseConnector) -> DataContract:
    """The frame run.fix_chain gets replayed against - shared by
    app/routers/approvals.py and app/query/pipeline.py (Phase 6) rather
    than each re-deriving it.

    If this run paused against a mutable source (run.snapshot_path is set -
    only ever true when connector.source_immutable was False at ingest time),
    replay uses the snapshot taken at ingest, never a live re-fetch: the
    source may have changed since, and re-fetching would silently verify (or
    answer a question against) data the diagnosis never saw. Immutable
    sources (snapshot_path always None for them) fetch fresh, which is safe
    only because re-fetching them is guaranteed reproducible.
    """
    if run.snapshot_path:
        return DataContract(data=load_snapshot(run.snapshot_path), source_type=SourceType(source.type), source_id=source.id)
    return connector.fetch()


def repaired_contract_for_run(run: Run, source: DataSource, connector: BaseConnector, baseline_profile: dict | None) -> DataContract:
    """base_contract_for_run + fix_chain replay in one call - "the repaired
    frame" for a specific run, end to end. Used wherever a later phase
    needs the same data validation/repair actually committed to, never a
    fresh, unrepaired fetch (app/exploration/pipeline.py's callers,
    app/narrative/pipeline.py's callers, and app/query/pipeline.py all rely
    on this being the SAME frame)."""
    base = base_contract_for_run(run, source, connector)
    repaired = apply_fix_chain(base.data, run.fix_chain, baseline_profile=baseline_profile)
    return DataContract(
        data=repaired,
        source_type=base.source_type,
        source_id=base.source_id,
        connector_metadata=base.connector_metadata,
        detected_encoding=base.detected_encoding,
        encoding_confidence=base.encoding_confidence,
    )
