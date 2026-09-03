"""
BibTeX parsing utilities.

Migrated from ads2bibtex with improvements.
"""

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["extract_cite_keys", "parse_bibtex_entries", "make_rawfile"]


@dataclass
class BibEntry:
    """Parsed BibTeX entry."""

    bibcode: str
    entry_type: str
    authors: list[str]
    title: str
    year: str
    journal: str | None = None
    volume: str | None = None
    pages: str | None = None
    raw: str = ""


def extract_cite_keys(tex_file: str | Path) -> list[str]:
    """
    Extract citation keys from a LaTeX file.

    Parameters
    ----------
    tex_file : str or Path
        Path to the LaTeX file.

    Returns
    -------
    list[str]
        List of unique bibcode-style citation keys (19 characters, year prefix).
    """
    path = Path(tex_file)
    if not path.exists():
        return []

    contents = path.read_text()

    # Match \cite{...}, \citep{...}, \citet{...} but ignore commented lines
    # Also handles multiple keys in single cite command
    rx = re.compile(r"""(?<!\\)%.+|(\\\w*cite\w*\{([^{}]+)\})""")

    keys = []
    for match in rx.finditer(contents):
        if match.group(2):
            key_list = match.group(2).split(",")
            for key in key_list:
                key = key.strip()
                # Check if it looks like a bibcode (19 chars, starts with 4 digits)
                if len(key) == 19 and key[:4].isdigit():
                    if key not in keys:
                        keys.append(key)

    return keys


def parse_bibtex_entries(bibtex_text: str) -> list[BibEntry]:
    """
    Parse BibTeX text into structured entries.

    Parameters
    ----------
    bibtex_text : str
        Raw BibTeX content.

    Returns
    -------
    list[BibEntry]
        Parsed BibTeX entries.
    """
    entries = []

    # Split into individual entries
    entry_pattern = re.compile(r"@(\w+)\{([^,]+),\s*(.*?)\n\}", re.DOTALL)

    for match in entry_pattern.finditer(bibtex_text):
        entry_type = match.group(1).lower()
        bibcode = match.group(2).strip()
        body = match.group(3)

        # Extract fields
        def get_field(name: str) -> str | None:
            pattern = rf"{name}\s*=\s*\{{(.+?)\}}"
            field_match = re.search(pattern, body, re.IGNORECASE)
            return field_match.group(1) if field_match else None

        # Parse authors
        author_str = get_field("author") or ""
        authors = []
        if author_str:
            # Extract last names from {LastName} pattern
            authors = re.findall(r"\{([^{}]+)\}", author_str)

        entry = BibEntry(
            bibcode=bibcode,
            entry_type=entry_type,
            authors=authors,
            title=get_field("title") or "",
            year=get_field("year") or "",
            journal=get_field("journal"),
            volume=get_field("volume"),
            pages=get_field("pages"),
            raw=match.group(0),
        )
        entries.append(entry)

    return entries


def make_rawfile(bibtex_text: str, output_file: str | Path) -> None:
    """
    Create a summary file from BibTeX.

    Generates a file with one line per entry:
    bibcode  # author_summary || title

    Parameters
    ----------
    bibtex_text : str
        BibTeX content.
    output_file : str or Path
        Output file path.
    """
    lines = []

    for line in bibtex_text.split("\n"):
        line = line.strip()

        # Start collecting entry info
        if line.startswith("@"):
            match = re.match(r"@\w+\{([^,]+),", line)
            if match:
                current_bib = match.group(1)
                current_auth = ""
                current_title = ""

        elif line.startswith("author ="):
            auth_line = line.split(" = {")[1].rstrip(",").rstrip("}")
            # Extract last names
            lasts = re.findall(r"\{([^{}]+)\}", auth_line)
            if len(lasts) > 3:
                current_auth = f"{lasts[0]}+{len(lasts) - 1}"
            elif len(lasts) > 1:
                current_auth = "+".join(lasts)
            elif lasts:
                current_auth = lasts[0]

        elif line.startswith("title ="):
            current_title = line.split("{")[1].rstrip("},").rstrip("}")

        elif line == "}":
            if len(current_bib) == 19:
                lines.append(f"{current_bib}  # {current_auth} || {current_title}")

    Path(output_file).write_text("\n".join(lines))
