"""Session Summary settings. No magic numbers at a call site."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.narrative.config import NarrativeConfig


@dataclass(frozen=True)
class SummaryConfig:
    #: The brief says 4-8 short sentences. Both ends are enforced by a
    #: deterministic post-check: a one-sentence summary has dropped most of
    #: what the run found, and a twenty-sentence one is the detailed report
    #: again under a different heading.
    min_sentences: int = 4
    max_sentences: int = 8

    #: Attempts at each stage, matching the Narrative Agent's resilience.
    max_attempts: int = 3

    #: The narrative settings are reused verbatim for number rounding, the
    #: causal lexicon and numeric matching, so the two agents cannot disagree
    #: about what counts as a fabricated number or a causal word.
    narrative: NarrativeConfig = field(default_factory=NarrativeConfig)
