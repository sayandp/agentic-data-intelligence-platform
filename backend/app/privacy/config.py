"""Every threshold and pattern the privacy layer uses.

NO MAGIC NUMBERS ELSEWHERE IN THIS PACKAGE. A detection threshold that is
invisible cannot be audited, and this layer's entire job is to be auditable:
"this column was classified as personal because 98% of its non-null values
matched an email pattern" is the output, not "trust me".

Government ID formats are per-locale and configurable, because there is no
universal one and guessing at another country's format is how a detector
both misses real IDs and fires on ordinary reference numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: A column is classified when at least this fraction of its NON-NULL values
#: match. Deliberately high: a column with a stray email in a free-text note
#: is not an email column, and classifying it as one would redact a column
#: the model legitimately needs while teaching the operator to ignore the
#: privacy report.
DEFAULT_MATCH_THRESHOLD = 0.80

#: Below this many non-null values a column is not classified at all. Three
#: values that happen to look like phone numbers are not evidence.
DEFAULT_MIN_NON_NULL = 5

#: Rows sampled from a column when measuring the match fraction. Detection
#: runs on a 1M-row frame at ingest; testing every value would cost more than
#: the rest of the pipeline. Seeded, so the classification is reproducible.
DEFAULT_SAMPLE_SIZE = 2000
DEFAULT_SAMPLE_SEED = 20260817


#: Government ID patterns, keyed by locale. Each is (name, pattern, needs_check)
#: where `needs_check` names a validator in app/privacy/detectors.py, or None
#: when the pattern alone is sufficient.
#:
#: Only formats specific enough not to collide with ordinary business
#: identifiers are here. A plain 9-digit number is NOT a US SSN for detection
#: purposes - far too many order numbers are nine digits - so only the
#: hyphenated form is matched. That is a deliberate miss in favour of not
#: crying wolf; the confirm-a-column flow exists for the rest.
GOVERNMENT_ID_PATTERNS: dict[str, tuple[str, str]] = {
    "us_ssn": (r"^\d{3}-\d{2}-\d{4}$", "United States Social Security number"),
    "uk_nino": (r"^[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]$", "United Kingdom National Insurance number"),
    "in_pan": (r"^[A-Z]{5}\d{4}[A-Z]$", "India Permanent Account Number"),
    "in_aadhaar": (r"^\d{4}\s?\d{4}\s?\d{4}$", "India Aadhaar number"),
}


@dataclass(frozen=True)
class PrivacyConfig:
    match_threshold: float = DEFAULT_MATCH_THRESHOLD
    min_non_null: int = DEFAULT_MIN_NON_NULL
    sample_size: int = DEFAULT_SAMPLE_SIZE
    sample_seed: int = DEFAULT_SAMPLE_SEED
    #: Which locales' government ID formats to test for. Empty disables the
    #: check entirely rather than defaulting to one country's assumptions.
    government_id_locales: tuple[str, ...] = ("us_ssn", "uk_nino", "in_pan", "in_aadhaar")
    #: Column-name hints that make a column a CANDIDATE for human review.
    #: These never classify on their own - see app/privacy/classification.py
    #: for why a `customer_name` heuristic is refused.
    candidate_name_patterns: dict[str, str] = field(
        default_factory=lambda: {
            "person_name": r"(name|firstname|first_name|lastname|last_name|surname|fullname|full_name|contact)",
            "postal_address": r"(address|addr|street|city|postcode|post_code|zip|zipcode)",
            "free_text": r"(note|notes|comment|comments|description|feedback|message|remark)",
        }
    )

    def as_reported(self) -> dict:
        return {
            "match_threshold": self.match_threshold,
            "min_non_null": self.min_non_null,
            "sample_size": self.sample_size,
            "government_id_locales": list(self.government_id_locales),
        }


def compiled_candidate_patterns(config: PrivacyConfig) -> dict[str, re.Pattern[str]]:
    return {kind: re.compile(pattern, re.I) for kind, pattern in config.candidate_name_patterns.items()}
