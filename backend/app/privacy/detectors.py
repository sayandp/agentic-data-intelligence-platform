"""Deterministic PII value detectors. NO LLM, ever.

Using a model to find personal data would mean sending the data to a third
party in order to find out whether it should be sent to a third party. That
is circular, and it is the reason every detector here is a pattern plus, where
one exists, a checksum.

Each detector answers one question about ONE VALUE: does this look like an
email / a phone number / a payment card. Whether a COLUMN is personal is a
separate decision made in app/privacy/classification.py from the fraction of
values that matched - one stray email in a notes column does not make the
column an email column.
"""

from __future__ import annotations

import re

# An email is the one format specific enough that a pattern is close to
# sufficient. Deliberately not RFC 5322 - that grammar accepts things no real
# address uses and would widen the net for no gain.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")

# International and national forms, with common separators. Requires at least
# 7 digits: shorter runs are extension numbers, quantities or year ranges.
_PHONE_SHAPE = re.compile(r"^\+?[\d\s().\-]{7,22}$")
_DIGITS = re.compile(r"\d")

# Dotted-quad and a conservative IPv6. The IPv4 pattern is range-checked
# below, because `999.999.999.999` matches the shape and is not an address.
_IPV4 = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
_IPV6 = re.compile(r"^(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{1,4}$", re.I)

# Card numbers as written: 13-19 digits, optionally grouped.
_CARD_SHAPE = re.compile(r"^[\d \-]{13,25}$")

# Dates match the phone shape almost exactly - `2026-01-01` is eight digits
# with separators - and a date column classified as a phone number would be
# redacted, destroying every trend prompt in the system. Found on a real ads
# export whose `Reporting starts` column was classified HIGH-confidence phone.
_DATE_LIKE = re.compile(
    r"""^(
        \d{4}[-/.]\d{1,2}[-/.]\d{1,2}      # 2026-01-01, 2026/1/1
        | \d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}  # 01-01-2026, 1/1/26
    )([ T].*)?$""",
    re.X,
)


def is_email(value: str) -> bool:
    return bool(_EMAIL.match(value.strip()))


def is_phone_number(value: str) -> bool:
    """Shape plus a digit count. Deliberately conservative.

    A phone number and a plain long integer are not distinguishable by
    pattern alone, so a bare run of digits with no separator and no country
    prefix is NOT treated as a phone number - that would classify every order
    id in the system. A separator or a leading + is required.
    """
    candidate = value.strip()
    # A date is not a phone number, however similar the shape.
    if _DATE_LIKE.match(candidate):
        return False
    if not _PHONE_SHAPE.match(candidate):
        return False
    digits = _DIGITS.findall(candidate)
    if not 7 <= len(digits) <= 15:
        return False
    # Something other than digits must be present, or it is just a number.
    return candidate.startswith("+") or bool(re.search(r"[\s().\-]", candidate))


def luhn_valid(digits: str) -> bool:
    """The Luhn checksum. This is what separates a payment card from a
    16-digit order id - roughly 90% of arbitrary 16-digit numbers fail it."""
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def is_payment_card(value: str) -> bool:
    """Shape AND checksum. The checksum is the whole point: without it this
    detector would classify order ids, and a system that redacts order ids
    teaches its operator to disable it."""
    candidate = value.strip()
    if not _CARD_SHAPE.match(candidate):
        return False
    return luhn_valid(re.sub(r"[ \-]", "", candidate))


def is_ip_address(value: str) -> bool:
    candidate = value.strip()
    match = _IPV4.match(candidate)
    if match:
        return all(0 <= int(octet) <= 255 for octet in match.groups())
    return bool(_IPV6.match(candidate))


def government_id_matcher(pattern: str):
    compiled = re.compile(pattern, re.I)

    def matches(value: str) -> bool:
        return bool(compiled.match(value.strip()))

    return matches


#: The high-confidence detectors, by PII kind. Every one is pattern-verifiable
#: on its own; nothing here needs a human to confirm it.
HIGH_CONFIDENCE_DETECTORS = {
    "email": is_email,
    "payment_card": is_payment_card,
    "ip_address": is_ip_address,
    "phone_number": is_phone_number,
}
