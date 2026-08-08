"""Market basket analysis - Apriori over transaction/item pairs.

Reports support, confidence and lift, filtered to lift > 1 (a pair that
co-occurs no more often than independence predicts is not a finding). Itemset
size and rule count are capped so a dense catalogue cannot produce a
combinatorial explosion.

Skips ENTIRELY, with a reason, when transactions are mostly single-item -
Apriori on baskets of one produces nothing, and reporting "no rules found"
would suggest the analysis ran and found nothing interesting rather than that
it was never eligible.

NO LLM. NO CAUSAL VOCABULARY: a rule is an ASSOCIATION. Lift > 1 says two
items co-occur more often than chance, never that buying one brings about
the other.
"""

from __future__ import annotations

import pandas as pd

from app.analytics.applicability import AnalysisKind
from app.analytics.findings import (
    AnalysisEvidence,
    AnalysisFinding,
    AnalysisFindingType,
    AssociationRulePayload,
    BusinessAnalysisResult,
)
from app.analytics.roles import ColumnRole, RoleDetection

DEFAULT_MIN_SUPPORT = 0.02
DEFAULT_MIN_CONFIDENCE = 0.2

#: Pairs and triples. Longer itemsets are exponentially more numerous and
#: essentially never actionable.
MAX_ITEMSET_SIZE = 3
MAX_RULES = 50

#: Below this mean basket size there is nothing to co-occur.
MIN_MEAN_BASKET_SIZE = 1.2

#: Apriori's one-hot matrix is transactions x items; a huge catalogue makes
#: it enormous. Restrict to the most frequent items and say so.
MAX_ITEMS = 200


def run_market_basket(
    df: pd.DataFrame,
    detection: RoleDetection,
    min_support: float = DEFAULT_MIN_SUPPORT,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> BusinessAnalysisResult:
    analysis = AnalysisKind.MARKET_BASKET.value
    transaction = detection.best(ColumnRole.TRANSACTION_ID)
    item = detection.best(ColumnRole.ITEM_ID)

    if not (0 < min_support < 1) or not (0 < min_confidence <= 1):
        raise ValueError(f"min_support must be in (0,1) and min_confidence in (0,1]; got {min_support}, {min_confidence}")

    missing = [
        label
        for label, found in (("a transaction/order identifier", transaction), ("a line-item identifier", item))
        if found is None
    ]
    if missing:
        return BusinessAnalysisResult(
            analysis=analysis, ran=False, not_run_reason=f"needs {', '.join(missing)}; not detected"
        )

    txn_col, item_col = transaction.column, item.column
    frame = df[[txn_col, item_col]].dropna().drop_duplicates()
    if frame.empty:
        return BusinessAnalysisResult(analysis=analysis, ran=False, not_run_reason="no rows with both a transaction and an item")

    basket_sizes = frame.groupby(txn_col, observed=True)[item_col].nunique()
    mean_basket = float(basket_sizes.mean())
    transaction_count = int(len(basket_sizes))

    if mean_basket < MIN_MEAN_BASKET_SIZE:
        multi = int((basket_sizes >= 2).sum())
        return BusinessAnalysisResult(
            analysis=analysis,
            ran=False,
            not_run_reason=(
                f"transactions are mostly single-item (mean basket size {mean_basket:.2f}; "
                f"{multi} of {transaction_count} transactions have 2+ items) - "
                "there are no co-occurrences to find"
            ),
            parameters={"mean_basket_size": round(mean_basket, 4), "transaction_count": transaction_count},
        )

    top_items = frame[item_col].value_counts().head(MAX_ITEMS).index
    truncated_items = int(frame[item_col].nunique()) > len(top_items)
    frame = frame[frame[item_col].isin(top_items)]

    onehot = (
        frame.assign(_present=True)
        .pivot_table(index=txn_col, columns=item_col, values="_present", fill_value=False, aggfunc="first")
        .astype(bool)
    )

    from mlxtend.frequent_patterns import apriori, association_rules

    itemsets = apriori(onehot, min_support=min_support, use_colnames=True, max_len=MAX_ITEMSET_SIZE)
    if itemsets.empty:
        return BusinessAnalysisResult(
            analysis=analysis,
            ran=False,
            not_run_reason=f"no itemset reached the {min_support:.0%} minimum support",
            parameters={"min_support": min_support, "transaction_count": transaction_count},
        )

    rules = association_rules(itemsets, metric="confidence", min_threshold=min_confidence)
    if rules.empty:
        return BusinessAnalysisResult(
            analysis=analysis,
            ran=False,
            not_run_reason=f"no rule reached the {min_confidence:.0%} minimum confidence",
            parameters={"min_support": min_support, "min_confidence": min_confidence},
        )

    # Lift > 1 only. A rule at lift <= 1 describes a pair that co-occurs no
    # more often than independence predicts - reporting it as an association
    # would be reporting noise.
    rules = rules[rules["lift"] > 1.0]
    if rules.empty:
        return BusinessAnalysisResult(
            analysis=analysis,
            ran=False,
            not_run_reason="no rule had lift above 1 - no pair co-occurs more often than independence predicts",
            parameters={"min_support": min_support, "min_confidence": min_confidence, "lift_floor": 1.0},
        )

    rules = rules.sort_values(["lift", "confidence", "support"], ascending=False, kind="mergesort").head(MAX_RULES)

    findings = [
        AnalysisFinding(
            analysis=analysis,
            finding_type=AnalysisFindingType.ASSOCIATION_RULE,
            columns=[txn_col, item_col],
            payload=AssociationRulePayload(
                antecedent=sorted(str(x) for x in row.antecedents),
                consequent=sorted(str(x) for x in row.consequents),
                support=round(float(row.support), 6),
                confidence=round(float(row.confidence), 6),
                lift=round(float(row.lift), 6),
                transaction_count=transaction_count,
            ),
            evidence=AnalysisEvidence(
                sample_size=int(len(frame)),
                entity_count=transaction_count,
                parameters={"min_support": min_support, "min_confidence": min_confidence, "lift_floor": 1.0},
            ),
        )
        for row in rules.itertuples()
    ]

    return BusinessAnalysisResult(
        analysis=analysis,
        ran=True,
        findings=findings,
        parameters={
            "transaction_column": txn_col,
            "item_column": item_col,
            "min_support": min_support,
            "min_confidence": min_confidence,
            "lift_floor": 1.0,
            "max_itemset_size": MAX_ITEMSET_SIZE,
            "rule_cap": MAX_RULES,
            "transaction_count": transaction_count,
            "mean_basket_size": round(mean_basket, 4),
            "items_truncated_to": len(top_items) if truncated_items else None,
        },
    )
