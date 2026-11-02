"""Partial ADS pages cannot prove a unique identifier match."""

from types import SimpleNamespace

import pytest

from adsfolio.config import ADSError
from adsfolio.pdf.identifier import PdfIdentifier


@pytest.mark.parametrize("kind", ["doi", "arxiv", "bibcode_alias"])
def test_identifier_lookup_checks_all_pages_before_choosing_a_record(kind):
    own = "10.5555/shared"
    arxiv = "2601.12345"
    alias = "2026NatAs.tmp....1A"
    documents = [
        {
            "bibcode": code,
            "doi": [own],
            "identifier": [f"arXiv:{arxiv}"],
            "alternate_bibcode": [alias],
        }
        for code in ("2026NatAs..10....1A", "2026NatAs..10....2B")
    ]
    starts = []

    def query(*, q, fl, rows, start=0):
        starts.append(start)
        return SimpleNamespace(
            num_found=2, start=start, docs=documents[start : start + 1]
        )

    identifier = PdfIdentifier(SimpleNamespace(search=SimpleNamespace(query=query)))
    if kind == "doi":
        result = identifier.query_docs_for_dois([own])
        key = own
    elif kind == "arxiv":
        result = identifier.query_docs_for_arxiv_ids([arxiv])
        key = arxiv
    else:
        result = identifier.query_docs_for_bibcodes([alias])
        key = alias

    assert result[key] is None
    assert starts == [0, 1]


def test_missing_ads_page_is_an_error_instead_of_a_unique_match():
    calls = []

    def query(*, q, fl, rows, start=0):
        calls.append(start)
        return SimpleNamespace(
            num_found=2,
            start=start,
            docs=[{"bibcode": "B1", "doi": ["10.5555/shared"]}] if start == 0 else [],
        )

    identifier = PdfIdentifier(SimpleNamespace(search=SimpleNamespace(query=query)))

    with pytest.raises(ADSError, match="incomplete"):
        identifier.query_docs_for_dois(["10.5555/shared"])
    assert calls == [0, 1]
