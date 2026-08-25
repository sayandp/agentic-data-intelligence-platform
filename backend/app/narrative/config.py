"""Every threshold, cap, and the banned-causal-language lexicon in one
place, mirroring app/exploration/config.py's constructor-configurable
pattern."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.exploration.config import DEFAULT_MODALITY_BINS

# Part 2.2: exact terms from the phase brief, plus the inflections a model
# could reach for without ever typing the listed word itself (e.g.
# "drivers" instead of "driver"). Matched case-insensitively, on word
# boundaries, against the generated prose - never against claim_text or
# finding payloads, which structurally cannot contain these (the
# Exploration Agent's vocabulary has no causal words to draw from at all;
# see app/exploration/findings.py).
DEFAULT_CAUSAL_LEXICON: tuple[str, ...] = (
    # -- asserting a cause directly --
    r"\bcaus(e|es|ed|ing|al)\b",
    # -- one thing producing another --
    r"\bdriv(e|es|en|ing|er|ers)\b",
    r"\bdrove\b",
    r"\blead(s|ing)?\s+to\b",
    r"\bled\s+to\b",
    r"\bresults?\s+in\b",
    r"\bresult(ed|ing)\s+in\b",
    r"\bstem(s|med|ming)?\s+from\b",
    r"\baris(e|es|ing)\s+from\b",
    r"\barose\s+from\b",
    # -- attributing an outcome to a source --
    r"\bdue\s+to\b",
    r"\bowing\s+to\b",
    r"\bthanks\s+to\b",
    r"\bresponsible\s+for\b",
    r"\battributable\s+to\b",
    r"\battributed\s+to\b",
    # -- bare "because" and its variants. `because of` alone left the single
    #    most common causal connective in English uncaught: "sales fell
    #    because the feed broke" cleared this check from Phase 5 until a live
    #    Session Summary produced one and it was noticed in an aurora E2E run.
    #    The bare form subsumes "because of", so that entry is gone rather
    #    than kept as a narrower duplicate.
    r"\bbecause\b",
    # -- inferential connectives. Each asserts that one fact FOLLOWS from
    #    another, which is the claim this check exists to prevent even when
    #    no cause is named outright.
    r"\bas\s+a\s+result\b",
    r"\bconsequently\b",
    r"\btherefore\b",
    r"\bhence\b",
    r"\bthus\b",
    # -- "the reason ..." REQUIRES its causal continuation. A bare
    #    `\bthe reason\b` would flag a dataset with a column literally named
    #    `reason` the moment prose mentioned it ("the reason column has 3
    #    nulls"), which is a description, not a claim.
    r"\bthe\s+reason\s+(for|why|behind|that)\b",
    # -- effect and influence --
    r"\bimpact(s|ed|ing)?\b",
    r"\beffects?\b",
    r"\binfluenc(es?|ed|ing)\b",
    r"\bexplain(s|ed|ing)?\b",
)

# DELIBERATELY NOT IN THE LEXICON, with the reason, so a later audit does not
# "complete" it by adding them without measuring first:
#
#   affect/affects/affected - "the affected column" and "affected rows" are
#     this system's own descriptive vocabulary for what a validation event
#     touched. "X affects Y" is causal and "the affected rows" is not, and no
#     word-boundary pattern separates them. Left out rather than guessed at.
#
#   correlat* - explicitly permitted below. A correlation is what this system
#     measures; banning the word would ban the finding.
NOT_BANNED_WITH_REASON: tuple[str, ...] = ("affect", "affects", "affected", "correlates")

# Phrasing the brief explicitly permits - never flagged even though some
# share a root with a banned word (e.g. "correlates" is fine; "influences"
# is not). Not used to suppress a match; documented so the lexicon above is
# never "tightened" to accidentally catch these.
PERMITTED_PHRASES: tuple[str, ...] = ("is associated with", "correlates with", "moves together with")

DEFAULT_MAX_REGENERATION_ATTEMPTS = 1  # Part 2: ONE regeneration attempt, then the template

# Part 2.1: formatting variants a numeral in prose might take relative to
# a claim's stored value (0.31 stated as "31%", trailing zeros, etc).
NUMERIC_MATCH_DECIMALS = 6

# Dashboard UX pass, Part 3 (NUMBER PRECISION): a raw finding value like
# 61.271834912 makes for an unreadable narrative - app/narrative/grounding.py
# rounds every GroundedClaim value to this many decimal places at stage-1
# grounding time (a value that's already whole - a count, cardinality,
# sample_size, ... - stays whole regardless of this setting; see
# app/narrative/grounding.py::_round_claim_value). Rounding happens on the
# CLAIM, not by loosening app/narrative/postchecks.py's number_fidelity
# comparison - the ground-truth set that check compares against is exactly
# as strict as before, it just now contains the rounded number instead of
# the raw one.
CLAIM_VALUE_ROUND_DECIMALS = 2

# Part 3: charts
DEFAULT_MAX_CHARTS = 20

# Dashboard UX pass, Part 4: an integer-dtype numeric column with at most
# this many distinct values (a 1-5 star rating, a 1-10 satisfaction score,
# ...) is a discrete/ordinal quantity, not a continuous one - a histogram
# with fractional bin edges misrepresents it (see app/narrative/charts.py).
# Reused from app.exploration.config.DEFAULT_MODALITY_BINS rather than a
# second, independently-tunable "how many distinct values before this stops
# looking like a small fixed set" threshold - it's the same signal
# app/exploration/distribution.py already uses to decide modality bins for
# effectively the same kind of column.
DEFAULT_DISCRETE_BAR_MAX_CARDINALITY = DEFAULT_MODALITY_BINS

# Part 1: LLM prompts never see raw row-level data (findings are already
# aggregated), but category values / column names inside a finding payload
# still originate from the source data, so the same untrusted-data
# delimiting discipline app/diagnosis/agent.py applies to sample rows
# applies here to the serialized findings/claims block.
SAMPLE_START_MARKER = "<<<NARRATIVE_INPUT_START>>>"
SAMPLE_END_MARKER = "<<<NARRATIVE_INPUT_END>>>"


@dataclass
class NarrativeConfig:
    causal_lexicon: tuple[str, ...] = DEFAULT_CAUSAL_LEXICON
    max_regeneration_attempts: int = DEFAULT_MAX_REGENERATION_ATTEMPTS
    numeric_match_decimals: int = NUMERIC_MATCH_DECIMALS
    max_charts: int = DEFAULT_MAX_CHARTS
    claim_value_round_decimals: int = CLAIM_VALUE_ROUND_DECIMALS
    discrete_bar_max_cardinality: int = DEFAULT_DISCRETE_BAR_MAX_CARDINALITY
