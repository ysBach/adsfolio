"""
Journal name formatting utilities.

Migrated from ads2bibtex with improvements for integration.
"""

import re
from typing import Literal

from ..journal_utils import JOURNAL_MACRO, format_journal

__all__ = ["JOURNAL_MACRO", "expand_macros", "iso4_abbreviate", "change_journal_name"]


def expand_macros(bibtex_text: str) -> str:
    """
    Expand LaTeX journal macros to full names.

    Parameters
    ----------
    bibtex_text : str
        BibTeX content with macros like \\aj.

    Returns
    -------
    str
        BibTeX with expanded journal names.

    Examples
    --------
    >>> expand_macros("journal = {\\aj}")
    'journal = {Astronomical Journal}'
    """
    result = bibtex_text
    for macro, fullname in JOURNAL_MACRO.items():
        # Match \macro pattern
        result = re.sub(rf"\\{macro}(?![a-z])", fullname, result, flags=re.IGNORECASE)
    return result


def iso4_abbreviate(text: str, periods: bool = True) -> str:
    """
    Apply ISO4 abbreviations to journal name.

    Delegates to journal_utils.format_journal.

    Parameters
    ----------
    text : str
        Full journal name.
    periods : bool, optional
        Ignored (handled by cached/standard logic).

    Returns
    -------
    str
        ISO4 abbreviated journal name.
    """
    return format_journal(text, fmt="iso4", latex=False)


def _iso4fy_journals(bibtex_text: str) -> str:
    """Apply ISO4 abbreviations to journal fields in BibTeX."""
    lines = bibtex_text.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("journal"):
            # Extract journal name
            match = re.search(r"\{(.+?)\}", line)
            if match:
                fullname = match.group(1)
                # Use format_journal (cached logic)
                abbrname = format_journal(fullname, fmt="iso4", latex=False)
                lines[i] = line.replace(fullname, abbrname)
    return "\n".join(lines)


def change_journal_name(
    bibtex_text: str,
    format: Literal["ads", "full", "iso4"] = "ads",
) -> str:
    """
    Change journal name format in BibTeX.

    Parameters
    ----------
    bibtex_text : str
        BibTeX content.
    format : str, optional
        Output format:
        - "ads": Keep ADS output (with macros like \\aj)
        - "full": Expand to full journal names
        - "iso4": Use ISO4 abbreviations

    Returns
    -------
    str
        BibTeX with modified journal names.
    """
    if format == "ads":
        return bibtex_text
    elif format == "full":
        return expand_macros(bibtex_text)
    elif format == "iso4":
        return _iso4fy_journals(expand_macros(bibtex_text))
    else:
        raise ValueError(f"Unknown journal format: {format}")
