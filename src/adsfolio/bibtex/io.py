"""
File I/O utilities for bibcode and BibTeX files.

"""

import re
from pathlib import Path

__all__ = ["read_bibcodes_file", "read_bibtex_file"]


def read_bibcodes_file(
    file_path: str | Path,
    sort: bool = False,
) -> list[str]:
    """
    Read bibcodes from a text file.

    Handles:
    - Comments (# or %)
    - Comma-separated bibcodes
    - Whitespace and empty lines

    Parameters
    ----------
    file_path : str or Path
        Path to the bibcode file.
    sort : bool, optional
        Whether to sort the bibcodes. Default: False.

    Returns
    -------
    list[str]
        List of bibcodes.

    Examples
    --------
    File format:
    ```
    2020ApJ...900..100S  # A comment
    2021MNRAS.501..123A, 2021AJ....161..147B
    ```
    """
    path = Path(file_path)
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    text += "\n"  # Ensure trailing newline

    # Remove comments (# or % to end of line)
    text = re.sub(r"\s?[#%](.)*\s?\n", "\n", text)

    # Split on commas
    text = re.sub(r"\s?,\s?", "\n", text)

    # Clean whitespace
    text = re.sub(r"\s?\n", "\n", text)
    text = text.replace(" ", "")

    # Convert to list, removing empty strings
    bibcodes = [b.strip() for b in text.split("\n") if b.strip()]

    if sort:
        bibcodes.sort()

    return bibcodes


def read_bibtex_file(file_path: str | Path) -> tuple[str, list[str]]:
    """
    Read a BibTeX file and extract citation keys.

    Parameters
    ----------
    file_path : str or Path
        Path to the BibTeX file.

    Returns
    -------
    tuple[str, list[str]]
        - Raw file content
        - List of citation keys
    """
    path = Path(file_path)
    if not path.exists():
        return "", []

    content = path.read_text(encoding="utf-8")

    # Extract citation keys from @type{key, entries
    keys = re.findall(r"@\w+\{([^,]+),", content)

    return content, keys


def write_bibtex_file(
    content: str,
    file_path: str | Path,
    append: bool = False,
) -> None:
    """
    Write BibTeX content to a file.

    Parameters
    ----------
    content : str
        BibTeX content to write.
    file_path : str or Path
        Output file path.
    append : bool, optional
        Append to existing file. Default: False.
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a" if append else "w", encoding="utf-8") as stream:
        stream.write(content)
