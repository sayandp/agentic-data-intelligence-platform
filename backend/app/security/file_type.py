"""Does the file's CONTENT match the type its name claims?

An extension is a claim made by whoever named the file. The bytes are the
evidence. When they disagree the honest response is to refuse, because every
downstream reader - pandas, openpyxl, the encoding sniffer - is about to act
on the claim.

This is not virus scanning and does not pretend to be. It catches the case
that actually happens: a spreadsheet exported as `.csv` but written as real
XLSX, a `.xlsx` that is really a CSV someone renamed, a binary that is
neither. Each of those currently produces a pandas traceback deep inside a
connector, which reads as "the tool is broken" rather than "this file is not
what it says it is".

WHAT IT DOES NOT DO. A file whose content matches its extension is accepted,
whatever is inside it. A CSV is arbitrary text by definition and this makes no
judgement about that text - the formula-injection question belongs to whatever
opens the export, and is stated in SECURITY.md rather than papered over here.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The first bytes of the container formats we accept or must reject.
_ZIP_MAGIC = b"PK\x03\x04"  # xlsx/xlsm are ZIP archives
_ZIP_EMPTY = b"PK\x05\x06"  # an empty archive, still a ZIP
_ZIP_SPANNED = b"PK\x07\x08"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .xls

_ZIP_PREFIXES = (_ZIP_MAGIC, _ZIP_EMPTY, _ZIP_SPANNED)

#: How much of the file to look at. The magic numbers live in the first few
#: bytes; the rest of this window is used to decide whether a claimed CSV is
#: plausibly text.
SNIFF_BYTES = 8192


@dataclass(frozen=True)
class TypeMismatch:
    """Why a file was refused. Carries what was claimed and what was found,
    because "invalid file" tells the person nothing they can act on."""

    claimed: str
    detected: str
    detail: str

    def message(self) -> str:
        return (
            f"file content does not match its {self.claimed} extension: {self.detail}. "
            f"It looks like {self.detected}. Rename it to its real type, or re-export it "
            f"as {self.claimed}."
        )


def _looks_like_text(head: bytes) -> bool:
    """A NUL byte is the practical separator between text and binary.

    Deliberately not a decode attempt: this system reads CSVs in many
    encodings (the connector sniffs them), so refusing anything that is not
    valid UTF-8 would reject files it handles correctly today. A NUL byte, by
    contrast, appears in no text encoding this reads except UTF-16 - which is
    handled below by its BOM.
    """
    return b"\x00" not in head


def _is_utf16(head: bytes) -> bool:
    return head.startswith((b"\xff\xfe", b"\xfe\xff"))


def detect_mismatch(head: bytes, extension: str) -> TypeMismatch | None:
    """None when the content is consistent with the extension.

    `head` is the first SNIFF_BYTES of the file; `extension` is lowercased and
    includes the dot.
    """
    extension = extension.lower()

    if extension == ".csv":
        if head.startswith(_ZIP_PREFIXES):
            return TypeMismatch(
                claimed=".csv",
                detected="an Excel workbook (.xlsx) or another ZIP archive",
                detail="the file begins with a ZIP archive header",
            )
        if head.startswith(_OLE2_MAGIC):
            return TypeMismatch(
                claimed=".csv",
                detected="a legacy Excel workbook (.xls)",
                detail="the file begins with an OLE2 compound-document header",
            )
        if _is_utf16(head):
            return None  # a UTF-16 CSV is still a CSV; the connector sniffs encodings
        if not _looks_like_text(head):
            return TypeMismatch(
                claimed=".csv",
                detected="a binary file",
                detail="the first bytes contain NUL, which no text encoding this reads produces",
            )
        return None

    if extension in {".xlsx", ".xlsm"}:
        if head.startswith(_OLE2_MAGIC):
            return TypeMismatch(
                claimed=extension,
                detected="a legacy Excel workbook (.xls)",
                detail="the file begins with an OLE2 compound-document header, not a ZIP header",
            )
        if not head.startswith(_ZIP_PREFIXES):
            detected = "a text file, possibly a CSV" if _looks_like_text(head) else "an unrecognised binary file"
            return TypeMismatch(
                claimed=extension,
                detected=detected,
                detail="an .xlsx workbook is a ZIP archive and this file has no ZIP header",
            )
        return None

    if extension == ".xls":
        if head.startswith(_ZIP_PREFIXES):
            # A modern .xlsx renamed to .xls. openpyxl and xlrd will each
            # refuse it for different, unhelpful reasons.
            return TypeMismatch(
                claimed=".xls",
                detected="a modern Excel workbook (.xlsx)",
                detail="the file begins with a ZIP header, which legacy .xls files do not have",
            )
        if not head.startswith(_OLE2_MAGIC):
            detected = "a text file, possibly a CSV" if _looks_like_text(head) else "an unrecognised binary file"
            return TypeMismatch(
                claimed=".xls",
                detected=detected,
                detail="a legacy .xls workbook begins with an OLE2 compound-document header and this file does not",
            )
        return None

    # An extension this module has no opinion about. Saying nothing is correct:
    # the upload allowlist is what decides which extensions are accepted at
    # all, and duplicating that decision here would be a second gate to keep
    # in sync with the first.
    return None
