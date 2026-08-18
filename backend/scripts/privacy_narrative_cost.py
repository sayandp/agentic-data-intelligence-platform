"""What does redacting a free-text column cost the NARRATIVE?

Diagnosis lost nothing to redaction (measured: 3/3 identical cause and
confidence). Narrative is the opposite case by construction - it exists to
say things ABOUT the data, and a Pareto band's top entities or a basket
rule's item names ARE the content. If Description becomes <TEXT_1>, the
report may still be accurate and useless.

Runs the real two-stage narrative agent against a live model, twice, on the
same Online Retail II-shaped findings: once with Description redacted and
once clear. Compares what the claims and prose actually say.
"""

import json

import numpy as np
import pandas as pd

from app.exploration.engine import ExplorationEngine
from app.exploration.findings import DataQualityContext
from app.llm.factory import get_llm_client
from app.narrative.agent import NarrativeAgent
from app.privacy.classification import (
    ColumnClassification,
    PIIConfidence,
    PIIKind,
    PrivacyClassification,
)

rng = np.random.default_rng(5)
N = 500

PRODUCTS = [
    "WHITE HANGING HEART T-LIGHT HOLDER",
    "REGENCY CAKESTAND 3 TIER",
    "JUMBO BAG RED RETROSPOT",
    "PARTY BUNTING",
    "ASSORTED COLOUR BIRD ORNAMENT",
]
COUNTRIES = ["United Kingdom", "France", "Germany", "EIRE"]


def retail_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Invoice": [f"5{36000 + i // 4}" for i in range(N)],
            "Description": rng.choice(PRODUCTS, N, p=[0.4, 0.25, 0.15, 0.12, 0.08]),
            "Quantity": rng.integers(1, 40, N),
            "InvoiceDate": pd.date_range("2010-01-01", periods=N, freq="h"),
            "Price": np.round(rng.uniform(0.5, 20, N), 2),
            "Country": rng.choice(COUNTRIES, N, p=[0.7, 0.12, 0.1, 0.08]),
        }
    )


def description_redacted() -> PrivacyClassification:
    """Description marked personal, as default-deny on a free-text candidate
    would mark it."""
    return PrivacyClassification(
        classifications=[
            ColumnClassification(
                column="Description",
                kind=PIIKind.FREE_TEXT,
                confidence=PIIConfidence.CONFIRMED,
                matched_fraction=None,
                reason="free-text candidate, redacted by default-deny policy",
            )
        ]
    )


frame = retail_frame()
findings = ExplorationEngine().run(frame, run_id="narrative-cost", data_quality_context=DataQualityContext(total_events=0))

# ANALYTICS findings are where redaction bites hardest: a Pareto band's
# top_entities and a basket rule's item names ARE the content. "Band A is led
# by <TEXT_1>" is accurate and useless.
from app.analytics.engine import run_business_analytics
analytics = run_business_analytics("narrative-cost", frame, {"monetary": "Price", "item_id": "Description"})
print("analytics findings available:", sum(len(r.findings) for r in analytics.results))
for r in analytics.results:
    for f in r.findings:
        payload = f.payload.model_dump()
        if payload.get("top_entities"):
            print("  sample top_entities:", payload["top_entities"][:3])
            break
    else:
        continue
    break
print()

print(f"provider: {type(get_llm_client()).__name__}\n")

results = {}
for label, privacy in (("CLEAR", None), ("REDACTED", description_redacted())):
    agent = NarrativeAgent(llm_client=get_llm_client(), sleep=lambda _s: None)
    claims_outcome = agent.generate_claims(findings, analytics_findings=analytics, privacy=privacy)
    claims = claims_outcome.claims or []
    prose_outcome = agent.generate_prose(claims) if claims else None
    prose = prose_outcome.prose.report_text if prose_outcome and prose_outcome.prose else ""

    results[label] = {"claims": claims, "prose": prose}

    print(f"{'=' * 74}\n{label}  ({len(claims)} claims, source {claims_outcome.source})\n{'=' * 74}")
    for claim in claims[:6]:
        print(f"  - {claim.claim_text}")
    print(f"\n  PROSE ({len(prose)} chars):\n  {prose[:520]}\n")

# ---- does the redacted narrative still name real products? ----
print("=" * 74)
print("DOES THE NARRATIVE CARRY REAL PRODUCT NAMES?")
print("=" * 74)
for label, payload in results.items():
    text = " ".join(c.claim_text for c in payload["claims"]) + " " + payload["prose"]
    named = [p for p in PRODUCTS if p.lower() in text.lower()]
    tokens = text.count("<TEXT_")
    print(f"  {label:9} real product names present: {len(named)}/{len(PRODUCTS)}  {named[:2]}")
    print(f"  {'':9} redaction tokens in the text: {tokens}")

print()
print("Country is NOT redacted in either run, so a difference in country")
print("coverage would indicate degradation beyond the redacted column itself.")
for label, payload in results.items():
    text = " ".join(c.claim_text for c in payload["claims"]) + " " + payload["prose"]
    named = [c for c in COUNTRIES if c.lower() in text.lower()]
    print(f"  {label:9} country names present: {len(named)}/{len(COUNTRIES)} {named[:3]}")
