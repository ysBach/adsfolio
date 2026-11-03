"""Corroborate a provider DOI with the PDF title, labeled metadata DOIs, and first-page
text, and fail closed on mismatches, conflicts, and malformed input.
"""

from __future__ import annotations

import hashlib
from contextlib import closing
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from adsfolio.pdf.document_identity_evidence import (
    check_document_doi_evidence,
    corroborate_document_doi_evidence,
)


def _create_synthetic_pdf(
    *,
    title: str = "Test Title",
    subject: str = "A&A 123. DOI: 10.1051/0004-6361/202612345",
    page_text: str = "Test Title\nhttps://doi.org/10.1051/0004-6361/202612345\nAbstract",
) -> bytes:
    """Helper to generate an in-memory PDF using fitz."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    if page_text:
        page.insert_text((72, 72), page_text, fontsize=11)
    metadata = {}
    if title is not None:
        metadata["title"] = title
    if subject is not None:
        metadata["subject"] = subject
    doc.set_metadata(metadata)
    data = doc.tobytes()
    doc.close()
    return data


def _with_article_doi(pdf: bytes, doi: str) -> bytes:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(BytesIO(pdf))
    writer = PdfWriter(clone_from=reader)
    writer.add_metadata({"/WPS-ARTICLEDOI": doi})
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def test_publisher_article_doi_is_checked_against_page_and_provider() -> None:
    doi = "10.1234/example.2026"
    title = "Thermal-infrared beaming - A surface model"
    pdf = _with_article_doi(
        _create_synthetic_pdf(
            title="Thermalinfrared beaming  A surface model",
            subject=None,
            page_text=f"{title}\nDOI: {doi}",
        ),
        doi,
    )

    result = check_document_doi_evidence(pdf, {"title": [title], "doi": [doi]})

    assert result.doi == doi


def test_article_doi_conflict_is_not_ignored_when_another_doi_matches(
    tmp_path: Path,
) -> None:
    from pypdf import PdfReader, PdfWriter

    from adsfolio.pdf.identity import IdentityConflictError, assert_mutation_allowed

    good = "10.1234/example"
    bad = "10.1234/other"
    writer = PdfWriter(clone_from=PdfReader(BytesIO(_create_synthetic_pdf())))
    writer.add_metadata({"/DOI": good, "/WPS-ARTICLEDOI": bad})
    path = tmp_path / "paper.pdf"
    with path.open("wb") as output:
        writer.write(output)
    provider = {"title": ["Test Title"], "doi": [good], "bibcode": "2026Test"}

    with pytest.raises(IdentityConflictError):
        assert_mutation_allowed(path, provider, provider_document=provider)


def test_conflicting_subject_doi_is_not_ignored_when_other_fields_match(
    tmp_path: Path,
) -> None:
    from pypdf import PdfReader, PdfWriter

    from adsfolio.pdf.identity import IdentityConflictError, assert_mutation_allowed

    good = "10.1234/example"
    pdf = _create_synthetic_pdf(subject="DOI: 10.1234/other")
    writer = PdfWriter(clone_from=PdfReader(BytesIO(pdf)))
    writer.add_metadata({"/DOI": good, "/WPS-ARTICLEDOI": good})
    path = tmp_path / "paper.pdf"
    with path.open("wb") as output:
        writer.write(output)
    provider = {"title": ["Test Title"], "doi": [good], "bibcode": "2026Test"}

    with pytest.raises(IdentityConflictError):
        assert_mutation_allowed(path, provider, provider_document=provider)


def test_conflicting_subject_and_article_dois_cannot_use_provider_aliases(
    tmp_path: Path,
) -> None:
    from pypdf import PdfReader, PdfWriter

    from adsfolio.pdf.identity import IdentityConflictError, assert_mutation_allowed

    article = "10.1234/article"
    subject = "10.1234/other"
    pdf = _create_synthetic_pdf(subject=f"DOI: {subject}")
    writer = PdfWriter(clone_from=PdfReader(BytesIO(pdf)))
    writer.add_metadata({"/DOI": article, "/WPS-ARTICLEDOI": article})
    path = tmp_path / "paper.pdf"
    with path.open("wb") as output:
        writer.write(output)
    provider = {
        "title": ["Test Title"],
        "doi": [article, subject],
        "bibcode": "2026Test",
    }

    with pytest.raises(IdentityConflictError):
        assert_mutation_allowed(path, provider, provider_document=provider)


def test_conflicting_subject_and_doi_cannot_use_provider_aliases(
    tmp_path: Path,
) -> None:
    from pypdf import PdfReader, PdfWriter

    from adsfolio.pdf.identity import IdentityConflictError, assert_mutation_allowed

    pdf = _create_synthetic_pdf(subject="DOI: 10.1234/other")
    writer = PdfWriter(clone_from=PdfReader(BytesIO(pdf)))
    writer.add_metadata({"/DOI": "10.1234/article"})
    path = tmp_path / "paper.pdf"
    with path.open("wb") as output:
        writer.write(output)
    provider = {
        "title": ["Test Title"],
        "doi": ["10.1234/article", "10.1234/other"],
        "bibcode": "2026Test",
    }

    with pytest.raises(IdentityConflictError):
        assert_mutation_allowed(path, provider, provider_document=provider)


def test_malformed_arxiv_doi_in_docinfo_cannot_verify_or_be_confirmed(
    tmp_path: Path,
) -> None:
    from pypdf import PdfReader, PdfWriter

    from adsfolio.pdf.identity import UnverifiedIdentityError, assert_mutation_allowed

    writer = PdfWriter(
        clone_from=PdfReader(BytesIO(_create_synthetic_pdf(subject=None)))
    )
    writer.add_metadata({"/DOI": "10.48550/arxiv.1208.4606-extra"})
    path = tmp_path / "paper.pdf"
    with path.open("wb") as output:
        writer.write(output)
    provider = {
        "title": ["Test Title"],
        "identifier": ["arXiv:1208.4606"],
        "bibcode": "2012arXiv1208.4606R",
    }

    with pytest.raises(UnverifiedIdentityError) as error:
        assert_mutation_allowed(
            path, provider, provider_document=provider, manual_confirmation=True
        )
    assert not error.value.manually_confirmable


@pytest.mark.parametrize(
    "invalid", ["10.48550/arxiv.not-an-id", "10.48550/arxiv.1208.4606-extra"]
)
def test_invalid_article_arxiv_doi_blocks_other_matching_identifiers(
    tmp_path: Path, invalid: str
) -> None:
    from pypdf import PdfReader, PdfWriter

    from adsfolio.pdf.identity import IdentityValidationError, assert_mutation_allowed

    good = "10.1234/example"
    writer = PdfWriter(
        clone_from=PdfReader(BytesIO(_create_synthetic_pdf(subject=None)))
    )
    writer.add_metadata({"/DOI": good, "/WPS-ARTICLEDOI": invalid})
    path = tmp_path / "paper.pdf"
    with path.open("wb") as output:
        writer.write(output)
    provider = {"title": ["Test Title"], "doi": [good], "bibcode": "2026Test"}

    with pytest.raises(IdentityValidationError):
        assert_mutation_allowed(path, provider, provider_document=provider)


def test_manual_confirmation_cannot_override_invalid_article_doi(
    tmp_path: Path,
) -> None:
    from adsfolio.pdf.identity import IdentityValidationError, assert_mutation_allowed

    path = tmp_path / "paper.pdf"
    path.write_bytes(
        _with_article_doi(
            _create_synthetic_pdf(subject=None), "10.48550/arxiv.1208.4606-extra"
        )
    )

    with pytest.raises(IdentityValidationError):
        assert_mutation_allowed(path, {"title": "Test Title"}, manual_confirmation=True)


def test_article_arxiv_doi_cannot_confirm_a_different_primary_eprint(
    tmp_path: Path,
) -> None:
    from adsfolio.pdf.identity import IdentityConflictError, assert_mutation_allowed

    doi = "10.48550/arxiv.1403.7143"
    path = tmp_path / "paper.pdf"
    path.write_bytes(
        _with_article_doi(_create_synthetic_pdf(title="Test Title", subject=None), doi)
    )
    provider = {
        "bibcode": "2012arXiv1208.4606R",
        "identifier": ["arXiv:1208.4606", "arXiv:1403.7143"],
        "doi": [doi],
        "title": ["Test Title"],
    }

    with pytest.raises(IdentityConflictError):
        assert_mutation_allowed(path, provider, provider_document=provider)


def test_article_doi_requires_corroboration_even_with_manual_confirmation(
    tmp_path: Path,
) -> None:
    from adsfolio.pdf.identity import UnverifiedIdentityError, assert_mutation_allowed

    doi = "10.1234/example"
    path = tmp_path / "paper.pdf"
    path.write_bytes(
        _with_article_doi(
            _create_synthetic_pdf(
                title="Test Title", subject=None, page_text="Different page"
            ),
            doi,
        )
    )
    provider = {"bibcode": "2026Test", "doi": [doi], "title": ["Test Title"]}

    with pytest.raises(UnverifiedIdentityError) as error:
        assert_mutation_allowed(
            path, provider, provider_document=provider, manual_confirmation=True
        )
    assert not error.value.manually_confirmable


@pytest.mark.parametrize(
    "pdf_title,page_text,subject,article_doi,provider_title,provider_doi",
    [
        (
            "Wrong paper",
            "Right paper\n10.1234/example",
            None,
            "10.1234/example",
            "Right paper",
            "10.1234/example",
        ),
        (
            "Right paper",
            "Wrong paper\n10.1234/example",
            None,
            "10.1234/example",
            "Right paper",
            "10.1234/example",
        ),
        (
            "Right paper",
            "Right paper\n10.1234/example.extra",
            None,
            "10.1234/example",
            "Right paper",
            "10.1234/example",
        ),
        (
            "Right paper",
            "Right paper\n10.1234/example",
            None,
            "10.1234/example",
            "Right paper",
            "10.1234/other",
        ),
        (
            "Right paper",
            "Right paper\n10.1234/example",
            "DOI: 10.1234/other",
            "10.1234/example",
            "Right paper",
            "10.1234/example",
        ),
        (
            "Right paper",
            "Right paper\n10.1234/example",
            None,
            "10.1234/example another",
            "Right paper",
            "10.1234/example",
        ),
    ],
)
def test_publisher_article_doi_rejects_incomplete_or_conflicting_evidence(
    pdf_title: str,
    page_text: str,
    subject: str | None,
    article_doi: str,
    provider_title: str,
    provider_doi: str,
) -> None:
    pdf = _with_article_doi(
        _create_synthetic_pdf(title=pdf_title, subject=subject, page_text=page_text),
        article_doi,
    )
    result = check_document_doi_evidence(
        pdf, {"title": [provider_title], "doi": [provider_doi]}
    )
    assert result.doi is None


def test_synthetic_pdf_full_mutual_corroboration() -> None:
    pdf_bytes = _create_synthetic_pdf(
        title="  A Study on Asteroids  ",
        subject="Journal XYZ. DOI: 10.1051/0004-6361/202611111",
        page_text="A Study on Asteroids\nDOI: 10.1051/0004-6361/202611111\nAbstract",
    )
    provider_doc = {
        "title": ["A study ON asteroids"],
        "doi": ["10.1051/0004-6361/202611111"],
    }
    res = corroborate_document_doi_evidence(pdf_bytes, provider_doc)
    assert res == "10.1051/0004-6361/202611111"


@pytest.mark.parametrize("separator", [" - ", " – ", " — "])
def test_quote_typography_and_subtitle_separator_do_not_reject_matching_doi(
    separator: str,
) -> None:
    pdf_bytes = _create_synthetic_pdf(
        title=f"Ice grains show ‘dust’ release{separator}A model for activity"
    )
    provider = {
        "title": ["Ice grains show 'dust' release: A model for activity"],
        "doi": ["10.1051/0004-6361/202612345"],
    }

    assert corroborate_document_doi_evidence(pdf_bytes, provider) == provider["doi"][0]


def test_ads_astroobj_title_matches_the_document_title() -> None:
    title = (
        "65 Cybele in the thermal infrared: Multiple observations and "
        "thermophysical analysis"
    )
    provider_title = (
        "<ASTROBJ>65 Cybele</ASTROBJ> in the thermal infrared: "
        "Multiple observations and thermophysical analysis"
    )
    provider = {
        "title": [provider_title],
        "doi": ["10.1051/0004-6361/202612345"],
        "bibcode": "example-bibcode",
    }

    result = check_document_doi_evidence(_create_synthetic_pdf(title=title), provider)

    assert result.doi == provider["doi"][0]
    assert result.reason == ""


def test_ads_astroobj_title_identifies_and_renames_unchanged_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adsfolio.config import Config
    from adsfolio.pdf.identifier import PdfIdentifier
    from adsfolio.pdf.identify_settings import IdentifySettings
    from adsfolio.pdf.identity import IDENTITY_KEY
    from adsfolio.pdf.registry import open_registry_state
    from adsfolio.pdf.rename_settings import RenameSettings
    from adsfolio.pdf.renamer import PdfRenamer
    from adsfolio.pdf.watch_session import ArrivalIdentity, WatchEvent, WatchState
    from adsfolio.pdf.watcher import PdfWatcher

    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(
        "adsfolio.pubcache.cache_path", lambda: tmp_path / "cache" / "publications.json"
    )
    monkeypatch.setattr("adsfolio.pdf.identifier._fetch_external_doi", lambda *_: None)
    doi = "10.1051/0004-6361/202612345"
    bibcode = "2026A&A...712A.162B"
    title = (
        "65 Cybele in the thermal infrared: Multiple observations and "
        "thermophysical analysis"
    )
    provider_title = (
        "<ASTROBJ>65 Cybele</ASTROBJ> in the thermal infrared: "
        "Multiple observations and thermophysical analysis"
    )
    provider = {
        "title": [provider_title],
        "doi": [doi],
        "bibcode": bibcode,
        "author": ["Example, A."],
        "year": "2026",
    }
    queries: list[str] = []

    def query(**kwargs: object) -> SimpleNamespace:
        queries.append(str(kwargs["q"]))
        return SimpleNamespace(docs=[provider], num_found=1)

    client = SimpleNamespace(search=SimpleNamespace(query=query))
    root = tmp_path / "library"
    root.mkdir()
    pdf = root / "download.pdf"
    original = _create_synthetic_pdf(title=title, page_text=f"{title}\nDOI: {doi}")
    pdf.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    stat = pdf.stat()
    event = WatchEvent(
        root=root,
        session_id="display-markup",
        identity=ArrivalIdentity(stat.st_dev, stat.st_ino, None, 0),
        path=pdf,
        state=WatchState.DETECTED,
    )

    with closing(open_registry_state(root)) as state:
        outcome = PdfWatcher(PdfIdentifier(client), PdfRenamer()).process_event(
            event, state, RenameSettings(template="{bibcode}"), IdentifySettings()
        )
        assert queries
        assert outcome.event.state is WatchState.RENAMED, outcome.message
        assert outcome.destination is not None
        assert outcome.destination.name == f"{bibcode}.pdf"
        assert hashlib.sha256(outcome.destination.read_bytes()).hexdigest() == digest
        saved = state.registry.find_file_by_current_path(root, outcome.destination.name)
        assert saved is not None
        assert saved["bibcode"] == bibcode
        assert saved["sha256"] == digest
        assert saved["metadata"][IDENTITY_KEY]["status"] == "verified"


@pytest.mark.parametrize(
    "pdf_title,provider_title",
    [
        ("Dust and ice", "<b>Dust</b> and <i>ice</i>"),
        ("Dust and ice", "<STRONG>Dust <em>and</em> ice</STRONG>"),
        ("Dust and ice", "<span>Dust</span> and ice"),
        ("The 2001 CC21 target", "The 2001 CC<SUB>21</SUB> target"),
        ("CO+ emission", "CO<SUP>+</SUP> emission"),
        ("Dust & ice", "Dust &amp; ice"),
        ("Dust & ice", "Dust &#38; ice"),
        ("Dust & ice", "Dust &#x26; ice"),
        ("α Centauri & ice", "&#x3B1; Centauri &amp; ice"),
        ("A <Fe> spectrum", "A &lt;Fe&gt; spectrum"),
        ("A <Fe> spectrum", "<b>A</b> <Fe> spectrum"),
        ("A <Fe> & dust spectrum", "<i>A &lt;Fe&gt;</i> &amp; dust spectrum"),
        ("Dust and ice", "Dust&nbsp;and ice"),
    ],
)
def test_provider_display_markup_and_entities_preserve_title_content(
    pdf_title: str, provider_title: str
) -> None:
    provider = {"title": [provider_title], "doi": ["10.1051/0004-6361/202612345"]}

    assert (
        corroborate_document_doi_evidence(
            _create_synthetic_pdf(title=pdf_title), provider
        )
        == provider["doi"][0]
    )


@pytest.mark.parametrize(
    "pdf_title,provider_title",
    [
        ("65 Cybele observations", "<ASTROBJ>66 Cybele</ASTROBJ> observations"),
        ("Dust & ice", "<b>Dust</b> &amp; gas"),
        ("CO emission", "CO<SUP>+</SUP> emission"),
        ("H2O emission", "H<SUB>3</SUB>O emission"),
        ("x² emission", "x<SUP>2</SUP> emission"),
        ("x < 3 emission", "x &lt; 5 emission"),
        ("x 3 dust", "x < 3 <i>dust</i>"),
        ("C emission", "C<O> emission"),
        ("Mean flux", "Mean <flux>"),
        ("Dust and ice", "<unknown>Dust and ice</unknown>"),
        ("Dust and ice", "<i>Dust</i> and <unknown>ice</unknown>"),
        ("Dust and ice", "<i>Dust and ice"),
        ("Dust and ice", "Dust and ice</i>"),
        ("Dust and ice", "<i><b>Dust and ice</i></b>"),
        ("Dust and ice", '<i class="name">Dust and ice</i>'),
        ("Dust and ice", "<i/>Dust and ice"),
        ("Dust & ice", "Dust &amp ice"),
        ("Dust ¬AnEntity; ice", "Dust &notAnEntity; ice"),
        ("Dust � ice", "Dust &#0; ice"),
        ("Dust ice", "Dust &#xFDD0; ice"),
        ("Dust <ice>", "Dust &amp;lt;ice&amp;gt;"),
    ],
)
def test_provider_markup_does_not_hide_title_mismatches(
    pdf_title: str, provider_title: str
) -> None:
    provider = {"title": [provider_title], "doi": ["10.1051/0004-6361/202612345"]}

    result = check_document_doi_evidence(
        _create_synthetic_pdf(title=pdf_title), provider
    )

    assert result.doi is None
    assert "document titles differ" in result.reason


def test_literal_matched_tag_notation_remains_comparable() -> None:
    title = "A literal <i>2</i> notation"
    provider = {"title": [title], "doi": ["10.1051/0004-6361/202612345"]}

    assert (
        corroborate_document_doi_evidence(_create_synthetic_pdf(title=title), provider)
        == provider["doi"][0]
    )


@pytest.mark.parametrize(
    "subject,page_text,provider_doi",
    [
        (
            "No labeled DOI",
            "10.1051/0004-6361/202612345",
            "10.1051/0004-6361/202612345",
        ),
        (
            "DOI: 10.1051/0004-6361/202612345 and DOI: 10.1051/0004-6361/202600000",
            "10.1051/0004-6361/202612345",
            "10.1051/0004-6361/202612345",
        ),
        ("DOI: 10.1051/0004-6361/202612345", "No DOI", "10.1051/0004-6361/202612345"),
        (
            "DOI: 10.1051/0004-6361/202612345",
            "10.1051/0004-6361/202612345.extra",
            "10.1051/0004-6361/202612345",
        ),
        (
            "DOI: 10.1051/0004-6361/202612345",
            "10.1051/0004-6361/202612345",
            "10.1051/0004-6361/202600000",
        ),
    ],
)
def test_display_markup_does_not_bypass_doi_checks(
    subject: str, page_text: str, provider_doi: str
) -> None:
    data = _create_synthetic_pdf(
        title="Dust and ice", subject=subject, page_text=page_text
    )
    provider = {"title": ["<i>Dust</i> and ice"], "doi": [provider_doi]}

    assert corroborate_document_doi_evidence(data, provider) is None


@pytest.mark.parametrize(
    "pdf_title,provider_title",
    [
        ("A+B emission", "A-B emission"),
        ("A-B emission", "A B emission"),
        ("CO+ emission", "CO emission"),
        ("1.2 mm grains", "12 mm grains"),
        ("H₂O grains", "H2O grains"),
        ("A/B emission", "A B emission"),
        ("Model x - y", "Model x + y"),
    ],
)
def test_title_normalization_preserves_words_numbers_and_other_symbols(
    pdf_title: str, provider_title: str
) -> None:
    data = _create_synthetic_pdf(title=pdf_title)
    provider = {"title": [provider_title], "doi": ["10.1051/0004-6361/202612345"]}

    assert corroborate_document_doi_evidence(data, provider) is None


def test_diagnostic_names_title_mismatch_after_doi_matches() -> None:
    provider = {"title": ["Another Title"], "doi": ["10.1051/0004-6361/202612345"]}

    result = check_document_doi_evidence(_create_synthetic_pdf(), provider)

    assert result.doi is None
    assert "10.1051/0004-6361/202612345" in result.reason
    assert "document titles differ" in result.reason
    assert "no readable identifier" not in result.reason


def test_refuses_when_provider_title_mismatches() -> None:
    pdf_bytes = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject="Journal XYZ. DOI: 10.1051/0004-6361/202611111",
        page_text="A Study on Asteroids\nDOI: 10.1051/0004-6361/202611111\nAbstract",
    )
    provider_doc = {
        "title": ["Completely Different Title Here"],
        "doi": ["10.1051/0004-6361/202611111"],
    }
    assert corroborate_document_doi_evidence(pdf_bytes, provider_doc) is None


def test_refuses_when_provider_doi_mismatches() -> None:
    pdf_bytes = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject="Journal XYZ. DOI: 10.1051/0004-6361/202611111",
        page_text="A Study on Asteroids\nDOI: 10.1051/0004-6361/202611111\nAbstract",
    )
    provider_doc = {
        "title": ["A Study on Asteroids"],
        "doi": ["10.1051/0004-6361/999999999"],
    }
    assert corroborate_document_doi_evidence(pdf_bytes, provider_doc) is None


def test_refuses_when_first_page_has_doi_prefix_collision() -> None:
    # Target DOI is 10.1051/abc, but page 1 only contains longer variants
    target_doi = "10.1051/0004-6361/202611111"
    provider_doc = {
        "title": ["A Study on Asteroids"],
        "doi": [target_doi],
    }
    # Test collision with alphanumeric extension
    pdf_bytes_alnum = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject=f"Journal XYZ. DOI: {target_doi}",
        page_text=f"A Study on Asteroids\nDOI: {target_doi}222\nAbstract",
    )
    assert corroborate_document_doi_evidence(pdf_bytes_alnum, provider_doc) is None

    # Test collision with dot extension: 10.1000/x.extra
    pdf_bytes_dot = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject=f"Journal XYZ. DOI: {target_doi}",
        page_text=f"A Study on Asteroids\nDOI: {target_doi}.extra\nAbstract",
    )
    assert corroborate_document_doi_evidence(pdf_bytes_dot, provider_doc) is None

    # Test collision with colon extension: 10.1000/x:extra
    pdf_bytes_colon = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject=f"Journal XYZ. DOI: {target_doi}",
        page_text=f"A Study on Asteroids\nDOI: {target_doi}:extra\nAbstract",
    )
    assert corroborate_document_doi_evidence(pdf_bytes_colon, provider_doc) is None

    # Test collision with slash extension: 10.1000/x/extra
    pdf_bytes_slash = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject=f"Journal XYZ. DOI: {target_doi}",
        page_text=f"A Study on Asteroids\nDOI: {target_doi}/extra\nAbstract",
    )
    assert corroborate_document_doi_evidence(pdf_bytes_slash, provider_doc) is None


def test_preserves_terminal_punctuation_in_doi_without_heuristic_trimming() -> None:
    # DOI legitimately ending with terminal period: 10.1234/test.
    doi_with_dot = "10.1234/test."
    pdf_bytes = _create_synthetic_pdf(
        title="Punctuation Test",
        subject=f"Journal. DOI: {doi_with_dot}",
        page_text=f"Punctuation Test\nDOI: {doi_with_dot}\nAbstract",
    )
    provider_doc_with_dot = {
        "title": ["Punctuation Test"],
        "doi": [doi_with_dot],
    }
    # Exact dotted DOI corroborates when provider and PDF agree
    res = corroborate_document_doi_evidence(pdf_bytes, provider_doc_with_dot)
    assert res == doi_with_dot

    # If provider expects DOI without the dot, the helper must FAIL CLOSED
    # rather than heuristic auto-trimming the period
    provider_doc_without_dot = {
        "title": ["Punctuation Test"],
        "doi": ["10.1234/test"],
    }
    assert (
        corroborate_document_doi_evidence(pdf_bytes, provider_doc_without_dot) is None
    )


def test_refuses_when_subject_doi_missing_from_first_page_text() -> None:
    pdf_bytes = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject="Journal XYZ. DOI: 10.1051/0004-6361/202611111",
        page_text="A Study on Asteroids\nNo DOI printed here\nAbstract",
    )
    provider_doc = {
        "title": ["A Study on Asteroids"],
        "doi": ["10.1051/0004-6361/202611111"],
    }
    assert corroborate_document_doi_evidence(pdf_bytes, provider_doc) is None


def test_refuses_when_subject_has_multiple_conflicting_dois() -> None:
    pdf_bytes = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject="DOI: 10.1051/0004-6361/202611111 and DOI: 10.1051/0004-6361/202622222",
        page_text="A Study on Asteroids\n10.1051/0004-6361/202611111",
    )
    provider_doc = {
        "title": ["A Study on Asteroids"],
        "doi": ["10.1051/0004-6361/202611111"],
    }
    assert corroborate_document_doi_evidence(pdf_bytes, provider_doc) is None


def test_refuses_when_subject_doi_not_explicitly_labeled() -> None:
    pdf_bytes = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject="Journal XYZ 10.1051/0004-6361/202611111",
        page_text="A Study on Asteroids\n10.1051/0004-6361/202611111",
    )
    provider_doc = {
        "title": ["A Study on Asteroids"],
        "doi": ["10.1051/0004-6361/202611111"],
    }
    assert corroborate_document_doi_evidence(pdf_bytes, provider_doc) is None


def test_first_page_doi_alone_without_matching_subject_does_not_corroborate() -> None:
    pdf_bytes = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject="Solar System Research",
        page_text="A Study on Asteroids\nReferences:\nSmith et al. 10.1051/0004-6361/202611111",
    )
    provider_doc = {
        "title": ["A Study on Asteroids"],
        "doi": ["10.1051/0004-6361/202611111"],
    }
    assert corroborate_document_doi_evidence(pdf_bytes, provider_doc) is None


def test_malformed_and_empty_inputs_fail_closed() -> None:
    provider_doc = {"title": ["Test"], "doi": ["10.1051/test"]}
    assert corroborate_document_doi_evidence(b"", provider_doc) is None
    assert corroborate_document_doi_evidence(b"not a pdf", provider_doc) is None
    assert (
        corroborate_document_doi_evidence(b"%PDF-1.4\ntruncated", provider_doc) is None
    )
    assert corroborate_document_doi_evidence(b"\x00" * 50, provider_doc) is None
    assert corroborate_document_doi_evidence("not bytes", provider_doc) is None  # type: ignore[arg-type]
    assert corroborate_document_doi_evidence(b"%PDF-1.4...", None) is None  # type: ignore[arg-type]
