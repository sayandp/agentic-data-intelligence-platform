"""How much existing output does the widened causal lexicon now reject?

Measures the REAL corpus rather than a regenerated one. Every stored narrative
and session summary in the dev database is re-checked under both the old
lexicon and the new one, and every newly-flagged sentence is printed in full.

Why the stored text rather than a live regeneration: regenerating re-rolls the
model, so it would measure today's sampling rather than the lexicon change.
The stored corpus is what the system actually produced and shipped, and the
rate at which it contains now-banned language is what predicts the future
template-fallback rate.

Run from backend/ with the dev database:
    DATABASE_URL=sqlite:///d:/main_project/backend/local_dev.db \\
      .venv/Scripts/python.exe scripts/measure_causal_lexicon_gap.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Runnable as `python scripts/...` from backend/, like the other scripts here.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The lexicon as it stood from Phase 5 until this change. Kept verbatim here
# so the comparison is against what actually shipped, not a reconstruction.
OLD_LEXICON: tuple[str, ...] = (
    r"\bcause[sd]?\b",
    r"\bcausing\b",
    r"\bdrove\b",
    r"\bdrivers?\b",
    r"\bdrives\b",
    r"\bdriving\b",
    r"\bled to\b",
    r"\bresults?\s+in\b",
    r"\bresulted\s+in\b",
    r"\bresulting\s+in\b",
    r"\bdue to\b",
    r"\bbecause of\b",
    r"\bimpact(s|ed|ing)?\b",
    r"\beffects?\b",
    r"\binfluenc(es?|ed|ing)\b",
    r"\bexplain(s|ed|ing)?\b",
)

_SENTENCE = re.compile(r"[^.!?]+[.!?]?")


def matches(text: str, lexicon: tuple[str, ...]) -> list[str]:
    found = []
    for pattern in lexicon:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            found.append(match.group())
    return found


def flagged_sentences(text: str, lexicon: tuple[str, ...]) -> list[tuple[str, str]]:
    """(sentence, the term that flagged it)."""
    out = []
    for sentence in _SENTENCE.findall(text):
        stripped = sentence.strip()
        if not stripped:
            continue
        hits = matches(stripped, lexicon)
        if hits:
            out.append((stripped, hits[0]))
    return out


def main() -> int:
    os.environ.setdefault("DATABASE_URL", "sqlite:///d:/main_project/backend/local_dev.db")

    from app.db import SessionLocal
    from app.models import Report, SessionSummary
    from app.narrative.config import DEFAULT_CAUSAL_LEXICON

    db = SessionLocal()
    try:
        for label, rows, text_of in (
            ("NARRATIVE", db.query(Report).all(), lambda r: r.narrative_text or ""),
            ("SESSION SUMMARY", db.query(SessionSummary).all(), lambda r: r.summary_text or ""),
        ):
            total = len(rows)
            already = 0
            newly = 0
            examples: list[tuple[str, str]] = []

            for row in rows:
                text = text_of(row)
                if not text.strip():
                    continue
                old_hits = matches(text, OLD_LEXICON)
                new_hits = matches(text, DEFAULT_CAUSAL_LEXICON)
                if old_hits:
                    already += 1
                elif new_hits:
                    newly += 1
                    for sentence, term in flagged_sentences(text, DEFAULT_CAUSAL_LEXICON):
                        if not matches(sentence, OLD_LEXICON):
                            examples.append((term, sentence))

            print(f"\n=== {label} ===")
            print(f"stored rows with text        : {total}")
            print(f"already failing the OLD check: {already}")
            print(f"NEWLY failing under the new  : {newly}"
                  + (f"  ({newly / total:.1%} of stored)" if total else ""))

            if examples:
                print(f"\n  every newly-flagged sentence ({len(examples)}):")
                seen = set()
                for term, sentence in examples:
                    key = (term.lower(), sentence[:80])
                    if key in seen:
                        continue
                    seen.add(key)
                    print(f"    [{term}] {sentence}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
