"""Resolve current and former bibcodes from complete ADS search results."""

from types import SimpleNamespace

import pytest

from adsfolio.config import ADSError, RequestTooLongError
from adsfolio.pdf.identifier import PdfIdentifier


class Search:
    """Return controlled ADS pages without making a network request."""

    def __init__(self, documents, *, page_size=None, fail_on_start=None):
        self.documents = documents
        self.page_size = page_size
        self.fail_on_start = fail_on_start
        self.calls = []

    def query(self, *, q, fl, rows, start=0):
        self.calls.append({"q": q, "fl": fl, "rows": rows, "start": start})
        if start == self.fail_on_start:
            raise ADSError("ADS unavailable")
        size = self.page_size or rows
        return SimpleNamespace(
            num_found=len(self.documents),
            docs=self.documents[start : start + size],
            start=start,
        )


def identifier(search):
    return PdfIdentifier(SimpleNamespace(search=search))


def test_queries_all_bibcode_fields_in_one_batch():
    current = "2023ApJ...900..101A"
    old_alternate = "2023ApJ...tmp...2B"
    old_identifier = "2023MNRAS.tmp...3C"
    direct = {"bibcode": current}
    alternate = {"bibcode": "2023ApJ...900..102B", "alternate_bibcode": [old_alternate]}
    identifier_alias = {
        "bibcode": "2023MNRAS.500..103C",
        "identifier": [old_identifier],
    }
    search = Search([direct, alternate, identifier_alias])
    callbacks = []
    progress = []

    result = identifier(search).query_docs_for_bibcodes(
        [current, old_alternate, old_identifier, "missing", current.lower()],
        ads_documents_callback=callbacks.append,
        progress_callback=lambda done, total: progress.append((done, total)),
    )

    assert result == {
        current: direct,
        old_alternate: alternate,
        old_identifier: identifier_alias,
        "missing": None,
    }
    assert len(search.calls) == 1
    for value in (current, old_alternate, old_identifier, "missing"):
        for field in ("bibcode", "alternate_bibcode", "identifier"):
            assert f'{field}:"{value}"' in search.calls[0]["q"]
    assert callbacks == [[direct, alternate, identifier_alias]]
    assert progress == [(0, 1), (1, 1)]


@pytest.mark.parametrize("field", ["alternate_bibcode", "identifier"])
def test_former_bibcode_is_found_only_by_its_indexed_alias(field):
    wanted = "2023ApJ...tmp...2B"
    document = {"bibcode": "2023ApJ...900..102B", field: [wanted]}
    queries = []

    def query(*, q, fl, rows, start=0):
        queries.append(q)
        documents = [document] if f'{field}:"{wanted}"' in q.split(" OR ") else []
        return SimpleNamespace(num_found=len(documents), docs=documents, start=start)

    client = SimpleNamespace(search=SimpleNamespace(query=query))
    result = PdfIdentifier(client).query_docs_for_bibcodes([wanted])

    assert result[wanted] == document
    assert len(queries) == 1


@pytest.mark.parametrize("field", ["alternate_bibcode", "identifier"])
def test_unrelated_returned_alias_does_not_match(field):
    wanted = "2023ApJ...tmp...2B"
    unrelated = {"bibcode": "2023ApJ...900..101A", field: ["2023ApJ...tmp...7X"]}
    assert (
        identifier(Search([unrelated])).query_docs_for_bibcodes([wanted])[wanted]
        is None
    )


@pytest.mark.parametrize("field", ["alternate_bibcode", "identifier"])
def test_candidate_without_primary_bibcode_does_not_resolve(field):
    wanted = "2023ApJ...tmp...2B"
    assert (
        identifier(Search([{field: [wanted]}])).query_docs_for_bibcodes([wanted])[
            wanted
        ]
        is None
    )


@pytest.mark.parametrize("primary", [None, "", " ", [None], 123])
def test_malformed_primary_bibcode_does_not_resolve(primary):
    wanted = "2023ApJ...tmp...2B"
    document = {"bibcode": primary, "alternate_bibcode": [wanted]}
    assert (
        identifier(Search([document])).query_docs_for_bibcodes([wanted])[wanted] is None
    )


@pytest.mark.parametrize("field", ["bibcode", "alternate_bibcode", "identifier"])
def test_matches_are_case_insensitive(field):
    wanted = "2023ApJ...tmp...2B"
    document = {"bibcode": "2023ApJ...900..102B", field: wanted.upper()}
    result = identifier(Search([document])).query_docs_for_bibcodes(
        [wanted, wanted.lower()]
    )
    assert result == {wanted: document}


def test_primary_and_competing_alias_are_ambiguous_across_pages():
    wanted = "2023ApJ...tmp...2B"
    documents = [
        {"bibcode": wanted},
        {"bibcode": "2023ApJ...900..102B", "alternate_bibcode": [wanted]},
    ]
    search = Search(documents, page_size=1)
    callbacks = []

    result = identifier(search).query_docs_for_bibcodes(
        [wanted], ads_documents_callback=callbacks.append
    )

    assert result[wanted] is None
    assert [call["start"] for call in search.calls] == [0, 1]
    assert callbacks == [documents]


def test_two_different_alias_owners_are_ambiguous():
    wanted = "2023ApJ...tmp...2B"
    documents = [
        {"bibcode": "2023ApJ...900..101A", "identifier": [wanted]},
        {"bibcode": "2023ApJ...900..102B", "alternate_bibcode": [wanted]},
    ]
    assert (
        identifier(Search(documents)).query_docs_for_bibcodes([wanted])[wanted] is None
    )


@pytest.mark.parametrize(
    ("alias", "matches"),
    [
        ("arXiv:2301.12345v2", True),
        ("unrelated text arXiv:2301.12345 extra", False),
        ("arXiv:2301.99999", False),
    ],
)
def test_arxiv_alias_requires_a_complete_matching_identifier(alias, matches):
    wanted = "2023arXiv230112345A"
    document = {"bibcode": "2023ApJ...900..101A", "identifier": [alias]}
    result = identifier(Search([document])).query_docs_for_bibcodes([wanted])
    assert result[wanted] == (document if matches else None)


def test_duplicate_primary_records_are_not_treated_as_a_unique_candidate():
    wanted = "2023ApJ...tmp...2B"
    documents = [
        {"bibcode": "2023ApJ...900..101A", "identifier": [wanted]},
        {"bibcode": "2023apj...900..101a", "alternate_bibcode": [wanted]},
    ]
    with pytest.raises(ADSError, match="duplicate"):
        identifier(Search(documents)).query_docs_for_bibcodes([wanted])


def test_incomplete_pagination_propagates_error_before_callback():
    wanted = "2023ApJ...tmp...2B"
    documents = [
        {"bibcode": "2023ApJ...900..101A", "identifier": [wanted]},
        {"bibcode": "2023ApJ...900..102B", "identifier": [wanted]},
    ]
    search = Search(documents, page_size=1, fail_on_start=1)
    callbacks = []
    with pytest.raises(ADSError, match="ADS unavailable"):
        identifier(search).query_docs_for_bibcodes(
            [wanted], ads_documents_callback=callbacks.append
        )
    assert callbacks == []


def test_nonzero_first_page_is_rejected_before_callback():
    wanted = "2023ApJ...tmp...2B"
    document = {"bibcode": wanted}
    search = SimpleNamespace(
        query=lambda **kwargs: SimpleNamespace(num_found=1, docs=[document], start=1)
    )
    callbacks = []
    with pytest.raises(ADSError, match="first identifier page"):
        identifier(search).query_docs_for_bibcodes(
            [wanted], ads_documents_callback=callbacks.append
        )
    assert callbacks == []


def test_ads_request_error_propagates_without_callback():
    error = RequestTooLongError("request too long")

    def fail(**kwargs):
        raise error

    callbacks = []
    with pytest.raises(RequestTooLongError) as caught:
        identifier(SimpleNamespace(query=fail)).query_docs_for_bibcodes(
            ["2023ApJ...tmp...2B"], ads_documents_callback=callbacks.append
        )
    assert caught.value is error
    assert callbacks == []


@pytest.mark.parametrize("kind", ["bibcode", "doi", "arxiv"])
def test_identifier_batches_split_after_ads_rejects_a_long_request(kind):
    """Every identifier resolves after ADS rejects a combined request."""
    identifiers = {
        "bibcode": ["2023ApJ...tmp...1A", "2023ApJ...tmp...2B", "2023ApJ...tmp...3C"],
        "doi": ["10.5555/example1", "10.5555/example2", "10.5555/example3"],
        "arxiv": ["2301.10001", "2301.10002", "2301.10003"],
    }[kind]
    documents = []
    for index, value in enumerate(identifiers):
        document = {"bibcode": f"2023ApJ...900..10{index}A"}
        document[
            {"bibcode": "alternate_bibcode", "doi": "doi", "arxiv": "identifier"}[kind]
        ] = [f"arXiv:{value}" if kind == "arxiv" else value]
        documents.append(document)

    calls = []

    def query(*, q, fl, rows, start=0):
        calls.append(q)
        requested = [value for value in identifiers if value in q]
        if len(requested) > 1:
            raise RequestTooLongError("request too long")
        matches = [documents[identifiers.index(value)] for value in requested]
        return SimpleNamespace(num_found=len(matches), docs=matches, start=start)

    candidate = identifier(SimpleNamespace(query=query))
    resolver = {
        "bibcode": candidate.query_docs_for_bibcodes,
        "doi": candidate.query_docs_for_dois,
        "arxiv": candidate.query_docs_for_arxiv_ids,
    }[kind]
    result = resolver(identifiers)

    assert result == dict(zip(identifiers, documents, strict=True))
    assert len(calls) == 5


def test_callback_error_propagates():
    error = RuntimeError("cache unavailable")

    def fail(documents):
        raise error

    with pytest.raises(RuntimeError) as caught:
        identifier(Search([])).query_docs_for_bibcodes(
            ["2023ApJ...tmp...2B"], ads_documents_callback=fail
        )
    assert caught.value is error


def test_batches_and_progress_use_existing_identifier_limit():
    codes = [f"bibcode-{index}" for index in range(PdfIdentifier.MAX_BATCH_TERMS + 1)]
    search = Search([])
    progress = []
    callbacks = []

    result = identifier(search).query_docs_for_bibcodes(
        codes,
        progress_callback=lambda done, total: progress.append((done, total)),
        ads_documents_callback=callbacks.append,
    )

    assert result == dict.fromkeys(codes)
    assert len(search.calls) == 2
    assert progress == [(0, 2), (1, 2), (2, 2)]
    assert callbacks == [[], []]


def test_query_escapes_special_characters():
    wanted = 'quoted"bibcode\\value'
    document = {"bibcode": wanted}
    search = Search([document])
    assert identifier(search).query_docs_for_bibcodes([wanted])[wanted] == document
    for field in ("bibcode", "alternate_bibcode", "identifier"):
        assert f'{field}:"quoted\\"bibcode\\\\value"' in search.calls[0]["q"]
