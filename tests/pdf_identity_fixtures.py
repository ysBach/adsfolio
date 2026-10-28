"""PDF fixtures with explicit document identifiers or recorded manual consent."""

from pathlib import Path
from typing import Any

from pypdf import PdfWriter

from adsfolio.pdf.identity import IDENTITY_KEY, assert_mutation_allowed


def write_pdf(path: Path, *, doi: str = "", arxiv: str = "") -> bytes:
    """Write a readable one-page PDF and return its bytes."""
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    fields = {}
    if doi:
        fields["/DOI"] = doi
    if arxiv:
        fields["/arXiv"] = arxiv
    if fields:
        writer.add_metadata(fields)
    writer.write(path)
    return path.read_bytes()


def confirm_metadata(path: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    """Record explicit manual consent for a PDF without its own identifier."""
    result = dict(metadata)
    result[IDENTITY_KEY] = assert_mutation_allowed(
        path, result, manual_confirmation=True
    )
    return result
