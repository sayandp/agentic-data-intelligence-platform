"""Does redaction cost diagnosis accuracy WHEN IT ACTUALLY MASKS SOMETHING?

The corruption harness's own fixture is (id, city, amount) - no PII - so
redaction is a no-op on it and "accuracy unchanged" there is true but empty.

This runs the SAME corruptions over a frame whose columns ARE personal, and
diagnoses each one twice against the live model: once with redaction active
(what ships) and once with it disabled (what the model used to see). Same
corruption, same seed, same prompt structure - the only difference is whether
values are masked.
"""

import os

import numpy as np
import pandas as pd

from app.contract import DataContract, SourceType
from app.correlation import correlate_events
from app.diagnosis.agent import DiagnosticAgent
from app.diagnosis.cache import DiagnosisCache
from app.llm.factory import get_llm_client
from app.privacy.classification import PrivacyClassification, classify_frame
from app.profiling import BaselineProfiler
from app.validation.engine import ValidationEngine
from tests.corruption import CorruptionSuite

rng = np.random.default_rng(11)
N = 120
CARDS = ["4539578763621486", "6011000990139424", "5500005555555559", "4111111111111111"]
FIRST = ["alice", "bruno", "chen", "dara", "elif"]
LAST = ["smith", "okafor", "wei", "novak", "yilmaz"]


def pii_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "email": [f"{rng.choice(FIRST)}.{rng.choice(LAST)}@example.com" for _ in range(N)],
            "phone": [f"+44 20 7{rng.integers(100000, 999999)}" for _ in range(N)],
            "card_number": rng.choice(CARDS, N),
            "client_ip": [f"{rng.integers(1,223)}.{rng.integers(0,255)}.{rng.integers(0,255)}.{rng.integers(1,254)}" for _ in range(N)],
            "amount": np.round(rng.uniform(5, 500, N), 2),
        }
    )


CORRUPTIONS = [
    ("rename_column", {"column": "email", "new_name": "email_address"}),
    ("change_dtype", {}),
    ("inject_nulls", {}),
    ("inject_whitespace_case", {}),
]


def diagnose(corruption: str, kwargs: dict, redact: bool):
    clean = pii_frame()
    corrupted, _truth = CorruptionSuite().apply(clean, corruption, seed=5, **kwargs)

    baseline = BaselineProfiler().profile(clean)
    contract = DataContract(data=corrupted, source_type=SourceType.FILE, source_id="pii-accuracy")
    failures = ValidationEngine().validate(contract, baseline)
    if not failures:
        return corruption, redact, "no failure detected", 0

    groups = correlate_events(failures, baseline, contract)
    classification = classify_frame(corrupted) if redact else PrivacyClassification()

    # A fresh cache per (corruption, mode): the cache key is the failure
    # GROUP, so a redacted and an unredacted prompt would otherwise collide
    # and the second run would replay the first's answer.
    cache = DiagnosisCache(path=f".cache/accuracy_{corruption}_{'on' if redact else 'off'}.json")
    agent = DiagnosticAgent(llm_client=get_llm_client(), cache=cache, sleep=lambda _s: None)

    outcome = agent.diagnose_group(groups[0], baseline, contract, privacy=classification)
    cause = (outcome.diagnosis_json or {}).get("cause_category", "escalated/none")
    confidence = (outcome.diagnosis_json or {}).get("confidence")
    return corruption, redact, f"{cause} (confidence {confidence}, source {outcome.source})", len(groups)


print(f"provider: {type(get_llm_client()).__name__}\n")
print(f"{'corruption':26} {'redaction':10} {'diagnosis'}")
print("-" * 88)
agreements = 0
for name, kwargs in CORRUPTIONS:
    _, _, without, _ = diagnose(name, kwargs, redact=False)
    _, _, with_r, _ = diagnose(name, kwargs, redact=True)
    print(f"{name:26} {'OFF':10} {without}")
    print(f"{'':26} {'ON':10} {with_r}")
    same = without.split(" (")[0] == with_r.split(" (")[0]
    agreements += int(same)
    print(f"{'':26} {'-> ':10} {'SAME cause' if same else 'DIFFERENT cause'}\n")

print(f"cause agreed on {agreements}/{len(CORRUPTIONS)} corruptions with redaction on vs off")
