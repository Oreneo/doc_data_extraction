"""
Normalization helpers for LLM-extracted field values.

These act as a safety net: the extraction prompt already instructs the LLM
to output dates as mm-dd-yyyy and payment terms as "Net xx", but LLM format
compliance isn't 100%, so these re-derive the target format from whatever
text actually came back instead of trusting it blindly.
"""

import re
from typing import Optional

from dateutil import parser as date_parser

DATE_OUTPUT_FORMAT = "%m-%d-%Y"
# Dates are stored in the database in ISO 8601 instead, because mm-dd-yyyy
# held as TEXT sorts and compares incorrectly in SQL ("03-01-2025" sorts
# before "12-01-2024"), which breaks the range queries a contracts table
# exists to answer. The assignment's mm-dd-yyyy format is preserved on the
# model / extraction output.
ISO_DATE_FORMAT = "%Y-%m-%d"

_NET_TERMS_PATTERN = re.compile(r"^\s*net\s*(\d+)\s*$", re.IGNORECASE)
# Tolerates a parenthesized digit form immediately before "days", e.g.
# "thirty (30) days" - the closing paren sits between the digits and "days".
_DAYS_PATTERN = re.compile(r"(\d+)\)?\s*(?:calendar\s+)?days?", re.IGNORECASE)


def normalize_date(value: Optional[str]) -> Optional[str]:
    """
    Reparse a date string in whatever format it arrived in and reformat it
    as mm-dd-yyyy. Falls back to the original value if it can't be parsed,
    rather than raising - a slightly malformed date shouldn't fail the
    whole extraction.

    Args:
        value: Raw date string from the LLM, or None.

    Returns:
        Optional[str]: The date as mm-dd-yyyy, or the original value if
            parsing failed, or None if `value` was falsy.
    """
    if not value:
        return None

    try:
        parsed = date_parser.parse(str(value))
    except (ValueError, OverflowError):
        return value

    return parsed.strftime(DATE_OUTPUT_FORMAT)


def to_iso_date(value: Optional[str]) -> Optional[str]:
    """
    Convert a date string to ISO 8601 (YYYY-MM-DD) for storage in the
    database, where lexical ordering has to match chronological ordering.

    Values already normalized to mm-dd-yyyy by `normalize_date` are the
    expected input, but any parseable format is accepted. Falls back to the
    original value if it can't be parsed, matching `normalize_date`'s
    behaviour of never failing an extraction over a malformed date.

    Args:
        value: Date string, or None.

    Returns:
        Optional[str]: The date as YYYY-MM-DD, or the original value if
            parsing failed, or None if `value` was falsy.
    """
    if not value:
        return None

    try:
        # Explicit month-first parsing: the model's format is mm-dd-yyyy, and
        # dateutil would otherwise read an ambiguous value like "03-01-2025"
        # as the 3rd of January under a day-first locale assumption.
        parsed = date_parser.parse(str(value), dayfirst=False)
    except (ValueError, OverflowError):
        return value

    return parsed.strftime(ISO_DATE_FORMAT)


def normalize_payment_terms(value: Optional[str]) -> Optional[str]:
    """
    Normalize payment terms to "Net xx". Passes through values already in
    that shape (just tidying whitespace), derives it from a stated number
    of days in prose (e.g. "within thirty (30) days" -> "Net 30" once the
    LLM has already converted "thirty" to "30"), and otherwise falls back
    to the original value unchanged.

    Args:
        value: Raw payment terms string from the LLM, or None.

    Returns:
        Optional[str]: "Net xx", or the original value if no day count
            could be found, or None if `value` was falsy.
    """
    if not value:
        return None

    net_match = _NET_TERMS_PATTERN.match(value)
    if net_match:
        return f"Net {net_match.group(1)}"

    days_match = _DAYS_PATTERN.search(value)
    if days_match:
        return f"Net {days_match.group(1)}"

    return value
